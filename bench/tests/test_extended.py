"""The extended scorer's rules (decision 46), on hand-made keys and readings."""

from __future__ import annotations

from apbench import extended
from apbench.ap_eval import expected_run

import datetime as dt

KEY = {"credit_note": False,
       "po_number": {"value": "PO-55210", "label": "PO"},
       "vendor_tax_id": {"value": "GB 306 3842 07", "label": "VAT No"},
       "bank_account": "GB29NWBK60161331926819", "terms_days": 30, "bill_to": "Hayes & Mitchell LLP",
       "lines": [{"description": "Copy paper A4", "amount": 971.36, "alt_amounts": [], "qty": 4, "unit_price": 242.84,
                  "code": "CP-A4"},
                 {"description": "Freight", "amount": 120.0, "alt_amounts": [], "qty": None, "unit_price": None,
                  "code": None}]}
GOOD = {"po_number": "PO: PO-55210", "vendor_tax_id": "VAT No: GB306384207", "bank_account": "GB29NWBK60161331926819",
        "payment_terms": "Net 30", "bill_to": "HAYES AND MITCHELL LLP",
        "lines": [{"description": "Copy paper A4 80gsm", "amount": 971.36, "quantity": 4.0, "unit_price": 242.84,
                   "item_code": "cp a4"}]}


def results(pred):
    return {r["field"]: r["correct"] for r in extended.score_document(KEY, pred, extended._scorer())}


def test_right_readings_count_as_right_whatever_the_formatting() -> None:
    assert results(GOOD) == {f: True for f in ("po_number", "vendor_tax_id", "bank_account", "terms_days", "bill_to",
                                               "line_qty", "line_unit_price", "line_code")}


def test_one_wrong_digit_is_wrong_and_unpaired_lines_score_nothing() -> None:
    bad = {**GOOD, "po_number": "PO-55211", "vendor_tax_id": "GB 306 3842 08", "bank_account": "GB29NWBK60161331926818",
           "payment_terms": "60 days", "bill_to": "Mitchell Partners", "lines": [{**GOOD["lines"][0], "amount": 971.0}]}
    assert set(results(bad).values()) == {False}  # an unpaired line: its qty, price and code are all wrong
    assert results({})["po_number"] is False


def test_the_label_only_counts_in_front() -> None:
    assert extended._same_code("PO-55210", "PO-55210", "PO")
    assert extended._same_code("PO Number PO-55210", "PO-55210", "PO Number")
    assert not extended._same_code("55210", "PO-55210", "PO")


def test_payment_timing_oracle() -> None:
    lab = {"invoice_date": "2026-01-15", "due_date": None, "terms": "Net 30"}  # due Sat 14 Feb: Thu 12 Feb
    assert expected_run(lab, None, dt.date(2026, 1, 20)) == dt.date(2026, 2, 12)
    vendor = {"terms_days": 30, "discount_pct": 2.0, "discount_days": 10}  # by Sun 25 Jan: Thu 22 Jan
    assert expected_run(lab, vendor, dt.date(2026, 1, 20)) == dt.date(2026, 1, 22)
    assert expected_run({**lab, "due_date": "2026-01-10"}, None, dt.date(2026, 1, 20)) == dt.date(2026, 1, 22)
