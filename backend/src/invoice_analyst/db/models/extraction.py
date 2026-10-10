"""What the models read: raw extractions, invoices, lines, and every field value with its evidence."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import REAL, BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import PK, Base, Ccy, Created, Money, Qty, one_of

EXTRACTION_STATUSES = ("ok", "failed")
# the decision; payment progress is in payment_items (planned, waiting, paid)
INVOICE_STATES = ("EXTRACTED", "CHECKED", "MATCHED", "AUTO_APPROVED", "NEEDS_REVIEW", "APPROVED",
                  "APPROVED_PARTIAL", "REJECTED", "ON_HOLD")
DOC_TYPES = ("invoice", "credit_note", "statement", "other")
TIERS = ("high", "medium", "low")
LINE_KINDS = ("item", "charge", "discount")
GROUNDING = ("exact", "fuzzy", "page", "none")
SOURCES = ("llm", "pdf_text", "ocr", "derived", "human")


class Extraction(Base):
    __tablename__ = "extractions"
    __table_args__ = (one_of("status", EXTRACTION_STATUSES),)

    id: Mapped[PK]
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    route: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_hash: Mapped[str] = mapped_column(Text)
    schema_ver: Mapped[str] = mapped_column(Text)
    preprocess_ver: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    raw: Mapped[dict[str, Any] | None]  # the model's tool-call arguments, as returned
    normalized: Mapped[dict[str, Any] | None]  # after deterministic clean-up
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[Created]


class Invoice(Base):
    """One invoice; a multi-invoice file yields several (child_index 0, 1, ...)."""

    __tablename__ = "invoices"
    __table_args__ = (one_of("state", INVOICE_STATES), one_of("doc_type", DOC_TYPES), one_of("conf_tier", TIERS))

    id: Mapped[PK]
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    extraction_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("extractions.id", ondelete="SET NULL"))
    child_index: Mapped[int] = mapped_column(Integer, default=0)
    doc_type: Mapped[str] = mapped_column(Text, default="invoice")
    state: Mapped[str] = mapped_column(Text, default="EXTRACTED")
    vendor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("vendors.id", ondelete="SET NULL"), index=True)
    entity_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="SET NULL"))
    po_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("purchase_orders.id", ondelete="SET NULL"))
    vendor_name: Mapped[str | None] = mapped_column(Text)  # as printed (matched to the vendor list as vendor_id)
    currency: Mapped[Ccy | None]
    invoice_number: Mapped[str | None] = mapped_column(Text)
    invoice_number_norm: Mapped[str | None] = mapped_column(Text, index=True)
    invoice_date: Mapped[dt.date | None] = mapped_column(Date)
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    due_date_source: Mapped[str | None] = mapped_column(Text)  # printed / terms / vendor_terms / assumed_net30
    subtotal: Mapped[Money | None]
    tax: Mapped[Money | None]
    total: Mapped[Money | None]
    payable_amount: Mapped[Money | None]
    payable_currency: Mapped[Ccy | None]
    conf_tier: Mapped[str | None] = mapped_column(Text)
    review_labels: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[Created]
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                    onupdate=func.now())


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (one_of("kind", LINE_KINDS),)

    id: Mapped[PK]
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text, default="item")
    description: Mapped[str | None] = mapped_column(Text)
    item_code: Mapped[str | None] = mapped_column(Text)
    qty: Mapped[Qty | None]
    unit_price: Mapped[Money | None]
    amount: Mapped[Money | None]
    vat_rate: Mapped[Decimal | None]


class FieldValue(Base):
    """One extracted value with where it was found, how sure we are, and why. Corrections supersede, never edit."""

    __tablename__ = "field_values"
    __table_args__ = (one_of("grounding", GROUNDING), one_of("source", SOURCES), one_of("tier", TIERS))

    id: Mapped[PK]
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    invoice_line_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("invoice_lines.id", ondelete="CASCADE"))
    field: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)  # exactly as printed
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)  # normalised value (string, number, ISO date, ...)
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(ARRAY(REAL))  # [x0, y0, x1, y1] normalised 0-1
    evidence_text: Mapped[str | None] = mapped_column(Text)
    grounding: Mapped[str] = mapped_column(Text, default="none")
    source: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(REAL)
    tier: Mapped[str | None] = mapped_column(Text)
    reasons: Mapped[list[Any]] = mapped_column(default=list)  # [{"code", "text", "effect"}]
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("field_values.id", ondelete="SET NULL"))
    created_at: Mapped[Created]
