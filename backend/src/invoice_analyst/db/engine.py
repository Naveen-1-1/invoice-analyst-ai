"""Database engine and session factory."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings


@lru_cache(maxsize=1)
def engine() -> Engine:
    return create_engine(settings().database_url, pool_pre_ping=True)


def session_factory() -> sessionmaker[Session]:
    return sessionmaker(engine(), expire_on_commit=False)
