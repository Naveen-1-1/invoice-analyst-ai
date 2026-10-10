"""Check a workspace's invoices strictly in arrival order (feed_seq 1, 2, 3, ...), then decide on each one.

Documents are read in parallel, but duplicate and three-way-match checks depend on what arrived earlier, so the
sequencer walks the arrivals in order and stops at the first one that hasn't arrived or hasn't been read yet. It is
safe to run again at any time: invoices already checked are skipped, and each document is checked in one
transaction (an interruption leaves it unchecked, never half-checked). After its checks each invoice is approved
automatically or sent to review (decision/rules.py); an invoice checked but not yet decided is decided on the next pass.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from ..audit import chain
from ..checks.engine import check_invoice
from ..db.engine import session_factory
from ..decision.rules import decide
from ..db.models import Document, Invoice, Run, Workspace
from ..inbox.results import write_results
from ..payments import clock, runs

WAITING = ("RECEIVED", "PREPARED")
UNDECIDED = ("EXTRACTED", "MATCHED")  # not checked yet / checked, no decision yet


def advance(workspace_id: int) -> int:
    """Check every invoice that can be checked now, in order. Returns how many were checked."""
    sessions = session_factory()
    with sessions() as s:
        docs = {seq: (doc_id, state) for seq, doc_id, state in s.execute(
            select(Document.feed_seq, Document.id, Document.state)
            .where(Document.workspace_id == workspace_id, Document.feed_seq.is_not(None))).all()}
        pending = {doc_id for (doc_id,) in s.execute(
            select(Invoice.document_id).where(Invoice.workspace_id == workspace_id, Invoice.state.in_(UNDECIDED))).all()}
    checked = 0
    seq = 1
    while seq in docs:
        doc_id, state = docs[seq]
        if state in WAITING:
            break  # not read yet: everything after it waits, to keep arrival order
        if doc_id in pending:
            with sessions.begin() as s:
                doc = s.get(Document, doc_id)
                for inv in s.scalars(select(Invoice).where(Invoice.document_id == doc_id,
                                                           Invoice.state.in_(UNDECIDED))
                                     .order_by(Invoice.child_index)).all():
                    if inv.state == "EXTRACTED":
                        check_invoice(s, inv, doc, doc.received_at.date())
                    decide(s, inv)
                    checked += 1
        seq += 1
    with sessions.begin() as s:  # a live inbox pays its runs as the simulated clock passes them (decision 43)
        ws = s.get(Workspace, workspace_id)
        now = clock.today(s, ws)
        if now.live:
            runs.run_due(s, ws, now.date)
    _finish_runs(workspace_id, through=seq - 1)
    return checked


def _finish_runs(workspace_id: int, through: int) -> None:
    """Mark an inbox run finished once every one of its arrivals has been checked, and write its results."""
    sessions = session_factory()
    with sessions.begin() as s:
        ws = s.get(Workspace, workspace_id)
        run = s.scalars(select(Run).where(Run.workspace_id == workspace_id, Run.mode == "inbox",
                                          Run.status == "running")).first()
        expected = (run.config or {}).get("documents") if run else None
        if run is None or not expected or through < expected:
            return
        clock.freeze(s, ws)  # from now on today is settable, starting where the clock stopped
        run.status = "finished"
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        chain.record(s, ws.id, "system", "run.finished", "run", run.id, {"documents": expected})
        run_id = run.id
    write_results(run_id)
