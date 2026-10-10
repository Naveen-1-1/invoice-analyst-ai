"""Score an inbox run's checks against the companion labels (planted problems and natural ones).

  planted catch rate   per planted type: did the expected check fire on that invoice?
  per-check accuracy   for every check the labels cover: true/false alarms and misses, precision, recall
  matching accuracy    vendor, our company (addressee), purchase order
  payment timing       each automatic approval (booked on arrival) planned into the run an independent
                       calculation expects from the true issue date, due date or terms, and the vendor's terms
  decisions            straight-through rate; false auto-approvals (approved with no person although the labels
                       expect a problem, or with the total, number or vendor read wrong: the key safety number,
                       target 0); why the rest went to review, and how many of those had nothing actually wrong
A check 'fires' when its outcome is fail or warn. Raises the labels can't judge (round totals, stamps, charges
not on the PO, ...) are counted separately, not as false alarms.
"""

from __future__ import annotations

import datetime as dt
import json
import re
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


INFO_ONLY = {"doctype.credit_note"}

# decision 12, worked out here independently of the backend: weekly runs on Thursday, the last one on or before the
# due date (or the discount deadline when it can still be made), else the next run
THURSDAY = 3
_RECEIPT = re.compile(r"\b(due\s+(up)?on\s+receipt|on\s+receipt|immediate(ly)?|cod|cash\s+on\s+delivery)\b", re.I)
_DAYS = re.compile(r"(?:net\s*(\d{1,3}))|(?:(\d{1,3})\s*(?:days?|d\b))", re.I)


def _thursday_on_or_after(d: dt.date) -> dt.date:
    return d + dt.timedelta(days=(THURSDAY - d.weekday()) % 7)


def _thursday_on_or_before(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=(d.weekday() - THURSDAY) % 7)


def _terms(text: str | None) -> int | None:
    if not text:
        return None
    if _RECEIPT.search(text):
        return 0
    m = _DAYS.search(text)
    return int(m.group(1) or m.group(2)) if m else None


def expected_run(lab: dict, vendor: dict | None, booked: dt.date) -> dt.date:
    issue = dt.date.fromisoformat(lab["invoice_date"])
    days = _terms(lab.get("terms"))
    due = (dt.date.fromisoformat(lab["due_date"]) if lab.get("due_date") else
           issue + dt.timedelta(days=days) if days is not None else
           issue + dt.timedelta(days=vendor["terms_days"]) if vendor and vendor.get("terms_days") is not None else
           issue + dt.timedelta(days=30))
    first = _thursday_on_or_after(booked)
    if vendor and vendor.get("discount_pct") and vendor.get("discount_days"):
        by = _thursday_on_or_before(issue + dt.timedelta(days=vendor["discount_days"]))
        if by >= first:
            return by
    return max(first, _thursday_on_or_before(due))  # shown to the reviewer, never a reason to stop an automatic approval


def _norm(text: str | None) -> str:
    return "".join(ch for ch in (text or "").upper() if ch.isalnum())


def _read_wrong(got: dict, lab: dict) -> list[str]:
    wrong = []
    if got["total"] is None or lab["total"] is None or abs(got["total"] - lab["total"]) > 0.01:
        wrong.append("total")
    if _norm(got["invoice_number"]) != _norm(lab["invoice_number"]):
        wrong.append("invoice number")
    if got["vendor_code"] != lab["vendor_code"]:
        wrong.append("vendor")
    return wrong


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
    vendors = {v["code"]: v for v in json.loads((specs.ROOT / "data" / set_name / "companion" / "vendors.json").read_text())}
    stream = {s["seq"]: s for s in json.loads((specs.ROOT / "data" / set_name / "inbox" / "stream.json").read_text())}
    timing: Counter = Counter()
    late_or_early: list[str] = []
    decisions: Counter = Counter()
    review_labels: Counter = Counter()
    needless_labels: Counter = Counter()
    false_auto: list[str] = []
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
            if got_inv["doc_type"] in ("invoice", "credit_note"):
                problems = sorted((_expected_families(lab["expect"]) | set(item["expect"])) - INFO_ONLY)
                wrong = _read_wrong(got_inv, lab)
                decisions["bills"] += 1
                state = got_inv["state"]
                if state == "AUTO_APPROVED" and lab.get("invoice_date"):
                    planned = [p["run_date"] for p in got_inv.get("payments") or [] if p["run_date"]]
                    want = expected_run(lab, vendors.get(lab["vendor_code"]),
                                        dt.date.fromisoformat(stream[item["seq"]]["received_date"]))
                    if planned and planned[0] == want.isoformat():
                        timing["right run"] += 1
                    else:
                        timing["other run"] += 1
                        late_or_early.append(f"{item['ref']}: planned {planned[0] if planned else 'nowhere'}, "
                                             f"expected {want}")
                if state == "AUTO_APPROVED":
                    decisions["auto_approved"] += 1
                    if problems or wrong:
                        false_auto.append(f"{item['ref']}: " + ", ".join(problems + [f"{w} read wrong" for w in wrong]))
                elif state in ("NEEDS_REVIEW", "ON_HOLD", "APPROVED", "APPROVED_PARTIAL", "REJECTED"):
                    decisions["reviewed"] += 1
                    review_labels.update(got_inv.get("review_labels") or [])
                    if not problems and not wrong:
                        decisions["reviewed_nothing_wrong"] += 1
                        needless_labels.update(got_inv.get("review_labels") or [])
                else:
                    decisions["undecided"] += 1
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
    bills = decisions["bills"] or 1
    lines += ["", "## Decisions", "",
              f"- bills (invoices and credit notes): {decisions['bills']}",
              f"- approved automatically (straight through): {decisions['auto_approved']} "
              f"({decisions['auto_approved'] / bills:.0%})",
              f"- **false auto-approvals: {len(false_auto)}**",
              f"- sent to review: {decisions['reviewed']} ({decisions['reviewed'] / bills:.0%}); with nothing actually "
              f"wrong: {decisions['reviewed_nothing_wrong']}",
              *([f"- not decided yet: {decisions['undecided']}"] if decisions["undecided"] else []),
              *[f"  - {f}" for f in false_auto],
              "", "| review label | invoices | of which nothing wrong |", "|---|---|---|",
              *[f"| {k} | {v} | {needless_labels[k]} |" for k, v in review_labels.most_common()]]
    lines += ["", "## Payment timing (automatic approvals)", "",
              f"- planned into the expected run: {timing['right run']} of {timing['right run'] + timing['other run']}",
              *[f"  - {x}" for x in late_or_early]]
    lines += ["", "## Matching", ""] + [f"- {k}: {c['right']}/{c['right'] + c['wrong']} right" for k, c in matching.items()]
    lines += ["", "## Raised but not judged by the labels", "", ", ".join(f"{k} {v}" for k, v in sorted(unlabeled.items())) or "none",
              "", f"## Misses ({len(misses)})", "", *[f"- {m}" for m in misses],
              "", f"## False alarms ({len(false_alarms)})", "", *[f"- {f}" for f in false_alarms]]
    report = {"planted": {k: dict(v) for k, v in planted.items()}, "checks": {k: dict(v) for k, v in tally.items()},
              "matching": {k: dict(v) for k, v in matching.items()}, "unlabeled": dict(unlabeled),
              "misses": misses, "false_alarms": false_alarms, "decisions": dict(decisions),
              "false_auto_approvals": false_auto, "payment_timing": dict(timing), "payment_timing_off": late_or_early, "review_labels": dict(review_labels),
              "review_labels_nothing_wrong": dict(needless_labels)}
    (run_dir(run) / "ap_eval.json").write_text(json.dumps(report, indent=1) + "\n")
    return "\n".join(lines), report
