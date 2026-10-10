"""Append events to a workspace's hash chain. hash = sha256(prev_hash || canonical JSON of the event).

Writers take a transaction-scoped advisory lock per workspace, so sequence numbers have no gaps or races and the
event commits (or rolls back) together with the change it describes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

import rfc8785
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..db.models import AuditEvent

GENESIS = "0" * 64


def event_hash(prev_hash: str, event: dict[str, Any]) -> str:
    return hashlib.sha256(prev_hash.encode() + rfc8785.dumps(event)).hexdigest()


def canonical(ev: AuditEvent) -> dict[str, Any]:
    return {"workspace_id": ev.workspace_id, "seq": ev.seq, "occurred_at": ev.occurred_at.isoformat(),
            "actor": ev.actor, "action": ev.action, "subject_type": ev.subject_type, "subject_id": ev.subject_id,
            "data": ev.data}


def record(session: Session, workspace_id: int | None, actor: str, action: str, subject_type: str,
           subject_id: int | None, data: dict[str, Any] | None = None) -> AuditEvent:
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": workspace_id or 0})
    scope = AuditEvent.workspace_id == workspace_id if workspace_id is not None else AuditEvent.workspace_id.is_(None)
    last = session.execute(select(AuditEvent.seq, AuditEvent.hash).where(scope)
                           .order_by(AuditEvent.seq.desc()).limit(1)).first()
    ev = AuditEvent(workspace_id=workspace_id, seq=(last.seq + 1) if last else 1,
                    occurred_at=dt.datetime.now(dt.timezone.utc).replace(microsecond=0), actor=actor, action=action,
                    subject_type=subject_type, subject_id=subject_id, data=data or {},
                    prev_hash=last.hash if last else GENESIS, hash="")
    ev.hash = event_hash(ev.prev_hash, canonical(ev))
    session.add(ev)
    session.flush()
    return ev


def chain_length(session: Session, workspace_id: int) -> int:
    return session.scalar(select(func.count()).where(AuditEvent.workspace_id == workspace_id)) or 0
