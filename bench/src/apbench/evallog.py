"""One compact entry per evaluation, appended to data/eval/EVAL_LOG.jsonl (the dashboard reads it).

An entry gathers, for one set (dev, or the held-out set in the final run): the official extraction scores, the
extended fields, the confidence check, and the AP results of the inbox run (checks, decisions, payment timing),
with the documents that went wrong. Kinds: 'dev' (a reference run), 'final' (the one held-out evaluation, decision
47), 'after_final' (any later held-out run; it never replaces the final one).
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path
from typing import Any

from . import ap_eval, calibrate, extended, specs
from .score import run_dir

LOG = specs.ROOT / "data" / "eval" / "EVAL_LOG.jsonl"


def commit() -> str:
    sha = subprocess.run(["git", "-C", str(specs.ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                         check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(specs.ROOT), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, check=True).stdout.strip()
    return sha + ("-dirty" if dirty else "")


def entries() -> list[dict[str, Any]]:
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text().splitlines() if line.strip()]


def _official(run: str) -> dict[str, Any]:
    scores = json.loads((run_dir(run) / "scores.json").read_text())
    s = scores["summary"]
    errors = []
    for d in scores["documents"]:
        wrong = [f for f, r in d["fields"].items() if not r["correct"]]
        if wrong:
            errors.append({"doc_id": d["doc_id"], "fields": wrong})
    return {"documents": s["documents"], "fully_correct": s["fully_correct"], "key_fields": s["key_fields"],
            "fields": s["fields"], "line_items": s["line_items"], "flags": s["flags"], "categories": s["categories"],
            "severity": s.get("severity", {}), "errors": errors}


def summarize(kind: str, set_name: str, bench_run: str, inbox_run: str | None) -> dict[str, Any]:
    _, ext = extended.score_run(bench_run, set_name)
    entry: dict[str, Any] = {
        "kind": kind, "set": set_name, "logged_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "commit": commit(), "bench_run": run_dir(bench_run).name, "inbox_run": run_dir(inbox_run).name if inbox_run else None,
        "official": _official(bench_run),
        "extended": {"documents": ext["documents"], "fields": ext["fields"], "overall": ext["overall"],
                     "errors": [{"doc_id": d, "fields": sorted({w["field"] for w in ws})}
                                for d, ws in sorted(ext["errors"].items())]},
        "confidence": calibrate.check(bench_run, set_name),
    }
    if inbox_run:
        _, ap = ap_eval.evaluate(inbox_run, set_name)
        entry["ap"] = {k: ap[k] for k in ("planted", "checks", "matching", "decisions", "false_auto_approvals",
                                          "payment_timing", "payment_timing_off", "review_labels",
                                          "review_labels_nothing_wrong", "misses", "false_alarms")}
    return entry


def append(entry: dict[str, Any]) -> Path:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return LOG
