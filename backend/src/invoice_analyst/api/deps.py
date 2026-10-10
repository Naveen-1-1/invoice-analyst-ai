"""Shared request dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from ..db.engine import session_factory


def db() -> Iterator[Session]:
    with session_factory()() as s:
        yield s
