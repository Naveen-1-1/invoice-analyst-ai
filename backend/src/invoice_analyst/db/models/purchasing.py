"""Purchase orders (what we ordered) and goods receipts (what arrived)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import PK, Base, Ccy, Money, Qty, one_of

PO_STATUSES = ("open", "closed", "cancelled")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (one_of("status", PO_STATUSES),)

    id: Mapped[PK]
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    po_number: Mapped[str] = mapped_column(Text)
    po_number_norm: Mapped[str] = mapped_column(Text, index=True)
    vendor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("vendors.id", ondelete="SET NULL"))
    entity_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("entities.id", ondelete="SET NULL"))
    currency: Mapped[Ccy]
    order_date: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, default="open")


class PoLine(Base):
    __tablename__ = "po_lines"

    id: Mapped[PK]
    po_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    item_code: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    qty: Mapped[Qty | None]  # null for services (two-way match on amount)
    unit_price: Mapped[Money | None]
    amount: Mapped[Money | None]
    vat_rate: Mapped[Decimal | None]
    gl_account: Mapped[str | None] = mapped_column(Text)


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"

    id: Mapped[PK]
    po_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    grn_number: Mapped[str] = mapped_column(Text)
    received_date: Mapped[dt.date] = mapped_column(Date)


class GoodsReceiptLine(Base):
    __tablename__ = "goods_receipt_lines"

    id: Mapped[PK]
    goods_receipt_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("goods_receipts.id", ondelete="CASCADE"),
                                                  index=True)
    po_line_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("po_lines.id", ondelete="CASCADE"), index=True)
    qty: Mapped[Qty]
