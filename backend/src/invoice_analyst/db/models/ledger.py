"""Payment runs and the double-entry ledger. Journal rows are append-only (database triggers enforce it)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import PK, Base, Ccy, Created, Money, one_of

PAYMENT_RUN_STATUSES = ("planned", "executed")
ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")
ENTRY_KINDS = ("invoice", "credit_note", "payment", "reversal")


class PaymentRun(Base):
    __tablename__ = "payment_runs"
    __table_args__ = (one_of("status", PAYMENT_RUN_STATUSES),)

    id: Mapped[PK]
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    run_date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, default="planned")
    executed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentItem(Base):
    __tablename__ = "payment_items"

    id: Mapped[PK]
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payment_runs.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    bank_account_id: Mapped[int | None] = mapped_column(BigInteger,
                                                        ForeignKey("vendor_bank_accounts.id", ondelete="SET NULL"))
    amount: Mapped[Money]
    discount: Mapped[Money] = mapped_column(default=Decimal(0))
    currency: Mapped[Ccy]
    reason: Mapped[str] = mapped_column(Text)  # plain language: why this run, why this amount


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (one_of("type", ACCOUNT_TYPES),)

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    __table_args__ = (one_of("kind", ENTRY_KINDS),)

    id: Mapped[PK]
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id"), index=True)
    entry_date: Mapped[dt.date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(Text)
    invoice_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("invoices.id"), index=True)
    payment_item_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("payment_items.id"))
    reverses_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("journal_entries.id"))
    memo: Mapped[str] = mapped_column(Text)
    posted_at: Mapped[Created]


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("debit >= 0 AND credit >= 0 AND (debit = 0 OR credit = 0) AND debit + credit > 0",
                        name="one_sided_positive"),
    )

    id: Mapped[PK]
    entry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("journal_entries.id"), index=True)
    account_code: Mapped[str] = mapped_column(Text, ForeignKey("accounts.code"))
    vendor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("vendors.id"))
    currency: Mapped[Ccy]
    debit: Mapped[Money] = mapped_column(default=Decimal(0))
    credit: Mapped[Money] = mapped_column(default=Decimal(0))
