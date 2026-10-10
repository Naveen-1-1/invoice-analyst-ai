"""Workspaces keep benchmark runs, the dev and held-out inboxes, and manual uploads apart."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import PK, Base, Created, one_of

WORKSPACE_KINDS = ("benchmark", "inbox", "manual")
DATASETS = ("dev", "heldout", "user")
RUN_MODES = ("benchmark", "inbox")
RUN_STATUSES = ("running", "finished", "failed")


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (one_of("kind", WORKSPACE_KINDS), one_of("dataset", DATASETS))

    id: Mapped[PK]
    name: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    dataset: Mapped[str] = mapped_column(Text)
    # Fixed "today" for manual mode; inbox mode moves its own simulated clock
    as_of_date: Mapped[dt.date | None] = mapped_column(Date)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[Created]


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (one_of("mode", RUN_MODES), one_of("status", RUN_STATUSES))

    id: Mapped[PK]
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(Text)
    git_sha: Mapped[str | None] = mapped_column(Text)
    # Model ids, prompt hashes, thresholds: everything needed to reproduce the run
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(Text, default="running")
    started_at: Mapped[Created]
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    scores: Mapped[dict[str, Any] | None]
