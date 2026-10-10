"""Double-entry booking (decisions 40 and 42), in the invoice's currency, for one of our companies.

  approval of an invoice   Dr expense (the PO line's account, else the vendor's default, else 5000)
                           Dr 1300 VAT input           Cr 2000 accounts payable    Cr 2100 withholding tax
  approval of a credit note  the same with the sides swapped
  payment                  Dr 2000 accounts payable    Cr 1000 cash                Cr 4900 discount received
  credit note netted in a run  Dr 1000 cash            Cr 2000 accounts payable

Entries are append-only and balance per currency (database triggers). A partial approval books its share of the
tax (decision 36); approving the rest books whatever tax and withholding are left.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (Account, Approval, Extraction, Invoice, InvoiceLine, JournalEntry, JournalLine, MatchLine,
                         PaymentItem, PaymentRun, PoLine, Vendor)

CASH, VAT_IN, AP, WHT, DISCOUNTS, DEFAULT_EXPENSE = "1000", "1300", "2000", "2100", "4900", "5000"
CENT = Decimal("0.01")


@dataclass
class Booking:
    entry: JournalEntry
    payable: Decimal  # what it adds to accounts payable; negative for a credit note


def _money(x: Decimal) -> Decimal:
    return x.quantize(CENT)


def withholding(s: Session, inv: Invoice) -> Decimal:
    ext = s.get(Extraction, inv.extraction_id) if inv.extraction_id else None
    raw = ((ext.raw or {}).get("invoices") or [{}])[inv.child_index] if ext else {}
    value = raw.get("withholding_amount")
    return _money(abs(Decimal(str(value)))) if value else Decimal(0)


def _posted(s: Session, inv: Invoice, account: str) -> Decimal:
    """Tax or withholding already booked for this invoice (by earlier approvals)."""
    rows = s.execute(select(func.coalesce(func.sum(JournalLine.debit + JournalLine.credit), 0))
                     .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                     .where(JournalEntry.invoice_id == inv.id, JournalEntry.kind.in_(("invoice", "credit_note")),
                            JournalLine.account_code == account)).scalar()
    return Decimal(rows or 0)


def _shares(s: Session, inv: Invoice, approval: Approval) -> tuple[Decimal, Decimal]:
    """(tax, withholding) for this approval."""
    total = inv.total or approval.amount
    tax_all, wht_all = inv.tax or Decimal(0), withholding(s, inv)
    earlier = Decimal(s.scalar(select(func.coalesce(func.sum(Approval.amount), 0))
                               .where(Approval.invoice_id == inv.id, Approval.id < approval.id)) or 0)
    if earlier and earlier + approval.amount >= total:  # the rest of a partial approval: whatever is left
        tax, wht = tax_all - _posted(s, inv, VAT_IN), wht_all - _posted(s, inv, WHT)
    else:
        split = next((r.get("split") for r in approval.reasons or [] if r.get("kind") == "partial" and r.get("split")),
                     None)
        share = approval.amount / total if total else Decimal(1)
        tax = Decimal(split["approved_tax"]) if split else _money(tax_all * share)
        wht = _money(wht_all * share)
    clamp = lambda x: min(max(x, Decimal(0)), approval.amount)  # noqa: E731
    return clamp(tax), clamp(wht)


def _expense_split(s: Session, inv: Invoice, net: Decimal) -> list[tuple[str, Decimal]]:
    """The net amount across expense accounts, in proportion to the invoice lines (PO line account where matched)."""
    known = set(s.scalars(select(Account.code).where(Account.type == "expense")))
    vendor = s.get(Vendor, inv.vendor_id) if inv.vendor_id else None
    default = vendor.default_gl if vendor and vendor.default_gl in known else DEFAULT_EXPENSE
    po_gl = {pl.id: pl.gl_account for pl in s.scalars(select(PoLine).where(PoLine.po_id == inv.po_id))} \
        if inv.po_id else {}
    matched = {m.invoice_line_id: m.po_line_id for m in s.scalars(select(MatchLine).where(MatchLine.invoice_id == inv.id))}
    weights: dict[str, Decimal] = {}
    for ln in s.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)):
        amount = ln.amount if ln.amount is not None else (
            ln.qty * ln.unit_price if ln.qty is not None and ln.unit_price is not None else Decimal(0))
        if ln.kind == "discount":
            weights[default] = weights.get(default, Decimal(0)) - abs(amount)
            continue
        gl = po_gl.get(matched.get(ln.id)) if ln.kind == "item" else None
        gl = gl if gl in known else default
        weights[gl] = weights.get(gl, Decimal(0)) + abs(amount)
    weights = {g: w for g, w in weights.items() if w > 0}
    if not weights:
        return [(default, net)]
    total = sum(weights.values())
    parts = [(g, _money(net * w / total)) for g, w in sorted(weights.items())]
    gap = net - sum(p for _, p in parts)
    biggest = max(range(len(parts)), key=lambda k: parts[k][1])
    parts[biggest] = (parts[biggest][0], parts[biggest][1] + gap)
    return [(g, p) for g, p in parts if p > 0]


def _line(entry: JournalEntry, account: str, currency: str, *, debit: Decimal = Decimal(0),
          credit: Decimal = Decimal(0), vendor_id: int | None = None) -> JournalLine | None:
    if debit <= 0 and credit <= 0:
        return None
    return JournalLine(entry_id=entry.id, account_code=account, currency=currency, debit=max(debit, Decimal(0)),
                       credit=max(credit, Decimal(0)), vendor_id=vendor_id)


def book_approval(s: Session, approval: Approval, on: dt.date) -> Booking | None:
    """Book an approval (once; booking again returns the first entry)."""
    inv = s.get(Invoice, approval.invoice_id)
    existing = s.scalars(select(JournalEntry).where(JournalEntry.approval_id == approval.id)).first()
    if existing:
        item = s.scalars(select(PaymentItem).where(PaymentItem.approval_id == approval.id)).first()
        return Booking(existing, item.amount if item else Decimal(0))
    amount = _money(approval.amount)
    if amount <= 0 or not approval.currency:
        return None
    tax, wht = _shares(s, inv, approval)
    credit_note = inv.doc_type == "credit_note"
    vendor_name = inv.vendor_name or "unknown vendor"
    entry = JournalEntry(workspace_id=inv.workspace_id, entry_date=on, kind="credit_note" if credit_note else "invoice",
                         entity_id=inv.entity_id, invoice_id=inv.id, approval_id=approval.id,
                         memo=f"{'Credit note' if credit_note else 'Invoice'} {inv.invoice_number or '(no number)'} "
                              f"from {vendor_name}")
    s.add(entry)
    s.flush()
    ccy = approval.currency
    dr, cr = ("credit", "debit") if credit_note else ("debit", "credit")
    rows = [_line(entry, gl, ccy, **{dr: part}) for gl, part in _expense_split(s, inv, amount - tax)]
    rows += [_line(entry, VAT_IN, ccy, **{dr: tax}),
             _line(entry, AP, ccy, **{cr: amount - wht}, vendor_id=inv.vendor_id),
             _line(entry, WHT, ccy, **{cr: wht})]
    s.add_all(r for r in rows if r is not None)
    s.flush()
    payable = amount - wht
    return Booking(entry, -payable if credit_note else payable)


def book_payment(s: Session, item: PaymentItem, run: PaymentRun) -> JournalEntry:
    inv = s.get(Invoice, item.invoice_id)
    entry = JournalEntry(workspace_id=inv.workspace_id, entry_date=run.run_date, kind="payment",
                         entity_id=inv.entity_id, invoice_id=inv.id, payment_item_id=item.id,
                         memo=f"Payment run {run.run_date}: {inv.invoice_number or '(no number)'} "
                              f"to {inv.vendor_name or 'unknown vendor'}"
                              + (" (credit note subtracted)" if item.amount < 0 else ""))
    s.add(entry)
    s.flush()
    ccy = item.currency
    if item.amount > 0:
        rows = [_line(entry, AP, ccy, debit=item.amount, vendor_id=item.vendor_id),
                _line(entry, CASH, ccy, credit=item.amount - item.discount),
                _line(entry, DISCOUNTS, ccy, credit=item.discount)]
    else:
        rows = [_line(entry, CASH, ccy, debit=-item.amount),
                _line(entry, AP, ccy, credit=-item.amount, vendor_id=item.vendor_id)]
    s.add_all(r for r in rows if r is not None)
    s.flush()
    return entry
