"""Calibrate the confidence tiers against the dev answer keys (decision 7: 'high' should be right ~99% of the time).

Joins a run's exported evidence (fields.json, written by the backend, no answers in it) with the official scorer's
per-field results (scores.json), then:
  1. searches a small grid of knobs (OCR confidence, sharpness, brightness thresholds) for the setting that rates the
     most fields 'high' while 'high' stays >= 99% right with a Wilson 95% lower bound >= 0.97;
  2. checks that choice by group k-fold cross-validation, grouping documents by base invoice (the 60 photos/scans
     are only 6 invoices, so their copies must never sit on both sides of a fold);
  3. writes the chosen knobs and the observed accuracy per route and tier to the backend's calibration.json, which
     the backend reads as each field's numeric confidence.

The tier rules themselves are the backend's (`invoice_analyst.extraction.confidence`, pure Python), imported here so
bench and backend can never disagree about what a tier means.
"""

from __future__ import annotations

import datetime as dt
import importlib
import itertools
import json
import math
import random
import sys
from collections import defaultdict
from typing import Any

from . import specs
from .score import run_dir

HEADER_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "due_date", "currency", "subtotal", "tax", "total")
GRID = {"ocr_conf_min": (0.80, 0.85, 0.90, 0.95, 0.98), "sharpness_min": (0.0, 50.0, 100.0, 200.0, 400.0),
        "brightness_min": (0.0, 60.0, 80.0, 100.0)}
TARGET_ACCURACY, TARGET_WILSON = 0.99, 0.97
CALIBRATION_PATH = specs.ROOT / "backend" / "src" / "invoice_analyst" / "extraction" / "calibration.json"


def confidence_module():
    src = str(specs.ROOT / "backend" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    return importlib.import_module("invoice_analyst.extraction.confidence")


def wilson_lb(right: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = right / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def observations(run: str) -> list[dict[str, Any]]:
    """One observation per scored field: its evidence (one signal set, or one per line for line items) and
    whether the scorer marked it right."""
    out_dir = run_dir(run)
    fields = json.loads((out_dir / "fields.json").read_text())
    scores = {d["doc_id"]: d for d in json.loads((out_dir / "scores.json").read_text())["documents"]}
    manifest = {e["doc_id"]: e for e in json.loads((specs.DEV_CORPUS / "manifest.json").read_text())["documents"]}
    obs = []
    for doc_id, d in fields.items():
        if not d["invoices"] or doc_id not in scores:
            continue
        inv = d["invoices"][0]  # the scorer scores the first invoice in a file
        header = {ev["field"]: ev for ev in inv if ev["line_no"] is None}
        lines = [ev["signals"] for ev in inv if ev["line_no"] is not None]
        scored = scores[doc_id]["fields"]
        group = manifest[doc_id].get("base_id") or doc_id
        for name in HEADER_FIELDS:
            if name in scored and name in header:
                obs.append({"doc_id": doc_id, "group": group, "route": d["route"], "field": name,
                            "signals": [header[name]["signals"]], "correct": scored[name]["correct"]})
        if "line_items" in scored and lines:
            obs.append({"doc_id": doc_id, "group": group, "route": d["route"], "field": "line_items",
                        "signals": lines, "correct": scored["line_items"]["correct"]})
    return obs


def tier(ob: dict, knobs: dict, conf) -> str:
    return conf.lowest([conf.assess(s, knobs)[0] for s in ob["signals"]])


def tally(obs: list[dict], knobs: dict, conf) -> dict[tuple[str, str], list[int]]:
    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for ob in obs:
        t = tier(ob, knobs, conf)
        for key in ((ob["route"], t), ("all", t)):
            counts[key][0] += 1
            counts[key][1] += ob["correct"]
    return counts


def _combos():
    names = list(GRID)
    for values in itertools.product(*(GRID[n] for n in names)):
        yield dict(zip(names, values, strict=True))


def choose(obs: list[dict], conf) -> dict:
    """The knobs rating the most fields 'high' while 'high' meets the accuracy target (else the most accurate)."""
    best, best_key = None, None
    for knobs in _combos():
        n, right = tally(obs, knobs, conf)[("all", "high")]
        meets = n > 0 and right / n >= TARGET_ACCURACY and wilson_lb(right, n) >= TARGET_WILSON
        key = (meets, n if meets else right / n if n else 0.0, -knobs["ocr_conf_min"])
        if best_key is None or key > best_key:
            best, best_key = knobs, key
    return best  # type: ignore[return-value]


def cross_validate(obs: list[dict], conf, folds: int = 5) -> dict[str, Any]:
    groups = sorted({ob["group"] for ob in obs})
    random.Random(0).shuffle(groups)
    fold_of = {g: i % folds for i, g in enumerate(groups)}
    n = right = total = 0
    for k in range(folds):
        train = [ob for ob in obs if fold_of[ob["group"]] != k]
        test = [ob for ob in obs if fold_of[ob["group"]] == k]
        knobs = choose(train, conf)
        hn, hr = tally(test, knobs, conf)[("all", "high")]
        n, right, total = n + hn, right + hr, total + len(test)
    return {"folds": folds, "high_n": n, "high_right": right, "high_accuracy": round(right / n, 4) if n else None,
            "high_wilson_lb": round(wilson_lb(right, n), 4), "high_share": round(n / total, 3) if total else None}


def box_coverage(run: str) -> dict[str, dict[str, Any]]:
    """Share of printed values that got a box on the page, per route."""
    fields = json.loads((run_dir(run) / "fields.json").read_text())
    cov: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for d in fields.values():
        for inv in d["invoices"]:
            for ev in inv:
                if ev["signals"]["printed"] and ev["grounding"] != "none":
                    cov[d["route"]][0] += 1
                    cov[d["route"]][1] += ev["grounding"] in ("exact", "fuzzy")
    return {route: {"printed": n, "located": k, "share": round(k / n, 3)} for route, (n, k) in sorted(cov.items())}


def calibrate(run: str, write: bool = True, use_defaults: bool = False) -> str:
    """use_defaults: keep the backend's default knobs (decision 26) and only measure tier accuracy under them;
    the grid search's choice is still reported for reference."""
    conf = confidence_module()
    obs = observations(run)
    tuned = choose(obs, conf)
    knobs = dict(conf.DEFAULT_KNOBS) if use_defaults else tuned
    counts = tally(obs, knobs, conf)
    cv = cross_validate(obs, conf)
    boxes = box_coverage(run)
    accuracy = defaultdict(dict)
    for (route, t), (n, right) in sorted(counts.items()):
        accuracy[route][t] = {"n": n, "right": right, "accuracy": round(right / n, 4),
                              "wilson_lb": round(wilson_lb(right, n), 4)}
    wrong_high = sorted(f"{ob['doc_id']}: {ob['field']}" for ob in obs
                        if not ob["correct"] and tier(ob, knobs, conf) == "high")
    if write:
        CALIBRATION_PATH.write_text(json.dumps({
            "knobs": knobs, "knobs_source": "defaults (decision 26)" if use_defaults else "grid search",
            "tuned_knobs": tuned, "accuracy": accuracy, "cross_validation": cv, "boxes": boxes,
            "source": {"run": run_dir(run).name, "observations": len(obs), "created": dt.date.today().isoformat(),
                       "method": "grid search, Wilson >= 0.97 at >= 99% for 'high', 5-fold CV grouped by base_id"},
        }, indent=1) + "\n")
    lines = [f"Calibration from run {run_dir(run).name}: {len(obs)} scored fields "
             f"({sum(ob['correct'] for ob in obs)} right)", "",
             f"Knobs used: {knobs} ({'defaults' if use_defaults else 'grid search'}); grid search would pick {tuned}",
             "",
             "| route | tier | fields | right | accuracy | Wilson 95% low |", "|---|---|---|---|---|---|"]
    for route in ("all", "text_llm", "image_llm"):
        for t in ("high", "medium", "low"):
            a = accuracy.get(route, {}).get(t)
            if a:
                lines.append(f"| {route} | {t} | {a['n']} | {a['right']} | {a['accuracy']:.1%} | {a['wilson_lb']:.1%} |")
    lines += ["", f"Grid search, cross-validated ('high', 5 folds by base invoice): {cv['high_right']}/{cv['high_n']} right "
                  f"= {cv['high_accuracy']:.1%} (Wilson low {cv['high_wilson_lb']:.1%}); "
                  f"{cv['high_share']:.0%} of fields rated high",
              "", "Boxes found for printed values: " + "; ".join(
                  f"{r} {b['located']}/{b['printed']} ({b['share']:.1%})" for r, b in boxes.items()),
              "", f"Wrong but rated high ({len(wrong_high)}): " + (", ".join(wrong_high) or "none")]
    if write:
        lines += ["", f"Wrote {CALIBRATION_PATH.relative_to(specs.ROOT)}"]
    return "\n".join(lines)
