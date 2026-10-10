"""Shared fixtures. Database tests run against a throwaway database, migrated from scratch for each session."""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from invoice_analyst.checks.normalize import norm_name
from invoice_analyst.config import settings
from invoice_analyst.db.models import (Document, Entity, Extraction, GoodsReceipt, GoodsReceiptLine, Invoice,
                                       InvoiceLine, PoLine, PurchaseOrder, Vendor, VendorBankAccount, Workspace)
from invoice_analyst.extraction.resolve import resolve
from invoice_analyst.extraction.schemas import FileExtraction

_seq = itertools.count(1)

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    """URL of a fresh, fully migrated test database (dropped afterwards)."""
    url = make_url(settings().database_url).set(database=f"{make_url(settings().database_url).database}_test")
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{url.database}"')
    rendered = url.render_as_string(hide_password=False)
    cfg = Config(BACKEND / "alembic.ini")
    cfg.set_main_option("sqlalchemy.url", rendered)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    yield rendered
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')


@pytest.fixture(scope="session")
def engine(db_url: str) -> Iterator[Engine]:
    eng = create_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def pg(db_url: str) -> Iterator[psycopg.Connection]:
    """A raw psycopg connection to the test database (for LISTEN/NOTIFY and commit-time checks)."""
    with psycopg.connect(make_url(db_url).set(drivername="postgresql").render_as_string(hide_password=False)) as conn:
        yield conn


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
                 bank: str | None = None, feed_seq: int | None = None,
                 state: str = "EXTRACTED") -> tuple[Invoice, Document]:
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
                   feed_seq=feed_seq or seq, received_at=dt.datetime(2026, 1, 20, tzinfo=dt.timezone.utc), state=state,
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
