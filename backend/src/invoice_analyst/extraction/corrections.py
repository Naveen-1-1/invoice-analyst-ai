"""A reviewer's corrections, laid over what the model read so every check sees the corrected values.

Corrections are stored as field values with source 'human' (the model's reading stays in the history, superseded).
Each one is applied twice: to the model's reading (InvoiceDoc), so checks that look at printed text use it, and to
the resolved values, so date-order guessing or subtotal derivation can't override what the reviewer typed.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any

from .normalize import norm_invoice_number, parse_amount, parse_date
from .resolve import Resolved
from .schemas import InvoiceDoc, TaxLine

TEXT_FIELDS = ("vendor_name", "invoice_number", "po_number", "vendor_tax_id")
DATE_FIELDS = ("invoice_date", "due_date")
AMOUNT_FIELDS = ("subtotal", "tax", "total")
CORRECTABLE = (*TEXT_FIELDS, *DATE_FIELDS, "currency", *AMOUNT_FIELDS)
CLEARABLE = ("po_number", "vendor_tax_id", "due_date")  # may be corrected to 'not on the invoice'


class CorrectionError(ValueError):
    """A correction that can't be used, with a message for the reviewer."""


def normalize(field: str, text: str | None, date_order: str = "dmy") -> Any:
    """The stored value for what the reviewer typed or clicked: ISO date, plain number, ISO currency code, or text."""
    if field not in CORRECTABLE:
        raise CorrectionError(f"{field} can't be corrected here.")
    text = (text or "").strip()
    if not text:
        if field in CLEARABLE:
            return None
        raise CorrectionError("Enter a value (this field can't be left empty).")
    if field in DATE_FIELDS:
        try:
            return dt.date.fromisoformat(text).isoformat()
        except ValueError:
            parsed, _ = parse_date(text, date_order)
            if parsed is None:
                raise CorrectionError(f"{text!r} isn't a date I can read; use YYYY-MM-DD.") from None
            return parsed.isoformat()
    if field in AMOUNT_FIELDS:
        try:
            amount = Decimal(text)
        except InvalidOperation:
            amount = parse_amount(text)
        if amount is None:
            raise CorrectionError(f"{text!r} isn't an amount I can read.")
        return str(abs(amount).quantize(Decimal("0.01")))
    if field == "currency":
        code = text.upper()
        if len(code) != 3 or not code.isalpha():
            raise CorrectionError("Use a three-letter currency code, such as USD or ZAR.")
        return code
    if field == "invoice_number" and not any(ch.isdigit() for ch in text):
        raise CorrectionError("Invoice numbers carry at least one digit.")
    return text


def _dual(inv: InvoiceDoc) -> bool:
    p = inv.amount_payable
    return p is not None and bool(p.currency) and (p.currency or "").upper() != (inv.currency or "").upper()


def apply(inv: InvoiceDoc, fixes: dict[str, Any]) -> InvoiceDoc:
    """A copy of the model's reading with the corrections written in."""
    inv = inv.model_copy(deep=True)
    dual = _dual(inv)
    for field, value in fixes.items():
        if field == "vendor_name":
            inv.vendor.name.text = value
        elif field == "invoice_number":
            inv.invoice_number.text = value
        elif field == "po_number":
            inv.po_number.text = value
        elif field == "vendor_tax_id":
            inv.vendor.tax_id.text = value
        elif field in DATE_FIELDS:
            printed = getattr(inv, field)
            printed.text, printed.value = value, value
        elif field == "currency":
            if dual:
                inv.amount_payable.currency = value  # type: ignore[union-attr]
            else:
                inv.currency = value
        elif field == "total":
            if dual:
                inv.amount_payable.amount = float(value)  # type: ignore[union-attr]
                inv.amount_payable.text = value  # type: ignore[union-attr]
            else:
                inv.total.text, inv.total.value = value, float(value)
        elif field == "subtotal" and not dual:
            inv.subtotal.text, inv.subtotal.value = value, float(value)
        elif field == "tax":
            if dual:
                inv.amount_payable.tax_amount = float(value)  # type: ignore[union-attr]
            else:
                rates = {t.rate_percent for t in inv.tax_lines if t.rate_percent is not None}
                inv.tax_lines = [TaxLine(label="Tax (corrected)", rate_percent=rates.pop() if len(rates) == 1 else None,
                                         amount=float(value))]
    return inv


def pin(r: Resolved, fixes: dict[str, Any]) -> Resolved:
    """Make the resolved values say exactly what the reviewer entered, and drop notes about those fields."""
    for field, value in fixes.items():
        if field == "invoice_number":
            r.invoice_number, r.invoice_number_norm = value, norm_invoice_number(value)
        elif field == "vendor_name":
            r.vendor_name = value
        elif field in DATE_FIELDS:
            setattr(r, field, value)
        elif field == "currency":
            r.currency = value
        elif field in AMOUNT_FIELDS:
            setattr(r, field, float(value))
    if "subtotal" in fixes or "tax" in fixes or "total" in fixes:
        if "subtotal" not in fixes and r.total is not None and r.tax is not None and _derived(r):
            r.subtotal = round(r.total - r.tax, 2)
    r.notes = [n for n in r.notes if n["code"].split(".", 1)[0] not in fixes]
    return r


def _derived(r: Resolved) -> bool:
    return any(n["code"] == "currency.dual" for n in r.notes)
