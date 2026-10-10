"""Shared fixtures. Database tests run against a throwaway database, migrated from scratch for each session."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url

from invoice_analyst.config import settings

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    """URL of a fresh, fully migrated test database (dropped afterwards)."""
    url = make_url(settings().database_url).set(database=f"{make_url(settings().database_url).database}_test")
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{url.database}"')
    rendered = url.render_as_string(hide_password=False)
    cfg = Config(BACKEND / "alembic.ini")
    cfg.set_main_option("sqlalchemy.url", rendered)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    yield rendered
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')


@pytest.fixture(scope="session")
def engine(db_url: str) -> Iterator[Engine]:
    eng = create_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def pg(db_url: str) -> Iterator[psycopg.Connection]:
    """A raw psycopg connection to the test database (for LISTEN/NOTIFY and commit-time checks)."""
    with psycopg.connect(make_url(db_url).set(drivername="postgresql").render_as_string(hide_password=False)) as conn:
        yield conn
