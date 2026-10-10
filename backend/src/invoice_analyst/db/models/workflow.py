"""Check results, line matching, pipeline step runs, review tasks/decisions and approvals."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from .base import PK, Base, Ccy, Created, Money, Qty, one_of

CHECK_OUTCOMES = ("pass", "warn", "fail", "skip")
SEVERITIES = ("info", "review", "block")
MATCH_OUTCOMES = ("match", "price_variance", "qty_over_received", "unmatched", "two_way")
STEP_STATUSES = ("queued", "running", "done", "failed", "skipped")
REVIEW_STATUSES = ("open", "resolved")
REVIEW_ACTIONS = ("approve", "approve_partial", "reject", "hold", "reprocess")
APPROVAL_KINDS = ("auto", "manual")


class CheckResult(Base):
    __tablename__ = "check_results"
    __table_args__ = (one_of("outcome", CHECK_OUTCOMES), one_of("severity", SEVERITIES))

    id: Mapped[PK]
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    step_run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("step_runs.id", ondelete="SET NULL"))
    code: Mapped[str] = mapped_column(Text)  # e.g. dup.vendor_number, bank.changed, match.price_over_po
    category: Mapped[str] = mapped_column(Text)  # arithmetic / doctype / duplicate / vendor / bank / match / pattern
    outcome: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, default="info")
    message: Mapped[str] = mapped_column(Text)  # plain language, shown in the UI
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    related_invoice_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("invoices.id", ondelete="SET NULL"))
    check_ver: Mapped[str] = mapped_column(Text)
    created_at: Mapped[Created]


class MatchLine(Base):
    """One invoice line against its PO line and receipts. qty_approved consumes received quantity."""

    __tablename__ = "match_lines"
    __table_args__ = (one_of("outcome", MATCH_OUTCOMES),)

    id: Mapped[PK]
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    invoice_line_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("invoice_lines.id", ondelete="CASCADE"))
    po_line_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("po_lines.id", ondelete="SET NULL"),
                                                   index=True)
    qty_invoiced: Mapped[Qty | None]
    qty_received_avail: Mapped[Qty | None]
    qty_approved: Mapped[Qty | None]
    price_inv: Mapped[Money | None]
    price_po: Mapped[Money | None]
    variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    outcome: Mapped[str] = mapped_column(Text)


class StepRun(Base):
    """One pipeline step for a document or invoice; the UI's per-step panels read these."""

    __tablename__ = "step_runs"
    __table_args__ = (one_of("status", STEP_STATUSES),)

    id: Mapped[PK]
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("invoices.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(Text)  # prepare / extract / locate / check / match / decide / post / schedule / pay
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(Text, default="queued")
    job_id: Mapped[int | None] = mapped_column(BigInteger)  # procrastinate job id
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    explanation: Mapped[str | None] = mapped_column(Text)  # plain language
    error: Mapped[str | None] = mapped_column(Text)


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (one_of("status", REVIEW_STATUSES),)

    id: Mapped[PK]
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    labels: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # no_po, possible_duplicate, ...
    reasons: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(Text, default="open")
    created_at: Mapped[Created]
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (one_of("action", REVIEW_ACTIONS),)

    id: Mapped[PK]
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("review_tasks.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(Text)
    corrections: Mapped[dict[str, Any]] = mapped_column(default=dict)
    note: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[Created]


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (one_of("kind", APPROVAL_KINDS),)

    id: Mapped[PK]
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(Text)
    amount: Mapped[Money]
    currency: Mapped[Ccy]
    reasons: Mapped[list[Any]] = mapped_column(default=list)
    rules_ver: Mapped[str] = mapped_column(Text)
    created_at: Mapped[Created]
