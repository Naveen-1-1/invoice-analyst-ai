"""Turn what the model read (InvoiceDoc) into final invoice values, plus notes on anything uncertain.

Conventions applied here, not by the model:
  - dates: re-parsed from the printed text; ambiguous numeric dates resolved by `normalize.resolve_date_order`;
  - subtotal: the printed net subtotal, else lines - tax on VAT-inclusive invoices, else the sum of the lines;
  - dual currency: a payable box in another currency is the amount due (its currency, total and tax);
  - document type: statements and other non-bills become 'other'.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import normalize
from .schemas import FileExtraction, InvoiceDoc


@dataclass
class Resolved:
    document_type: str  # invoice / credit_note / other
    vendor_name: str | None
    vendor_country: str | None
    invoice_number: str | None
    invoice_number_norm: str | None
    invoice_date: str | None
    due_date: str | None
    currency: str | None
    subtotal: float | None
    tax: float | None
    total: float | None
    line_currency: str | None
    lines: list[dict]
    references: list[str]
    date_order: str
    date_order_source: str
    notes: list[dict] = field(default_factory=list)  # [{"code", "text"}] for confidence and review


def _references(raw: list[str]) -> list[str]:
    """Each reference as printed and without a leading label ('Trip Ref: TR27001' -> also 'TR27001')."""
    out: list[str] = []
    for ref in raw:
        for value in (ref.strip(), ref.rsplit(":", 1)[-1].strip() if ":" in ref else ""):
            if value and value not in out:
                out.append(value)
    return out


def _currency(code: str | None) -> str | None:
    code = (code or "").strip().upper()
    return code if len(code) == 3 and code.isalpha() else None


def resolve(inv: InvoiceDoc) -> Resolved:
    notes: list[dict] = []
    country = (inv.vendor.country or "").upper() or None
    date_texts = [inv.invoice_date.text, inv.due_date.text]
    order, how = normalize.resolve_date_order(date_texts, country, inv.invoice_date.text, inv.due_date.text,
                                              inv.payment_terms.text)

    def date(field_name: str) -> str | None:
        printed = getattr(inv, field_name)
        parsed, ambiguous = normalize.parse_date(printed.text, order)
        model_value = printed.value
        if parsed is None:
            if printed.text:
                notes.append({"code": f"{field_name}.unparsed", "text": f"Couldn't read the date {printed.text!r}; "
                                                                        "using the model's reading."})
            return model_value
        iso = parsed.isoformat()
        if ambiguous:
            # settled by another date on the document or by the terms = evidence; by country alone = a guess
            settled = "resolved" if how in ("document", "terms") else "country"
            why = {"document": "another date on the document", "terms": "the payment terms",
                   "country": "the vendor country's usual date order"}[how]
            notes.append({"code": f"{field_name}.ambiguous_{settled}",
                          "text": f"{printed.text!r} can be read two ways; read as {iso} from {why}."})
        if model_value and model_value != iso:
            notes.append({"code": f"{field_name}.disagrees",
                          "text": f"The model read {printed.text!r} as {model_value}; the rules give {iso}."})
        return iso

    for printed, name in ((inv.subtotal, "subtotal"), (inv.total, "total")):
        reparsed = normalize.parse_amount(printed.text)
        if reparsed is not None and printed.value is not None and abs(float(reparsed) - abs(printed.value)) > 0.005:
            notes.append({"code": f"{name}.disagrees",
                          "text": f"The model read {printed.text!r} as {printed.value}; the text says {reparsed}."})

    line_currency = _currency(inv.currency)
    lines = [{"description": ln.description, "amount": abs(ln.amount)} for ln in inv.lines if ln.amount is not None]
    lines_sum = round(sum(ln["amount"] for ln in lines), 2)
    tax = round(sum(abs(t.amount) for t in inv.tax_lines if t.amount is not None), 2) if inv.tax_lines else None
    total = abs(inv.total.value) if inv.total.value is not None else None
    if inv.subtotal.value is not None:
        subtotal = abs(inv.subtotal.value)
    elif lines:
        subtotal = round(lines_sum - (tax or 0.0), 2) if inv.prices_include_tax else lines_sum
    else:
        subtotal = None
    currency = line_currency

    payable = inv.amount_payable
    if payable is not None and _currency(payable.currency) and _currency(payable.currency) != line_currency:
        currency = _currency(payable.currency)
        total = abs(payable.amount)
        tax = abs(payable.tax_amount) if payable.tax_amount is not None else 0.0
        subtotal = round(total - tax, 2)
        notes.append({"code": "currency.dual", "text": f"Lines are in {line_currency}; the amount payable is "
                                                       f"{total:,.2f} {currency}."})

    number = inv.invoice_number.text
    if number and not any(ch.isdigit() for ch in number):
        # Invoice numbers carry digits; letters alone are usually a logo or initials (e.g. 'KH')
        notes.append({"code": "invoice_number.no_digits",
                      "text": f"{number!r} has no digits, so it is unlikely to be the invoice number; left empty."})
        number = None

    doc_type = inv.document_type if inv.document_type in ("invoice", "credit_note") else "other"
    return Resolved(
        document_type=doc_type, vendor_name=inv.vendor.name.text, vendor_country=country,
        invoice_number=number, invoice_number_norm=normalize.norm_invoice_number(number),
        invoice_date=date("invoice_date"), due_date=date("due_date"), currency=currency, subtotal=subtotal, tax=tax,
        total=total, line_currency=line_currency, lines=lines, references=_references(inv.references), date_order=order,
        date_order_source=how, notes=notes)


def primary(extraction: FileExtraction) -> InvoiceDoc:
    """The invoice a single-invoice view shows: the first one in the file."""
    return extraction.invoices[0]
