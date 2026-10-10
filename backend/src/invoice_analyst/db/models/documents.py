"""Uploaded files and their pages (rendered images, word boxes, quality metrics)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import PK, Base, Created, one_of

DOCUMENT_STATES = ("RECEIVED", "PREPARED", "EXTRACTED", "SPLIT", "NOT_INVOICE", "FAILED")
TEXT_LAYERS = ("full", "partial", "none", "n/a")
ROUTES = ("text_llm", "image_llm")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        one_of("state", DOCUMENT_STATES),
        one_of("text_layer", TEXT_LAYERS),
        one_of("route", ROUTES),
        # Not unique: the same file arriving twice is a duplicate to detect, not an error
        Index("ix_documents_workspace_sha256", "workspace_id", "sha256"),
    )

    id: Mapped[PK]
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="SET NULL"))
    sha256: Mapped[str] = mapped_column(Text)
    phash: Mapped[str | None] = mapped_column(Text)  # perceptual hash of page 1, for re-photographed paper
    filename: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(Text)
    bytes: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    text_layer: Mapped[str | None] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(Text)
    bench_doc_id: Mapped[str | None] = mapped_column(Text)  # benchmark doc_id when loaded from a corpus
    feed_seq: Mapped[int | None] = mapped_column(Integer)  # arrival order in inbox mode
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(Text, default="RECEIVED")
    storage_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[Created]


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "page_no"), one_of("unit", ("pt", "px")))

    id: Mapped[PK]
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"))
    page_no: Mapped[int] = mapped_column(Integer)  # 1-based
    width: Mapped[float]
    height: Mapped[float]
    unit: Mapped[str] = mapped_column(Text)
    image_path: Mapped[str] = mapped_column(Text)  # rendered PNG the reviewer sees; boxes are relative to it
    rotation_applied: Mapped[int] = mapped_column(Integer, default=0)
    # [{"t": text, "bbox": [x0, y0, x1, y1] normalised 0-1, "conf": 0-1, "src": "pdf_text"|"ocr"}]
    words: Mapped[list[Any]] = mapped_column(default=list)
    quality: Mapped[dict[str, Any]] = mapped_column(default=dict)  # sharpness, brightness, contrast, ocr_conf, skew
    phash: Mapped[str | None] = mapped_column(Text)
