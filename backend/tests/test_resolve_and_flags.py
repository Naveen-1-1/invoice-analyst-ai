"""From what the model read to benchmark values: conventions and the arithmetic flag."""

from __future__ import annotations

from invoice_analyst.bench.predictions import flag_reasons, to_prediction
from invoice_analyst.checks.arithmetic import arithmetic_issues
from invoice_analyst.extraction.schemas import FileExtraction, InvoiceDoc


def inv(**kw) -> InvoiceDoc:
    base = {
        "document_type": "invoice", "vendor": {"name": {"text": "Acme Industrial Supply Co"}, "country": "US"},
        "invoice_number": {"text": "ACM-100401"}, "invoice_date": {"text": "04/01/2024", "value": "2024-04-01"},
        "due_date": {"text": "05/01/2024", "value": "2024-05-01"}, "payment_terms": {"text": "Net 30"},
        "currency": "USD",
        "lines": [{"description": "Safety bundle", "amount": 1020.0}, {"description": "Lockout kit", "amount": 496.0}],
        "subtotal": {"text": "1,516.00", "value": 1516.0},
        "tax_lines": [{"label": "Sales tax 7.5%", "rate_percent": 7.5, "amount": 113.7}],
        "total": {"text": "$1,629.70", "value": 1629.7},
    }
    base.update(kw)
    return InvoiceDoc.model_validate(base)


def test_consistent_invoice_maps_cleanly_and_is_not_flagged() -> None:
    p = to_prediction(FileExtraction(invoices=[inv()]))
    assert p["invoice_date"] == "2024-04-01" and p["due_date"] == "2024-05-01"  # US: month first
    assert (p["subtotal"], p["tax"], p["total_amount"], p["currency"]) == (1516.0, 113.7, 1629.7, "USD")
    assert p["flagged"] is False and len(p["line_items"]) == 2


def test_inflated_total_is_flagged() -> None:
    assert [i.code for i in arithmetic_issues(inv(total={"text": "3,100.00", "value": 3100.0}))] == [
        "arith.total_mismatch"]


def test_lines_not_matching_subtotal_is_flagged() -> None:
    codes = [i.code for i in arithmetic_issues(inv(subtotal={"value": 1600.0}, total={"value": 1713.7}))]
    assert "arith.lines_vs_subtotal" in codes


def test_vat_amount_not_matching_rate_is_flagged() -> None:
    doc = inv(tax_lines=[{"label": "VAT 15%", "rate_percent": 15, "amount": 151.6}], total={"value": 1667.6})
    assert [i.code for i in arithmetic_issues(doc)] == ["arith.tax_rate_mismatch"]


def test_charges_count_towards_the_total() -> None:
    doc = inv(charges=[{"description": "Freight", "amount": 50.0, "kind": "shipping"}], total={"value": 1679.7})
    assert arithmetic_issues(doc) == []


def test_vat_inclusive_subtotal_is_net() -> None:
    doc = inv(prices_include_tax=True, subtotal={}, tax_lines=[{"label": "VAT incl", "amount": 197.74}],
              total={"value": 1516.0})
    p = to_prediction(FileExtraction(invoices=[doc]))
    assert p["subtotal"] == round(1516.0 - 197.74, 2) and p["flagged"] is False


def test_dual_currency_uses_the_payable_box() -> None:
    doc = inv(vendor={"name": {"text": "Etosha Gate Freight"}, "country": "NA"}, currency="EUR",
              total={"value": 162.48}, amount_payable={"currency": "NAD", "amount": 3247.51, "tax_amount": 0.0})
    p = to_prediction(FileExtraction(invoices=[doc]))
    assert (p["currency"], p["total_amount"], p["tax"], p["subtotal"]) == ("NAD", 3247.51, 0.0, 3247.51)


def test_statement_is_other_and_credit_note_amounts_are_positive() -> None:
    assert to_prediction(FileExtraction(invoices=[inv(document_type="statement")]))["document_type"] == "other"
    credit = inv(document_type="credit_note", total={"value": -1629.7}, subtotal={"value": -1516.0},
                 lines=[{"description": "Return", "amount": -1516.0}],
                 tax_lines=[{"label": "Sales tax 7.5%", "rate_percent": 7.5, "amount": -113.7}])
    p = to_prediction(FileExtraction(invoices=[credit]))
    assert (p["total_amount"], p["subtotal"], p["tax"]) == (1629.7, 1516.0, 113.7)


def test_several_invoices_in_one_file_are_flagged() -> None:
    second = inv(invoice_number={"text": "ACM-100402"})
    assert flag_reasons(FileExtraction(invoices=[inv(), second])) == ["file.multiple_invoices"]


def test_ambiguous_date_resolved_by_country_with_a_note() -> None:
    from invoice_analyst.extraction.resolve import resolve

    r = resolve(inv(vendor={"name": {"text": "Pennine Tyre"}, "country": "GB"}, payment_terms={},
                    invoice_date={"text": "04/03/2026", "value": "2026-04-03"}, due_date={}))
    assert r.invoice_date == "2026-03-04" and r.date_order_source == "country"
    assert {"invoice_date.ambiguous", "invoice_date.disagrees"} <= {x["code"] for x in r.notes}


def test_unread_line_amount_is_not_an_inconsistency() -> None:
    doc = inv(lines=[{"description": "Safety bundle", "amount": 1020.0}, {"description": "Fuel surcharge 15%"}],
              subtotal={"value": 1516.0})
    assert arithmetic_issues(doc) == []


def test_references_also_listed_without_their_labels() -> None:
    p = to_prediction(FileExtraction(invoices=[inv(references=["Trip Ref: TR27001", "IPPU8832877"])]))
    assert p["references"] == ["Trip Ref: TR27001", "TR27001", "IPPU8832877"]


def test_vat_on_fees_only_uses_the_lines_carrying_the_rate() -> None:
    doc = inv(lines=[{"description": "Import VAT paid (disbursement)", "amount": 4279.34, "vat_rate_percent": 0},
                     {"description": "Customs clearance fee", "amount": 2674.82, "vat_rate_percent": 15},
                     {"description": "Documentation fee", "amount": 487.72, "vat_rate_percent": 15}],
              subtotal={"value": 7441.88}, tax_lines=[{"label": "VAT 15% on fees", "rate_percent": 15, "amount": 474.38}],
              total={"value": 7916.26})
    assert arithmetic_issues(doc) == []


def test_tax_shown_only_in_the_payable_currency_is_not_line_tax() -> None:
    doc = inv(currency="USD", lines=[{"description": "Documentation fee", "amount": 92.66}],
              subtotal={"value": 92.66}, tax_lines=[{"label": "VAT ZAR", "amount": 256.46}], total={"value": 92.66},
              amount_payable={"currency": "ZAR", "amount": 1966.16, "tax_amount": 256.46})
    assert arithmetic_issues(doc) == []


def test_invoice_number_without_digits_is_left_empty() -> None:
    p = to_prediction(FileExtraction(invoices=[inv(invoice_number={"text": "KH"})]))
    assert p["invoice_number"] is None
