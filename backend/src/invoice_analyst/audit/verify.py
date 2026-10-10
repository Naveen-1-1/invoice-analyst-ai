"""Check a workspace's audit trail: every event's hash must follow from the one before it.

Recomputes sha256(prev_hash || canonical event) along the chain, in sequence order. Any edit to an event (its
data, actor, time, ...), a deleted or inserted event, or a reordering breaks the chain at that point.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AuditEvent
from .chain import GENESIS, canonical, event_hash


@dataclass
class Verdict:
    workspace_id: int | None
    ok: bool
    events: int
    broken_at: int | None = None  # sequence number of the first event that doesn't check out
    problem: str | None = None

    def words(self) -> str:
        if self.ok:
            return f"chain verified: {self.events} events, each hash follows from the one before"
        return f"chain broken at event {self.broken_at}: {self.problem}"


def verify(s: Session, workspace_id: int | None) -> Verdict:
    scope = AuditEvent.workspace_id == workspace_id if workspace_id is not None else AuditEvent.workspace_id.is_(None)
    prev, expected_seq, n = GENESIS, 1, 0
    for ev in s.scalars(select(AuditEvent).where(scope).order_by(AuditEvent.seq)).yield_per(1000):
        n += 1
        if ev.seq != expected_seq:
            return Verdict(workspace_id, False, n, ev.seq, f"expected event {expected_seq} next (one is missing)")
        if ev.prev_hash != prev:
            return Verdict(workspace_id, False, n, ev.seq, "it doesn't point at the event before it")
        if event_hash(ev.prev_hash, canonical(ev)) != ev.hash:
            return Verdict(workspace_id, False, n, ev.seq, "its content was changed after it was written")
        prev, expected_seq = ev.hash, expected_seq + 1
    return Verdict(workspace_id, True, n)


def workspaces(s: Session) -> list[int | None]:
    return list(s.scalars(select(AuditEvent.workspace_id).distinct()))
