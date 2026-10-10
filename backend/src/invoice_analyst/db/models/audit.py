"""The audit trail: one hash chain per workspace (workspace_id NULL = the global chain).

Rows are append-only (database triggers block UPDATE and DELETE). workspace_id is deliberately not a foreign
key, so a workspace's history survives the workspace being deleted.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import BigInteger, DateTime, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import PK, Base, one_of

ACTORS = ("system", "user", "llm")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("workspace_id", "seq"), one_of("actor", ACTORS))

    id: Mapped[PK]
    workspace_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    seq: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)  # e.g. invoice.state_changed, review.decided, payment.executed
    subject_type: Mapped[str] = mapped_column(Text)
    subject_id: Mapped[int | None] = mapped_column(BigInteger)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    prev_hash: Mapped[str] = mapped_column(Text)  # hash of the previous event in this chain ("0"*64 for the first)
    hash: Mapped[str] = mapped_column(Text)  # sha256(prev_hash || canonical JSON of this event)
