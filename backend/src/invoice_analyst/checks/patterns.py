"""Unusual patterns: amount far above the vendor's usual (decision 30), future date, round total, stamps."""

from __future__ import annotations

import re
import statistics

from sqlalchemy import select

from ..db.models import VendorHistory
from .base import Context, Finding

ABOVE_HISTORY_FACTOR = 3.0
MIN_HISTORY = 3
ROUND_TOTAL_MIN = 1000
STAMP_WORDS = re.compile(r"\b(paid|approved|received|posted)\b", re.IGNORECASE)


def patterns(ctx: Context) -> list[Finding]:
    out: list[Finding] = []
    total = float(ctx.invoice.total) if ctx.invoice.total is not None else None
    if ctx.vendor is not None and total is not None:
        past = [float(h.amount) for h in ctx.s.scalars(select(VendorHistory).where(
            VendorHistory.vendor_id == ctx.vendor.id, VendorHistory.currency == ctx.invoice.currency)).all()]
        if len(past) >= MIN_HISTORY:
            median = statistics.median(past)
            if median > 0 and total > ABOVE_HISTORY_FACTOR * median:
                out.append(Finding("pattern.above_history", "fail",
                                   f"{total:,.2f} is {total / median:.1f}x this vendor's usual invoice "
                                   f"({median:,.2f}, the median of {len(past)} past invoices).",
                                   {"median": round(median, 2), "past": len(past), "ratio": round(total / median, 2)}))
    if ctx.invoice.invoice_date and ctx.invoice.invoice_date > ctx.received:
        out.append(Finding("pattern.future_dated", "fail", f"Dated {ctx.invoice.invoice_date}, after it arrived "
                                                           f"({ctx.received})."))
    if total is not None and total >= ROUND_TOTAL_MIN and total % 100 == 0:
        out.append(Finding("pattern.round_total", "warn", f"A round total ({total:,.0f}); fine on its own, worth a "
                                                          "glance alongside anything else."))
    stamps = [a for a in ctx.inv.annotations if STAMP_WORDS.search(a)]
    if stamps:
        out.append(Finding("pattern.stamp", "fail", f"Marked {', '.join(repr(a) for a in stamps)} on the document: "
                                                    "check it hasn't already been paid or processed.",
                           {"annotations": stamps}))
    return out
