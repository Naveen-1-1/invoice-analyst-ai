"""Have we seen this invoice before? Compared with earlier documents in the same inbox and the vendor's history.

  dup.same_file      the very same file again                                        (block)
  dup.vendor_number  same vendor and invoice number                                  (block)
  dup.same_amount    same vendor and total, invoice dates within 30 days (decision 29)
  dup.same_content   same vendor, total and every line amount, whatever the date or number (decision 31: replaces
                     an image fingerprint, which on dev confused unrelated invoices sharing a layout)
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from ..db.models import Document, Invoice, InvoiceLine, VendorHistory
from .base import Context, Finding
from .normalize import norm_code, norm_name

SAME_AMOUNT_DAYS = 30


def _earlier_documents(ctx: Context) -> list[Document]:
    return ctx.s.scalars(select(Document).where(Document.workspace_id == ctx.workspace_id,
                                                Document.feed_seq < ctx.document.feed_seq)).all()


def _earlier_invoices(ctx: Context) -> list[tuple[Invoice, Document]]:
    """Invoices that arrived before this one (earlier documents, or earlier in the same file)."""
    rows = ctx.s.execute(select(Invoice, Document).join(Document, Document.id == Invoice.document_id).where(
        Invoice.workspace_id == ctx.workspace_id, Invoice.id != ctx.invoice.id)).all()
    seq = ctx.document.feed_seq
    return [(i, d) for i, d in rows if d.feed_seq < seq or (d.id == ctx.document.id and i.child_index < ctx.invoice.child_index)]


def _same_vendor(ctx: Context, other: Invoice) -> bool:
    if ctx.vendor is not None:
        return other.vendor_id == ctx.vendor.id
    # vendor unknown on both: compare the printed names
    return other.vendor_id is None and bool(norm_name(ctx.r.vendor_name)) and \
        norm_name(ctx.r.vendor_name) == norm_name(other.vendor_name)


def _line_amounts(ctx: Context, invoice_id: int) -> Counter:
    rows = ctx.s.scalars(select(InvoiceLine.amount).where(InvoiceLine.invoice_id == invoice_id,
                                                         InvoiceLine.kind == "item")).all()
    return Counter(round(float(a), 2) for a in rows if a is not None)


def duplicates(ctx: Context) -> list[Finding]:
    out: list[Finding] = []
    for doc in _earlier_documents(ctx):
        if doc.sha256 == ctx.document.sha256:
            out.append(Finding("dup.same_file", "fail", f"The same file arrived before ({doc.filename}, item "
                                                        f"{doc.feed_seq}).", {"earlier_document": doc.id}))
            break

    number = norm_code(ctx.r.invoice_number)
    total = float(ctx.invoice.total) if ctx.invoice.total is not None else None
    date = ctx.invoice.invoice_date
    for other, doc in _earlier_invoices(ctx):
        if not _same_vendor(ctx, other):
            continue
        if number and norm_code(other.invoice_number) == number:
            out.append(Finding("dup.vendor_number", "fail",
                               f"Invoice {ctx.r.invoice_number} from this vendor arrived before (item {doc.feed_seq}).",
                               {"earlier_document": doc.id}, related_invoice_id=other.id))
            break
        if (total is not None and other.total is not None and abs(float(other.total) - total) <= 0.01 and date
                and other.invoice_date and abs((date - other.invoice_date).days) <= SAME_AMOUNT_DAYS):
            out.append(Finding("dup.same_amount", "fail",
                               f"Same vendor and the same total ({total:,.2f}) as invoice {other.invoice_number or '?'} "
                               f"(item {doc.feed_seq}), {abs((date - other.invoice_date).days)} days apart, under a "
                               "different number: possibly the same bill sent again.",
                               {"earlier_document": doc.id}, related_invoice_id=other.id))
            break

    if ctx.vendor is not None and not any(f.code == "dup.vendor_number" for f in out):
        for h in ctx.s.scalars(select(VendorHistory).where(VendorHistory.vendor_id == ctx.vendor.id)).all():
            if number and norm_code(h.invoice_number) == number:
                out.append(Finding("dup.vendor_number", "fail", f"Invoice {ctx.r.invoice_number} is already in this "
                                                                f"vendor's history (dated {h.invoice_date}).", {"history": h.id}))
                break
            if (total is not None and abs(float(h.amount) - total) <= 0.01 and date
                    and abs((date - h.invoice_date).days) <= SAME_AMOUNT_DAYS):
                out.append(Finding("dup.same_amount", "fail", f"Same total ({total:,.2f}) as invoice {h.invoice_number} "
                                                              f"in this vendor's history, within {SAME_AMOUNT_DAYS} days.",
                                   {"history": h.id}))
                break

    if not any(f.code in ("dup.vendor_number", "dup.same_amount") for f in out):
        mine = _line_amounts(ctx, ctx.invoice.id)
        for other, doc in _earlier_invoices(ctx) if mine and total is not None else []:
            if (_same_vendor(ctx, other) and other.total is not None and abs(float(other.total) - total) <= 0.01
                    and _line_amounts(ctx, other.id) == mine):
                out.append(Finding("dup.same_content", "fail",
                                   f"Same vendor, total and line amounts as invoice {other.invoice_number or '?'} "
                                   f"(item {doc.feed_seq}), under {'a different number' if number else 'no number'}: "
                                   "possibly the same bill sent again.",
                                   {"earlier_document": doc.id}, related_invoice_id=other.id))
                break
    if not out:
        out.append(Finding("dup.none", "pass", "Not seen before: no earlier invoice with this number, amount or page."))
    return out
