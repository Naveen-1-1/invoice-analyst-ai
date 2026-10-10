"""When to pay (decision 12): weekly runs on Thursday; the last run before the due date, or before the
early-payment discount deadline when that run can still be made; overdue invoices go in the next run.

Pure date logic, no database: `plan` takes the first run that can still take items (`first`, a Thursday).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

RUN_WEEKDAY = 3  # Thursday (Monday = 0)
ASSUMED_TERMS_DAYS = 30
_ON_RECEIPT = re.compile(r"\b(due\s+(up)?on\s+receipt|on\s+receipt|immediate(ly)?|cod|cash\s+on\s+delivery)\b",
                         re.IGNORECASE)
_DAYS = re.compile(r"(?:net\s*(\d{1,3}))|(?:(\d{1,3})\s*(?:days?|d\b))", re.IGNORECASE)


def next_run(d: dt.date) -> dt.date:
    """The first Thursday on or after d."""
    return d + dt.timedelta(days=(RUN_WEEKDAY - d.weekday()) % 7)


def last_run(d: dt.date) -> dt.date:
    """The last Thursday on or before d."""
    return d - dt.timedelta(days=(d.weekday() - RUN_WEEKDAY) % 7)


def terms_days(text: str | None) -> int | None:
    """'30 days', 'Net 15' -> days; 'Due on receipt', 'COD' -> 0; unreadable -> None."""
    if not text:
        return None
    if _ON_RECEIPT.search(text):
        return 0
    m = _DAYS.search(text)
    return int(m.group(1) or m.group(2)) if m else None


@dataclass(frozen=True)
class Due:
    date: dt.date
    source: str  # printed / terms / vendor_terms / assumed

    def words(self) -> str:
        return {"printed": "printed on the invoice", "terms": "from the payment terms printed",
                "vendor_terms": "from the vendor's usual terms",
                "assumed": f"assumed: no due date or terms, so {ASSUMED_TERMS_DAYS} days"}[self.source]


def due_date(printed: dt.date | None, invoice_date: dt.date | None, terms_text: str | None,
             vendor_terms_days: int | None, today: dt.date) -> Due:
    """The printed due date; else invoice date + the printed terms; else + the vendor's terms; else + 30 days."""
    if printed:
        return Due(printed, "printed")
    base = invoice_date or today
    days = terms_days(terms_text)
    if days is not None:
        return Due(base + dt.timedelta(days=days), "terms")
    if vendor_terms_days is not None:
        return Due(base + dt.timedelta(days=vendor_terms_days), "vendor_terms")
    return Due(base + dt.timedelta(days=ASSUMED_TERMS_DAYS), "assumed")


@dataclass(frozen=True)
class Plan:
    run_date: dt.date
    why: str  # discount / before_due / overdue
    take_discount: bool


def plan(first: dt.date, due: dt.date, discount_by: dt.date | None = None) -> Plan:
    """The run for an invoice, given the first run still open (a Thursday)."""
    assert first.weekday() == RUN_WEEKDAY, first
    if discount_by is not None and last_run(discount_by) >= first:
        return Plan(last_run(discount_by), "discount", True)
    if last_run(due) >= first:
        return Plan(last_run(due), "before_due", False)
    return Plan(first, "overdue", False)


def explain(p: Plan, due: Due, discount_pct: float | None = None, discount_by: dt.date | None = None,
            today: dt.date | None = None) -> str:
    when = f"Thu {p.run_date:%d %b %Y}"
    if p.why == "discount":
        return (f"{discount_pct:g}% early-payment discount if paid by {discount_by:%d %b %Y}: paid in the run of "
                f"{when} (due {due.date:%d %b %Y}, {due.words()}).")
    if p.why == "before_due":
        return f"Due {due.date:%d %b %Y} ({due.words()}): the last run before then is {when}."
    if today is not None and due.date < today:
        return f"Due {due.date:%d %b %Y} ({due.words()}), already passed: paid in the next run, {when}."
    return f"Due {due.date:%d %b %Y} ({due.words()}), before the next run: paid in that run, {when}."
