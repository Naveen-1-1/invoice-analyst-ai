"""Every LLM call (request, response, timing) doubling as the response cache, plus the shared 429 cooldown."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import PK, Base, Created


class LlmCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        # One successful answer per cache key; failed attempts are kept for the log but never served
        Index("uq_llm_calls_cache_key_ok", "cache_key", unique=True, postgresql_where=text("ok")),
    )

    id: Mapped[PK]
    cache_key: Mapped[str] = mapped_column(Text)
    request_sha256: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_hash: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    request: Mapped[dict[str, Any]]  # images stored as {sha256, bytes, path}, never inline
    response: Mapped[dict[str, Any] | None]
    content: Mapped[str | None] = mapped_column(Text)
    reasoning: Mapped[str | None] = mapped_column(Text)
    finish_reason: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    nvcf_reqid: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("documents.id", ondelete="SET NULL"),
                                                    index=True)
    extraction_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("extractions.id", ondelete="SET NULL"))
    created_at: Mapped[Created]


class LlmState(Base):
    """Single row (id = 1): when the shared rate-limit cooldown ends."""

    __tablename__ = "llm_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    cooldown_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_429: Mapped[int] = mapped_column(Integer, default=0)
    last_429_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
