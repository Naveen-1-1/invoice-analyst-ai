"""The AP checks on small, hand-built workspaces: matching, duplicates, three-way match, bank and addressee."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from invoice_analyst.checks.engine import check_invoice
from invoice_analyst.checks.normalize import norm_name, same_legal_form, tax_digits

from .conftest import make_invoice


def test_names_tax_ids_and_legal_forms() -> None:
    assert norm_name("Okavango Hardware (Pty) Ltd") == "okavango hardware"
    assert not same_legal_form("Karoo Hardware CC", "Karoo Hardware (Pty) Ltd")
    assert same_legal_form("Acme Industrial Supply Co", "ACME Industrial Supply Company")
    assert same_legal_form("Acme Industrial Supply", "Acme Industrial Supply Co")  # no form printed: compatible
    assert tax_digits("VAT Reg No: 4362204999") == tax_digits("4362204999")


def raised(s: Session, wid: int, **kw) -> dict[str, str]:
    """Findings that need attention (review or block), by code; information-only notes are left out."""
    inv, doc = make_invoice(s, wid, **kw)
    return {f.code: f.severity for f in check_invoice(s, inv, doc, dt.date(2026, 1, 20))
            if f.outcome in ("fail", "warn") and f.severity != "info"}


def test_clean_invoice_raises_nothing(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        assert raised(s, wid) == {}


def test_price_tolerance_is_two_percent_or_fifty_cents(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        assert "match.price_over_po" not in raised(s, wid, number="A1", qty=1, price=102.0)  # +2%: allowed
    with sessions.begin() as s:
        assert raised(s, wid, number="B1", qty=1, price=102.5)["match.price_over_po"] == "review"  # +2.5%


def test_a_delivery_can_only_be_invoiced_once(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        assert "match.qty_over_received" not in raised(s, wid, number="P1", qty=6)
        assert "match.qty_over_received" not in raised(s, wid, number="P2", qty=4)
        assert raised(s, wid, number="P3", qty=1)["match.qty_over_received"] == "review"  # all 10 already invoiced


def test_duplicates_by_number_amount_and_content(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        raised(s, wid, number="D1", qty=1, date="2026-01-01")
        assert raised(s, wid, number="D1", qty=1, po=None, date="2026-02-20")["dup.vendor_number"] == "block"
        assert "dup.same_amount" in raised(s, wid, number="D2", qty=1, po=None, date="2026-01-31")  # 30 days later
    with sessions.begin() as s:
        raised(s, wid, number="E1", qty=2, service=700.0, date="2026-01-01")
        r = raised(s, wid, number="E2", qty=2, service=700.0, po=None, date="2026-03-15")  # 73 days: not same_amount
        assert "dup.same_amount" not in r and "dup.same_content" in r


def test_vendor_bank_and_addressee(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        assert "bank.changed" not in raised(s, wid, number="B1", bank="1234567890")
        assert raised(s, wid, number="B2", bank="9999999999")["bank.changed"] == "block"
        assert raised(s, wid, number="B3", vendor="Unheard Of Traders CC", tax_id=None)["vendor.unknown"] == "review"
        assert raised(s, wid, number="B4", tax_id="4123456780")["vendor.tax_id_mismatch"] == "review"
        assert "pattern.wrong_addressee" in raised(s, wid, number="B5", bill_to="Our Company CC")  # another legal form
        assert raised(s, wid, number="B6", po=None)["match.no_po"] == "review"
