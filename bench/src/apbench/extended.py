"""Extended answer keys and scorer (decision 46): the AP fields the official scorer leaves out.

  po_number, vendor_tax_id   exact after removing spaces, punctuation, case and a leading printed label
  bank_account               the account's digits: IBAN, or sort code + account, or the account number
  terms_days                 payment terms as a number of days ('Net 30' = '30 days'; 'due on receipt' = 0)
  bill_to                    the official scorer's vendor-name rule (normalised fuzzy match)
  line_qty, line_unit_price  to the cent, on the lines the official scorer's rule pairs (amount + description)
  line_code                  exact after normalising

A field is in the key only where the document prints it, checked against the generator's own page text
(layout.document_text, no rendering). Statements have no extended fields; a multi-invoice file is keyed on its first
invoice, like the official answer key. The backend writes its readings to extended.json without seeing any key.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import specs
from .ap_eval import _terms
from .companion import parse_bank
from .score import run_dir

FIELDS = ("po_number", "vendor_tax_id", "bank_account", "terms_days", "bill_to", "line_qty", "line_unit_price",
          "line_code")
# which line columns each table layout prints (generator/layout.py, columns())
QTY_COLUMNS = {"std", "code", "vat", "hours", "pct", "dual"}
CODE_COLUMNS = {"code"}
CENT = 0.005


def _squash(text: str | None) -> str:
    return re.sub(r"\s+", "", (text or "")).casefold()


def _code(text: str | None) -> str:
    return re.sub(r"[^0-9A-Z]", "", (text or "").upper())


def _same_code(got: str | None, expected: str, label: str | None) -> bool:
    """Exact after normalising, with or without the printed label in front ('VAT No: 4123...' = '4123...')."""
    g, e = _code(got), _code(expected)
    return bool(g) and (g == e or g == _code(label) + e)


def keys_for(spec: Any, layout: Any) -> dict[str, Any] | None:
    if spec.kind == "statement":
        return None
    inv = spec.children[0] if spec.kind == "multi_invoice" else spec
    text = _squash(layout.document_text(layout.build_pages(spec)))

    def printed(value: str | None) -> bool:
        return bool(value) and _squash(value) in text

    key: dict[str, Any] = {"credit_note": inv.is_credit}
    if printed(inv.po_number):
        key["po_number"] = {"value": inv.po_number, "label": inv.po_label}
    if printed(inv.vendor.tax_id):
        key["vendor_tax_id"] = {"value": inv.vendor.tax_id, "label": inv.vendor.tax_label}
    bank = parse_bank(inv.vendor.bank)
    if bank and bank["account_norm"] and all(printed(line) for line in inv.vendor.bank):
        key["bank_account"] = bank["account_norm"]
    if printed(inv.terms) and _terms(inv.terms) is not None:
        key["terms_days"] = _terms(inv.terms)
    if printed(inv.customer.name):
        key["bill_to"] = inv.customer.name
    lines = []
    for ln in inv.lines:
        plain = ln.pct is None  # a percentage line's 'qty' column holds the base value, its 'rate' a percentage
        lines.append({
            "description": ln.description, "amount": ln.amount,
            "alt_amounts": [inv.payable_line(ln)] if inv.payable and inv.payable.line_column else [],
            "qty": ln.qty if plain and ln.qty is not None and inv.columns in QTY_COLUMNS else None,
            "unit_price": ln.unit_price if plain and ln.unit_price is not None and inv.columns in QTY_COLUMNS else None,
            "code": ln.code if ln.code and inv.columns in CODE_COLUMNS else None})
    key["lines"] = lines
    return key


def build_keys(corpus: dict[str, Any]) -> dict[str, Any]:
    _, _, layout = specs.generator_modules()
    return {doc_id: k for doc_id, spec in sorted(corpus.items()) if (k := keys_for(spec, layout)) is not None}


def write_keys(set_name: str, corpus: dict[str, Any]) -> Path:
    path = specs.ROOT / "data" / set_name / "labels" / "extended_answers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"set": set_name, "documents": build_keys(corpus)}, indent=1) + "\n")
    return path


# ---------------------------------------------------------------- scoring


def _scorer():
    specs.generator_modules()  # puts the benchmark on sys.path
    from scorer import score  # noqa: PLC0415  (the official scorer's own matching rules)
    return score


def _pairs(got: list[dict], expected: list[dict], magnitude: bool, sc) -> list[tuple[dict, dict]]:
    """The official scorer's greedy line rule (amount within a cent, description similarity >= 0.6), as pairs."""
    preds = [(li, sc.to_num(li.get("amount"))) for li in got or []]
    used: set[int] = set()
    out = []
    for exp in expected:
        amounts = [exp["amount"], *exp.get("alt_amounts", [])]
        best, best_sim = None, 0.6
        for i, (li, amt) in enumerate(preds):
            if i in used or amt is None:
                continue
            a = abs(amt) if magnitude else amt
            if not any(abs(a - (abs(x) if magnitude else x)) <= 0.0101 for x in amounts):
                continue
            sim = sc.desc_similarity(li.get("description") or "", exp["description"])
            if sim >= best_sim:
                best, best_sim = i, sim
        if best is not None:
            used.add(best)
            out.append((preds[best][0], exp))
    return out


def _num_equal(got: Any, expected: float, sc) -> bool:
    g = sc.to_num(got)
    return g is not None and abs(abs(g) - abs(expected)) < CENT


def score_document(key: dict[str, Any], pred: dict[str, Any] | None, sc) -> list[dict[str, Any]]:
    pred = pred or {}
    out = []

    def add(field: str, ok: bool, expected: Any, got: Any) -> None:
        out.append({"field": field, "correct": bool(ok), "expected": expected, "got": got})

    for field in ("po_number", "vendor_tax_id"):
        if field in key:
            exp = key[field]
            add(field, _same_code(pred.get(field), exp["value"], exp["label"]), exp["value"], pred.get(field))
    if "bank_account" in key:
        add("bank_account", (pred.get("bank_account") or "") == key["bank_account"], key["bank_account"],
            pred.get("bank_account"))
    if "terms_days" in key:
        add("terms_days", _terms(pred.get("payment_terms")) == key["terms_days"], key["terms_days"],
            pred.get("payment_terms"))
    if "bill_to" in key:
        add("bill_to", sc.vendor_match(pred.get("bill_to"), key["bill_to"])[0], key["bill_to"], pred.get("bill_to"))
    paired = {id(exp): got for got, exp in _pairs(pred.get("lines") or [], key["lines"], key["credit_note"], sc)}
    for n, exp in enumerate(key["lines"], 1):
        got = paired.get(id(exp))
        if exp["qty"] is not None:
            add("line_qty", got is not None and _num_equal(got.get("quantity"), exp["qty"], sc), exp["qty"],
                got.get("quantity") if got else None)
        if exp["unit_price"] is not None:
            add("line_unit_price", got is not None and _num_equal(got.get("unit_price"), exp["unit_price"], sc),
                exp["unit_price"], got.get("unit_price") if got else None)
        if exp["code"] is not None:
            add("line_code", got is not None and _code(got.get("item_code")) == _code(exp["code"]), exp["code"],
                got.get("item_code") if got else None)
    return out


def score_run(run: str, set_name: str = "dev") -> tuple[str, dict[str, Any]]:
    keys = json.loads((specs.ROOT / "data" / set_name / "labels" / "extended_answers.json").read_text())["documents"]
    preds = json.loads((run_dir(run) / "extended.json").read_text())
    sc = _scorer()
    per_field: dict[str, Counter] = defaultdict(Counter)
    per_category: dict[str, Counter] = defaultdict(Counter)
    errors: dict[str, list[dict]] = {}
    scored_docs = 0
    for doc_id, key in keys.items():
        if doc_id not in preds:
            continue
        scored_docs += 1
        results = score_document(key, preds[doc_id], sc)
        category = doc_id.split("-", 1)[0]
        for r in results:
            per_field[r["field"]]["right" if r["correct"] else "wrong"] += 1
            per_category[category]["right" if r["correct"] else "wrong"] += 1
        wrong = [r for r in results if not r["correct"]]
        if wrong:
            errors[doc_id] = wrong

    def pct(c: Counter) -> str:
        n = c["right"] + c["wrong"]
        return f"{c['right'] / n:.1%}" if n else "—"

    lines = [f"Extended fields, run {run_dir(run).name} against the {set_name} extended answer keys "
             f"({scored_docs} documents)", "", "| field | right | of | accuracy |", "|---|---|---|---|"]
    for f in FIELDS:
        c = per_field[f]
        lines.append(f"| {f} | {c['right']} | {c['right'] + c['wrong']} | {pct(c)} |")
    total = sum(per_field.values(), Counter())
    lines += [f"| **all** | {total['right']} | {total['right'] + total['wrong']} | {pct(total)} |", "",
              "| category | right | of | accuracy |", "|---|---|---|---|"]
    for cat in sorted(per_category):
        c = per_category[cat]
        lines.append(f"| {cat} | {c['right']} | {c['right'] + c['wrong']} | {pct(c)} |")
    lines += ["", f"## Wrong ({sum(len(v) for v in errors.values())} fields in {len(errors)} documents)", ""]
    for doc_id, wrong in sorted(errors.items()):
        lines.append(f"- {doc_id}: " + "; ".join(f"{w['field']} {w['got']!r} (expected {w['expected']!r})"
                                                 for w in wrong))
    report = {"set": set_name, "documents": scored_docs,
              "fields": {f: dict(per_field[f]) for f in FIELDS},
              "categories": {k: dict(v) for k, v in sorted(per_category.items())},
              "overall": dict(total), "errors": errors}
    (run_dir(run) / "ext_scores.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    (run_dir(run) / "ext_scores.md").write_text("\n".join(lines) + "\n")
    return "\n".join(lines), report
