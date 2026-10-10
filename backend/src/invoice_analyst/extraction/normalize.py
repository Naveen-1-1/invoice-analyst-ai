"""Deterministic re-reading of what the model quoted: amounts, dates, invoice numbers.

The model returns both the printed text and its own normalised value. These functions re-parse the text so that
disagreements can be noticed (and later lower confidence), and so ambiguous numeric dates such as 03/04/2026 are
resolved by explicit rules instead of by the model:
  1. another numeric date on the same document whose day part is above 12 fixes the order;
  2. otherwise the order under which issue date + payment terms = due date;
  3. otherwise the vendor country's convention (US month-first, everyone else day-first).
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from dateutil import parser as date_parser

MONTH_FIRST_COUNTRIES = {"US"}

_NUMERIC_DATE = re.compile(r"^\s*(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})\s*$")
_TERMS_DAYS = re.compile(r"(\d{1,3})\s*(?:days?|d\b)", re.IGNORECASE)
_NET_DAYS = re.compile(r"net\s*(\d{1,3})", re.IGNORECASE)


def parse_amount(text: str | None) -> Decimal | None:
    """'N$ 1 234,56' -> 1234.56, '(1,234.56)' -> 1234.56 (magnitude), '1.234,56' -> 1234.56."""
    if not text:
        return None
    s = re.sub(r"[^\d.,'\s  -]", "", text).strip().strip("-").strip()
    s = re.sub(r"[\s  ']", "", s)
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        decimal = "," if s.rfind(",") > s.rfind(".") else "."
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        tail = s.rsplit(sep, 1)[1]
        # a single separator followed by exactly 3 digits is a thousands separator ("1,234"); otherwise decimal
        decimal = sep if (s.count(sep) == 1 and len(tail) != 3) else None
    else:
        decimal = None
    thousands = {",", "."} - ({decimal} if decimal else set())
    for t in thousands:
        s = s.replace(t, "")
    if decimal == ",":
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def date_order_for_country(country: str | None) -> str:
    return "mdy" if (country or "").upper() in MONTH_FIRST_COUNTRIES else "dmy"


def numeric_date_parts(text: str | None) -> tuple[int, int, int] | None:
    m = _NUMERIC_DATE.match(text or "")
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def _year(y: int) -> int:
    return y + 2000 if y < 100 else y


def parse_date(text: str | None, order: str = "dmy") -> tuple[dt.date | None, bool]:
    """(date, ambiguous). `ambiguous` is True when a numeric date could be read both ways and `order` decided it."""
    if not text or not text.strip():
        return None, False
    parts = numeric_date_parts(text)
    if parts:
        a, b, c = parts
        if a > 31:  # year first: Y/M/D
            try:
                return dt.date(a, b, c), False
            except ValueError:
                return None, False
        year = _year(c)
        candidates = {}
        for name, (d, m) in {"dmy": (a, b), "mdy": (b, a)}.items():
            try:
                candidates[name] = dt.date(year, m, d)
            except ValueError:
                pass
        if not candidates:
            return None, False
        if len(candidates) == 1:
            return next(iter(candidates.values())), False
        same = candidates["dmy"] == candidates["mdy"]
        return candidates[order], not same
    try:
        return date_parser.parse(text, dayfirst=(order == "dmy"), fuzzy=True).date(), False
    except (ValueError, OverflowError):
        return None, False


def terms_days(text: str | None) -> int | None:
    if not text:
        return None
    m = _NET_DAYS.search(text) or _TERMS_DAYS.search(text)
    return int(m.group(1)) if m else None


def resolve_date_order(date_texts: list[str | None], country: str | None,
                       issue_text: str | None = None, due_text: str | None = None,
                       terms_text: str | None = None) -> tuple[str, str]:
    """(order, how) where how is 'document', 'terms' or 'country'."""
    for text in date_texts:
        parts = numeric_date_parts(text)
        if parts and parts[0] <= 31:
            if parts[0] > 12 >= parts[1]:
                return "dmy", "document"
            if parts[1] > 12 >= parts[0]:
                return "mdy", "document"
    days = terms_days(terms_text)
    if days is not None and issue_text and due_text:
        fits = []
        for order in ("dmy", "mdy"):
            issue, _ = parse_date(issue_text, order)
            due, _ = parse_date(due_text, order)
            if issue and due and (due - issue).days == days:
                fits.append(order)
        if len(fits) == 1:
            return fits[0], "terms"
    return date_order_for_country(country), "country"


def norm_invoice_number(text: str | None) -> str | None:
    """Uppercase, no spaces or '#'/':' and no leading 'Invoice No' label: for duplicate detection."""
    if not text:
        return None
    s = re.sub(r"^\s*(tax\s+)?invoice\s*(no\.?|number|#)?\s*[:#]?\s*", "", text, flags=re.IGNORECASE)
    s = re.sub(r"[\s#:]", "", s).upper()
    return s or None
