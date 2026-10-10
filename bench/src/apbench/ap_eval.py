"""Score an inbox run's checks against the companion labels (planted problems and natural ones).

  planted catch rate   per planted type: did the expected check fire on that invoice?
  per-check accuracy   for every check the labels cover: true/false alarms and misses, precision, recall
  matching accuracy    vendor, our company (addressee), purchase order
A check 'fires' when its outcome is fail or warn. Raises the labels can't judge (round totals, stamps, charges
not on the PO, ...) are counted separately, not as false alarms.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from . import specs
from .score import run_dir

# check families the labels can judge, and the label codes that make them expected
LABELED = ("dup", "bank.changed", "vendor.unknown", "vendor.tax_id_mismatch", "match.no_po", "match.price_over_po",
           "match.qty_over_received", "pattern.above_history", "pattern.wrong_addressee", "doctype.multiple_invoices",
           "doctype.not_invoice", "doctype.credit_note", "arith")


def _family(code: str) -> str | None:
    for fam in LABELED:
        if code == fam or code.startswith(fam + "."):
            return fam
    return None


def _expected_families(expect: list[str]) -> set[str]:
    fams = set()
    for e in expect:
        fams.add("dup" if e == "dup.*" else (_family(e) or e))
    return fams


def evaluate(run: str, set_name: str = "dev") -> tuple[str, dict[str, Any]]:
    labels = json.loads((specs.ROOT / "data" / set_name / "labels" / "ap_labels.json").read_text())
    results = {r["seq"]: r for r in json.loads((run_dir(run) / "ap_results.json").read_text())["items"]}
    tally: dict[str, Counter] = defaultdict(Counter)
    planted: dict[str, Counter] = defaultdict(Counter)
    unlabeled: Counter = Counter()
    matching: dict[str, Counter] = defaultdict(Counter)
    misses, false_alarms = [], []
    for item in labels["items"]:
        res = results.get(item["seq"])
        if res is None:
            continue
        doc_fired = {_family(c["code"]) for inv in res["invoices"] for c in inv["checks"] if c["outcome"] in ("fail", "warn")}
        for fam in ("doctype.multiple_invoices", "doctype.not_invoice"):
            want, got = fam in item["expect"], fam in doc_fired
            tally[fam][("tp" if got else "fn") if want else ("fp" if got else "tn")] += 1
        by_index = {inv["child_index"]: inv for inv in res["invoices"]}
        for lab in item["invoices"]:
            got_inv = by_index.get(lab["index"])
            if got_inv is None:
                continue
            fired = {}
            for c in got_inv["checks"]:
                if c["outcome"] in ("fail", "warn"):
                    fam = _family(c["code"])
                    if fam is None:
                        unlabeled[c["code"]] += 1
                    else:
                        fired.setdefault(fam, c["code"])
            expected = _expected_families(lab["expect"]) | ({"arith"} if "arith" in lab["natural"] else set()) \
                | ({"doctype.credit_note"} if "doctype.credit_note" in lab["natural"] else set())
            is_duplicate = "dup" in expected
            for fam in LABELED:
                if fam in ("doctype.multiple_invoices", "doctype.not_invoice"):
                    continue
                want, got = fam in expected, fam in fired
                if want and got:
                    tally[fam]["tp"] += 1
                elif want:
                    tally[fam]["fn"] += 1
                    misses.append(f"{item['ref']}: {fam} not raised")
                elif got and fam == "match.qty_over_received" and is_duplicate:
                    tally[fam]["explained_by_duplicate"] += 1  # a second copy can't use the same delivery again
                elif got:
                    tally[fam]["fp"] += 1
                    false_alarms.append(f"{item['ref']}: {fired[fam]}")
                else:
                    tally[fam]["tn"] += 1
            if lab["planted"]:
                want = _expected_families(lab["expect"]) - {"match.no_po"}
                planted[lab["planted"]]["caught" if want <= set(fired) else "missed"] += 1
            matching["vendor"]["right" if got_inv["vendor_code"] == lab["vendor_code"] else "wrong"] += 1
            matching["addressee"]["right" if (got_inv["entity"] is None) == (lab["entity_code"] is None) else "wrong"] += 1
            if lab["po_number"] and lab["vendor_code"]:
                matching["po"]["right" if got_inv["po_number"] == lab["po_number"] else "wrong"] += 1

    def pr(c: Counter) -> tuple[str, str]:
        tp, fp, fn = c["tp"], c["fp"], c["fn"]
        p = f"{tp / (tp + fp):.0%}" if tp + fp else "—"
        r = f"{tp / (tp + fn):.0%}" if tp + fn else "—"
        return p, r

    lines = [f"AP evaluation of run {run_dir(run).name} against the {set_name} labels", "",
             "## Planted problems caught", "", "| planted | caught | of |", "|---|---|---|"]
    for kind in sorted(planted):
        c = planted[kind]
        lines.append(f"| {kind} | {c['caught']} | {c['caught'] + c['missed']} |")
    lines += ["", "## Each labelled check", "",
              "| check | raised right | false alarms | missed | precision | recall | note |", "|---|---|---|---|---|---|---|"]
    for fam in LABELED:
        c = tally[fam]
        p, r = pr(c)
        note = f"{c['explained_by_duplicate']} more on duplicate copies" if c["explained_by_duplicate"] else ""
        lines.append(f"| {fam} | {c['tp']} | {c['fp']} | {c['fn']} | {p} | {r} | {note} |")
    lines += ["", "## Matching", ""] + [f"- {k}: {c['right']}/{c['right'] + c['wrong']} right" for k, c in matching.items()]
    lines += ["", "## Raised but not judged by the labels", "", ", ".join(f"{k} {v}" for k, v in sorted(unlabeled.items())) or "none",
              "", f"## Misses ({len(misses)})", "", *[f"- {m}" for m in misses],
              "", f"## False alarms ({len(false_alarms)})", "", *[f"- {f}" for f in false_alarms]]
    report = {"planted": {k: dict(v) for k, v in planted.items()}, "checks": {k: dict(v) for k, v in tally.items()},
              "matching": {k: dict(v) for k, v in matching.items()}, "unlabeled": dict(unlabeled),
              "misses": misses, "false_alarms": false_alarms}
    (run_dir(run) / "ap_eval.json").write_text(json.dumps(report, indent=1) + "\n")
    return "\n".join(lines), report
