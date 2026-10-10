"""Three-way match: the invoice against the purchase order (what we ordered) and goods receipts (what arrived).

Decisions 9-10: unit price may exceed the PO by max(2%, 0.50 per unit); never pay for more than was received and
not yet invoiced; lines without a quantity (services) get a two-way amount check; an invoice without a PO goes to
review. Each matched line is stored (match_lines); its approved quantity uses up the receipt, so the same delivery
can't be paid twice.
"""

from __future__ import annotations

from decimal import Decimal

from rapidfuzz import fuzz
from sqlalchemy import func, select

from ..db.models import GoodsReceipt, GoodsReceiptLine, Invoice, InvoiceLine, MatchLine, PoLine, PurchaseOrder
from .base import Context, Finding
from .normalize import norm_code

PRICE_TOLERANCE_PCT = Decimal("0.02")
PRICE_TOLERANCE_ABS = Decimal("0.50")
CHARGES_MAX_SHARE = Decimal("0.01")
DESCRIPTION_MATCH_MIN = 85


def qty_text(q: Decimal) -> str:
    """A quantity for people: 3, 2.5 (the database keeps six decimals)."""
    return format(q.normalize(), "f")


def _allowed(base: Decimal) -> Decimal:
    return max(PRICE_TOLERANCE_PCT * abs(base), PRICE_TOLERANCE_ABS)


def _received_available(ctx: Context, po_line: PoLine) -> Decimal:
    received = ctx.s.scalar(select(func.coalesce(func.sum(GoodsReceiptLine.qty), 0))
                            .where(GoodsReceiptLine.po_line_id == po_line.id)) or Decimal(0)
    used = ctx.s.scalar(select(func.coalesce(func.sum(MatchLine.qty_approved), 0))
                        .join(Invoice, Invoice.id == MatchLine.invoice_id)
                        .where(MatchLine.po_line_id == po_line.id, MatchLine.invoice_id != ctx.invoice.id,
                               Invoice.state != "REJECTED")) or Decimal(0)
    return Decimal(received) - Decimal(used)


def _pair(lines: list[InvoiceLine], po_lines: list[PoLine]) -> list[tuple[InvoiceLine, PoLine | None]]:
    """Pair invoice lines with PO lines: by item code, then description, then amount (each PO line once)."""
    free = list(po_lines)
    pairs: list[tuple[InvoiceLine, PoLine | None]] = []
    for ln in lines:
        match = next((p for p in free if ln.item_code and p.item_code and norm_code(ln.item_code) == norm_code(p.item_code)),
                     None)
        if match is None and ln.description:
            scored = [(p, fuzz.token_set_ratio(ln.description.casefold(), (p.description or "").casefold())) for p in free]
            best = max(scored, key=lambda x: x[1], default=(None, 0))
            match = best[0] if best[1] >= DESCRIPTION_MATCH_MIN else None
        if match is None and ln.amount is not None:
            match = next((p for p in free if p.amount is not None and abs(p.amount - ln.amount) <= Decimal("0.01")), None)
        if match is not None:
            free.remove(match)
        pairs.append((ln, match))
    return pairs


def three_way(ctx: Context) -> list[Finding]:
    if ctx.inv.document_type != "invoice":
        return [Finding("match.three_way", "skip", "Only invoices are matched to purchase orders.")]
    printed = ctx.inv.po_number.text
    if printed and not any(ch.isdigit() for ch in printed):
        printed = None  # PO numbers carry digits; letters alone are a logo or initials (as with invoice numbers)
    if not printed:
        return [Finding("match.no_po", "fail", "No PO number on the invoice: approve it as a one-off expense, or ask "
                                               "the vendor which order it is for.")]
    po = ctx.s.scalars(select(PurchaseOrder).where(PurchaseOrder.workspace_id == ctx.workspace_id,
                                                   PurchaseOrder.po_number_norm == norm_code(printed))).first()
    if po is None:
        return [Finding("match.po_not_found", "fail", f"PO {printed} is not in our purchase orders.", {"po": printed})]
    ctx.po = po
    out: list[Finding] = []
    if ctx.vendor is not None and po.vendor_id not in (None, ctx.vendor.id):
        out.append(Finding("match.po_vendor_mismatch", "fail", f"PO {po.po_number} was raised with a different vendor.",
                           {"po": po.po_number}))

    po_lines = ctx.s.scalars(select(PoLine).where(PoLine.po_id == po.id).order_by(PoLine.line_no)).all()
    inv_lines = ctx.s.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == ctx.invoice.id,
                                                       InvoiceLine.kind == "item").order_by(InvoiceLine.line_no)).all()
    receipts = ctx.s.scalar(select(func.count()).select_from(GoodsReceipt).where(GoodsReceipt.po_id == po.id)) or 0
    over_price, over_qty, unmatched = [], [], []
    for ln, pl in _pair(inv_lines, po_lines):
        if pl is None:
            unmatched.append(ln.description or f"line {ln.line_no}")
            ctx.s.add(MatchLine(invoice_id=ctx.invoice.id, invoice_line_id=ln.id, qty_invoiced=ln.qty,
                                price_inv=ln.unit_price, outcome="unmatched"))
            continue
        if pl.qty is None:  # services: compare amounts only
            outcome = "two_way"
            if ln.amount is not None and pl.amount is not None and ln.amount - pl.amount > _allowed(pl.amount):
                outcome = "price_variance"
                over_price.append(f"{ln.description}: {ln.amount:,.2f} vs {pl.amount:,.2f} ordered")
            ctx.s.add(MatchLine(invoice_id=ctx.invoice.id, invoice_line_id=ln.id, po_line_id=pl.id,
                                price_inv=ln.amount, price_po=pl.amount, outcome=outcome))
            continue
        qty = ln.qty if ln.qty is not None else Decimal(1)
        price = ln.unit_price if ln.unit_price is not None else (ln.amount / qty if ln.amount is not None and qty else None)
        outcome = "match"
        variance = None
        if price is not None and pl.unit_price is not None:
            variance = (price - pl.unit_price) / pl.unit_price if pl.unit_price else None
            if price - pl.unit_price > _allowed(pl.unit_price):
                outcome = "price_variance"
                over_price.append(f"{ln.description}: {price:,.2f} each vs {pl.unit_price:,.2f} on the PO "
                                  f"({variance:+.1%})" if variance is not None else ln.description)
        available = _received_available(ctx, pl)
        if qty > available:
            outcome = "qty_over_received" if outcome == "match" else outcome
            over_qty.append(f"{ln.description}: {qty_text(qty)} invoiced, {qty_text(max(available, Decimal(0)))} "
                            "received and not yet invoiced")
        ctx.s.add(MatchLine(invoice_id=ctx.invoice.id, invoice_line_id=ln.id, po_line_id=pl.id, qty_invoiced=qty,
                            qty_received_avail=available, qty_approved=max(Decimal(0), min(qty, available)),
                            price_inv=price, price_po=pl.unit_price,
                            variance_pct=round(variance * 100, 4) if variance is not None else None, outcome=outcome))
        ctx.s.flush()  # later lines and invoices see this line's use of the receipt

    if over_price:
        out.append(Finding("match.price_over_po", "fail", "Charged more than the PO price: " + "; ".join(over_price),
                           {"po": po.po_number, "lines": over_price}))
    if over_qty:
        out.append(Finding("match.qty_over_received", "fail",
                           ("More invoiced than delivered: " if receipts else "Nothing has been received on this PO yet: ")
                           + "; ".join(over_qty) + ". Pay only for what arrived; hold the rest.",
                           {"po": po.po_number, "lines": over_qty}))
    if unmatched:
        out.append(Finding("match.line_not_on_po", "fail", "Not on the PO: " + "; ".join(unmatched),
                           {"po": po.po_number, "lines": unmatched}))
    charges = sum((c.amount for c in ctx.inv.charges if c.kind != "discount"), 0.0)
    total = ctx.invoice.total
    if charges and total and Decimal(str(charges)) > CHARGES_MAX_SHARE * total:
        out.append(Finding("match.charges_not_on_po", "warn", f"Charges of {charges:,.2f} (shipping, fees) are not on "
                                                              f"the PO and are more than 1% of the total.",
                           {"charges": charges}))
    if not out:
        out.append(Finding("match.three_way", "pass", f"Matches PO {po.po_number} and what was received.",
                           {"po": po.po_number}))
    return out
