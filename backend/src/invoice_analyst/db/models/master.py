"""Master data: our companies (entities), the vendor list, vendor bank accounts and invoice history."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from .base import PK, Base, Ccy, Money, one_of

VENDOR_STATUSES = ("active", "blocked")
BANK_KINDS = ("iban", "account", "sort_code")


class Entity(Base):
    """A company of ours that invoices may be addressed to (the bill-to)."""

    __tablename__ = "entities"

    id: Mapped[PK]
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    country: Mapped[str | None] = mapped_column(Text)
    tax_id: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)


class Vendor(Base):
    __tablename__ = "vendors"
    __table_args__ = (one_of("status", VENDOR_STATUSES),)

    id: Mapped[PK]
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    name_norm: Mapped[str] = mapped_column(Text, index=True)  # casefolded, legal-form words removed
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    country: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    tax_id: Mapped[str | None] = mapped_column(Text)
    tax_id_norm: Mapped[str | None] = mapped_column(Text, index=True)
    email: Mapped[str | None] = mapped_column(Text)
    terms_days: Mapped[int | None] = mapped_column(Integer)
    discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # e.g. 2.00 for "2/10 net 30"
    discount_days: Mapped[int | None] = mapped_column(Integer)
    default_gl: Mapped[str | None] = mapped_column(Text, default="5000")
    status: Mapped[str] = mapped_column(Text, default="active")
    origin: Mapped[str | None] = mapped_column(Text)  # companion / manual


class VendorBankAccount(Base):
    __tablename__ = "vendor_bank_accounts"
    __table_args__ = (one_of("kind", BANK_KINDS),)

    id: Mapped[PK]
    vendor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(Text)
    account_norm: Mapped[str] = mapped_column(Text)  # uppercase, no spaces; compared exactly
    bank_name: Mapped[str | None] = mapped_column(Text)
    bic: Mapped[str | None] = mapped_column(Text)
    branch: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[dt.date | None] = mapped_column(Date)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)  # set when replaced: payments go to the open one
    source: Mapped[str | None] = mapped_column(Text)  # companion, or the review that confirmed it


class VendorHistory(Base):
    """Past invoices per vendor (synthetic in companion data), for the 'unusual amount' check."""

    __tablename__ = "vendor_history"

    id: Mapped[PK]
    vendor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    invoice_number: Mapped[str | None] = mapped_column(Text)
    invoice_date: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[Money]
    currency: Mapped[Ccy]
