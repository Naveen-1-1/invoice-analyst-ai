"""The AP checks on small, hand-built workspaces: matching, duplicates, three-way match, bank and addressee."""

from __future__ import annotations

import datetime as dt
import itertools
from decimal import Decimal

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from invoice_analyst.checks.engine import check_invoice
from invoice_analyst.checks.normalize import norm_name, same_legal_form, tax_digits
from invoice_analyst.db.models import (Document, Entity, Extraction, GoodsReceipt, GoodsReceiptLine, Invoice,
                                       InvoiceLine, PoLine, PurchaseOrder, Vendor, VendorBankAccount, Workspace)
from invoice_analyst.extraction.resolve import resolve
from invoice_analyst.extraction.schemas import FileExtraction

_seq = itertools.count(1)


def test_names_tax_ids_and_legal_forms() -> None:
    assert norm_name("Okavango Hardware (Pty) Ltd") == "okavango hardware"
    assert not same_legal_form("Karoo Hardware CC", "Karoo Hardware (Pty) Ltd")
    assert same_legal_form("Acme Industrial Supply Co", "ACME Industrial Supply Company")
    assert same_legal_form("Acme Industrial Supply", "Acme Industrial Supply Co")  # no form printed: compatible
    assert tax_digits("VAT Reg No: 4362204999") == tax_digits("4362204999")


@pytest.fixture
def ws(engine: Engine):
    """A workspace with one vendor (bank account on file), one of our companies, and PO-100: 10 boxes at 100.00
    (all received) plus a service line of 500.00."""
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as s:
        w = Workspace(name=f"checks-{next(_seq)}-{dt.datetime.now().timestamp()}", kind="inbox", dataset="user",
                      settings={})
        s.add(w)
        s.flush()
        v = Vendor(workspace_id=w.id, code="V0001", name="Acme Supplies (Pty) Ltd", name_norm=norm_name("Acme Supplies (Pty) Ltd"),
                   aliases=[], country="ZA", tax_id="4123456789", tax_id_norm="4123456789", status="active")
        s.add(v)
        s.add(Entity(workspace_id=w.id, name="Our Company (Pty) Ltd", aliases=[], country="ZA"))
        s.flush()
        s.add(VendorBankAccount(vendor_id=v.id, kind="account", account_norm="1234567890"))
        po = PurchaseOrder(workspace_id=w.id, po_number="PO-100", po_number_norm="PO100", vendor_id=v.id,
                           currency="ZAR", order_date=dt.date(2026, 1, 1), status="open")
        s.add(po)
        s.flush()
        boxes = PoLine(po_id=po.id, line_no=1, item_code="BOX", description="Storage boxes", qty=Decimal(10),
                       unit_price=Decimal("100.00"), amount=Decimal("1000.00"))
        service = PoLine(po_id=po.id, line_no=2, description="Installation service", amount=Decimal("500.00"))
        s.add_all([boxes, service])
        s.flush()
        gr = GoodsReceipt(po_id=po.id, grn_number="GRN-1", received_date=dt.date(2026, 1, 10))
        s.add(gr)
        s.flush()
        s.add(GoodsReceiptLine(goods_receipt_id=gr.id, po_line_id=boxes.id, qty=Decimal(10)))
        wid = w.id
    return sessions, wid


def make_invoice(s: Session, wid: int, *, number: str = "INV-1", vendor: str = "Acme Supplies (Pty) Ltd",
                 tax_id: str | None = "4123456789", bill_to: str = "Our Company (Pty) Ltd", po: str | None = "PO-100",
                 qty: float = 10, price: float = 100.0, service: float = 500.0, date: str = "2026-01-15",
                 bank: str | None = None) -> tuple[Invoice, Document]:
    seq = next(_seq)
    lines = [{"description": "Storage boxes", "item_code": "BOX", "quantity": qty, "unit_price": price,
              "amount": round(qty * price, 2)},
             {"description": "Installation service", "amount": service}]
    subtotal = round(qty * price + service, 2)
    raw = {"invoices": [{
        "document_type": "invoice", "vendor": {"name": {"text": vendor}, "country": "ZA", "tax_id": {"text": tax_id},
                                               "bank": {"account_number": bank} if bank else None},
        "bill_to": {"name": {"text": bill_to}}, "invoice_number": {"text": number},
        "invoice_date": {"text": date, "value": date}, "po_number": {"text": po}, "currency": "ZAR", "lines": lines,
        "subtotal": {"value": subtotal}, "tax_lines": [], "total": {"value": subtotal}}]}
    ext_model = FileExtraction.model_validate(raw)
    doc = Document(workspace_id=wid, sha256=f"sha-{seq}", filename=f"inv-{seq}.pdf", mime="application/pdf", bytes=1,
                   feed_seq=seq, received_at=dt.datetime(2026, 1, 20, tzinfo=dt.timezone.utc), state="EXTRACTED",
                   storage_path="/dev/null", route="text_llm")
    s.add(doc)
    s.flush()
    ext = Extraction(document_id=doc.id, route="text_llm", model="m", prompt_hash="p", schema_ver="2",
                     preprocess_ver="x", status="ok", raw=ext_model.model_dump())
    s.add(ext)
    s.flush()
    r = resolve(ext_model.invoices[0])
    inv = Invoice(workspace_id=wid, document_id=doc.id, extraction_id=ext.id, child_index=0, doc_type="invoice",
                  state="EXTRACTED", vendor_name=vendor, currency="ZAR", invoice_number=number,
                  invoice_date=dt.date.fromisoformat(date), total=Decimal(str(subtotal)))
    s.add(inv)
    s.flush()
    for n, ln in enumerate(ext_model.invoices[0].lines, 1):
        s.add(InvoiceLine(invoice_id=inv.id, line_no=n, kind="item", description=ln.description, item_code=ln.item_code,
                          qty=None if ln.quantity is None else Decimal(str(ln.quantity)),
                          unit_price=None if ln.unit_price is None else Decimal(str(ln.unit_price)),
                          amount=Decimal(str(ln.amount))))
    s.flush()
    assert r.invoice_number == number
    return inv, doc


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
