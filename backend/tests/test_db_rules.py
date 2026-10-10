"""The rules the database itself enforces: append-only audit trail and ledger, balanced entries, notifications."""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy import Engine, text

WS = "INSERT INTO workspaces (name, kind, dataset, settings) VALUES (%s, 'manual', 'user', '{}') RETURNING id"


def _workspace(pg: psycopg.Connection, name: str) -> int:
    ws = pg.execute(WS, (name,)).fetchone()[0]
    pg.commit()
    return ws


def _audit(pg: psycopg.Connection, ws: int, seq: int) -> None:
    pg.execute("INSERT INTO audit_events (workspace_id, seq, actor, action, subject_type, data, prev_hash, hash) "
               "VALUES (%s, %s, 'system', 'test.event', 'workspace', '{}', %s, %s)", (ws, seq, "0" * 64, "a" * 64))


def _entry(pg: psycopg.Connection, ws: int, lines: list[tuple[str, str, float, float]]) -> int:
    eid = pg.execute("INSERT INTO journal_entries (workspace_id, entry_date, kind, memo) "
                     "VALUES (%s, current_date, 'invoice', 'test') RETURNING id", (ws,)).fetchone()[0]
    for account, ccy, debit, credit in lines:
        pg.execute("INSERT INTO journal_lines (entry_id, account_code, currency, debit, credit) "
                   "VALUES (%s, %s, %s, %s, %s)", (eid, account, ccy, debit, credit))
    return eid


def test_seed_data_and_queue_tables(engine: Engine) -> None:
    with engine.connect() as conn:
        codes = {r[0] for r in conn.execute(text("SELECT code FROM accounts"))}
        assert {"1000", "1300", "2000", "2100", "4900", "5000"} <= codes
        assert conn.execute(text("SELECT consecutive_429 FROM llm_state WHERE id = 1")).scalar_one() == 0
        tables = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
        assert {"procrastinate_jobs", "procrastinate_workers", "procrastinate_events"} <= tables


@pytest.mark.parametrize("statement", [
    "UPDATE audit_events SET action = 'tampered'",
    "DELETE FROM audit_events",
    "TRUNCATE audit_events",
])
def test_audit_trail_is_append_only(pg: psycopg.Connection, statement: str) -> None:
    ws = _workspace(pg, f"audit-{abs(hash(statement))}")
    _audit(pg, ws, 1)
    pg.commit()
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        pg.execute(statement)
    pg.rollback()


def test_balanced_entry_commits_and_then_cannot_change(pg: psycopg.Connection) -> None:
    ws = _workspace(pg, "ledger-balanced")
    eid = _entry(pg, ws, [("5000", "USD", 100, 0), ("1300", "USD", 15, 0), ("2000", "USD", 0, 115)])
    pg.commit()
    for statement in ("UPDATE journal_lines SET debit = 1 WHERE entry_id = %s",
                      "DELETE FROM journal_entries WHERE id = %s"):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            pg.execute(statement, (eid,))
        pg.rollback()


@pytest.mark.parametrize("lines, message", [
    ([("5000", "USD", 100, 0), ("2000", "USD", 0, 90)], "does not balance in USD"),
    # balanced in total but not per currency
    ([("5000", "USD", 100, 0), ("2000", "EUR", 0, 100)], "does not balance"),
    ([("5000", "USD", 100, 0)], "at least 2 are required"),
    ([], "at least 2 are required"),
])
def test_unbalanced_entry_is_rejected_at_commit(pg: psycopg.Connection, lines, message: str) -> None:
    ws = _workspace(pg, f"ledger-bad-{len(lines)}-{message[:8]}-{lines[-1][1] if lines else 'none'}")
    _entry(pg, ws, lines)  # accepted until commit: the check is deferred
    with pytest.raises(psycopg.errors.RaiseException, match=message):
        pg.commit()


def test_one_sided_positive_lines(pg: psycopg.Connection) -> None:
    ws = _workspace(pg, "ledger-one-sided")
    with pytest.raises(psycopg.errors.CheckViolation):
        _entry(pg, ws, [("5000", "USD", 10, 10), ("2000", "USD", 0, 0)])
    pg.rollback()


def test_audit_insert_is_announced(pg: psycopg.Connection, db_url: str) -> None:
    from sqlalchemy import make_url

    listen_url = make_url(db_url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(listen_url, autocommit=True) as listener:
        listener.execute("LISTEN audit_events")
        ws = _workspace(pg, "notify")
        _audit(pg, ws, 1)
        pg.commit()
        note = next(listener.notifies(timeout=5))
    assert note.channel == "audit_events"
    assert f'"workspace_id" : {ws}' in note.payload and '"action" : "test.event"' in note.payload
