"""Re-reading printed amounts and dates, and resolving ambiguous dates by rule."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from invoice_analyst.extraction import normalize as n


@pytest.mark.parametrize("text, expected", [
    ("1,234.56", "1234.56"), ("1.234,56", "1234.56"), ("1 234,56", "1234.56"), ("1 234.56", "1234.56"),
    ("N$ 3 247,51", "3247.51"), ("R1,629.70", "1629.70"), ("€ 162,48", "162.48"), ("(1,234.56)", "1234.56"),
    ("-96.00", "96.00"), ("1,234", "1234"), ("12,5", "12.5"), ("USD 1,000,000.00", "1000000.00"),
    ("", None), ("n/a", None),
])
def test_parse_amount(text: str, expected: str | None) -> None:
    assert n.parse_amount(text) == (Decimal(expected) if expected else None)


@pytest.mark.parametrize("text, order, expected, ambiguous", [
    ("2026-03-04", "dmy", dt.date(2026, 3, 4), False),
    ("2026/03/04", "mdy", dt.date(2026, 3, 4), False),
    ("04/03/2026", "dmy", dt.date(2026, 3, 4), True),
    ("04/03/2026", "mdy", dt.date(2026, 4, 3), True),
    ("23/09/2025", "mdy", dt.date(2025, 9, 23), False),  # only one reading is a real date
    ("04.03.2026", "dmy", dt.date(2026, 3, 4), True),
    ("4 March 2026", "mdy", dt.date(2026, 3, 4), False),
    ("04-Mar-2026", "mdy", dt.date(2026, 3, 4), False),
    ("March 4, 2026", "dmy", dt.date(2026, 3, 4), False),
    ("05/05/2026", "dmy", dt.date(2026, 5, 5), False),  # same either way
])
def test_parse_date(text: str, order: str, expected: dt.date, ambiguous: bool) -> None:
    assert n.parse_date(text, order) == (expected, ambiguous)


def test_date_order_from_another_date_on_the_document() -> None:
    assert n.resolve_date_order(["04/03/2026", "18/04/2026"], "US") == ("dmy", "document")
    assert n.resolve_date_order(["04/03/2026", "04/18/2026"], "GB") == ("mdy", "document")


def test_date_order_from_terms() -> None:
    # 01/02 + 30 days = 03/03 only when read day-first (1 Feb -> 3 Mar)
    assert n.resolve_date_order(["01/02/2026", "03/03/2026"], "US", "01/02/2026", "03/03/2026",
                                "Net 30") == ("dmy", "terms")


def test_date_order_from_country() -> None:
    assert n.resolve_date_order(["04/03/2026"], "US") == ("mdy", "country")
    assert n.resolve_date_order(["04/03/2026"], "ZA") == ("dmy", "country")


@pytest.mark.parametrize("text, expected", [
    ("Invoice No: INV-2026-70022", "INV-2026-70022"), ("# 100 401", "100401"), ("Tax Invoice 7781", "7781"),
    (None, None), ("  ", None),
])
def test_norm_invoice_number(text: str | None, expected: str | None) -> None:
    assert n.norm_invoice_number(text) == expected
