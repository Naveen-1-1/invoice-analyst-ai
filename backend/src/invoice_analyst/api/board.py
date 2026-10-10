"""The live inbox: workspaces, each workspace's board, live events (SSE), and uploads into the manual workspace."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections import defaultdict
from collections.abc import AsyncIterator

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import func, make_url, select, text
from sqlalchemy.orm import Session

from ..companion import loader
from ..config import settings
from ..db.engine import session_factory
from ..db.models import CheckResult, Document, Invoice, LlmState, Run, Workspace
from ..extraction.extract import receive
from ..ingest.files import sniff
from ..worker.tasks import read_document
from . import schemas as out
from .deps import db

router = APIRouter()
MANUAL_WORKSPACE = "manual"
MANUAL_COMPANION = "dev"  # decision 33: uploads are checked against a copy of the dev vendors, POs and deliveries


@router.get("/api/workspaces", response_model=list[out.WorkspaceOut], name="listWorkspaces")
def list_workspaces(s: Session = Depends(db)) -> list[out.WorkspaceOut]:
    counts = defaultdict(dict)
    for ws_id, state, n in s.execute(select(Document.workspace_id, Document.state, func.count())
                                     .group_by(Document.workspace_id, Document.state)).all():
        counts[ws_id][state] = n
    rows = s.scalars(select(Workspace).where(Workspace.kind.in_(["inbox", "manual"])).order_by(Workspace.id.desc())).all()
    return [out.WorkspaceOut(id=w.id, name=w.name, kind=w.kind, dataset=w.dataset, created_at=w.created_at,
                             documents=sum(counts[w.id].values()), states=counts[w.id]) for w in rows]


def _clock(ws: Workspace) -> out.ClockOut | None:
    c = (ws.settings or {}).get("clock")
    if not c:
        return None
    return out.ClockOut(start_real=dt.datetime.fromisoformat(c["start_real"]), start_sim=dt.date.fromisoformat(c["start_sim"]),
                        seconds_per_day=c["seconds_per_day"])


@router.get("/api/workspaces/{workspace_id}/board", response_model=out.BoardOut, name="getBoard")
def get_board(workspace_id: int, s: Session = Depends(db)) -> out.BoardOut:
    ws = s.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "no such workspace")
    docs = s.scalars(select(Document).where(Document.workspace_id == ws.id).order_by(Document.feed_seq)).all()
    invoices = defaultdict(list)
    for inv in s.scalars(select(Invoice).where(Invoice.workspace_id == ws.id).order_by(Invoice.child_index)):
        invoices[inv.document_id].append(inv)
    findings: dict[int, dict[str, int]] = defaultdict(lambda: {"block": 0, "review": 0, "info": 0})
    codes: dict[int, list[str]] = defaultdict(list)
    for inv_id, severity, code in s.execute(
            select(CheckResult.invoice_id, CheckResult.severity, CheckResult.code)
            .join(Invoice, Invoice.id == CheckResult.invoice_id)
            .where(Invoice.workspace_id == ws.id, CheckResult.outcome.in_(["fail", "warn"]))).all():
        findings[inv_id][severity] += 1
        if severity != "info":
            codes[inv_id].append(code)
    cards = []
    for d in docs:
        first = invoices[d.id][0] if invoices[d.id] else None
        cards.append(out.CardOut(
            document_id=d.id, seq=d.feed_seq, ref=d.bench_doc_id, filename=d.filename, state=d.state, route=d.route,
            received_at=d.received_at, invoices=len(invoices[d.id]),
            vendor=first.vendor_name if first else None, invoice_number=first.invoice_number if first else None,
            total=first.total if first else None, currency=first.currency if first else None,
            tier=first.conf_tier if first else None, invoice_state=first.state if first else None,
            findings=dict(findings[first.id]) if first else {}, codes=codes[first.id][:4] if first else [],
            invoice_id=first.id if first else None, labels=list(first.review_labels or []) if first else []))
    run = s.scalars(select(Run).where(Run.workspace_id == ws.id).order_by(Run.id.desc())).first()
    llm = s.get(LlmState, 1)
    worker_seen = s.scalar(text("SELECT max(last_heartbeat) FROM procrastinate_workers"))
    now = dt.datetime.now(dt.timezone.utc)
    return out.BoardOut(
        workspace=out.WorkspaceOut(id=ws.id, name=ws.name, kind=ws.kind, dataset=ws.dataset, created_at=ws.created_at,
                                   documents=len(docs), states={}),
        clock=_clock(ws), run_status=run.status if run else None,
        expected=(run.config or {}).get("documents") if run else None,
        llm_paused_until=llm.cooldown_until if llm and llm.cooldown_until and llm.cooldown_until > now else None,
        worker_online=bool(worker_seen and (now - worker_seen).total_seconds() < 30), cards=cards)


def _manual_workspace(s: Session) -> Workspace:
    ws = s.scalars(select(Workspace).where(Workspace.name == MANUAL_WORKSPACE)).first()
    if ws is None:
        ws = Workspace(name=MANUAL_WORKSPACE, kind="manual", dataset="user", settings={"set": MANUAL_COMPANION})
        s.add(ws)
        s.flush()
        loader.load(s, ws.id, MANUAL_COMPANION)
    return ws


@router.post("/api/uploads", response_model=list[out.UploadOut], name="uploadDocuments")
async def upload_documents(files: list[UploadFile]) -> list[out.UploadOut]:
    """Upload invoices (PDF, JPEG, PNG, TIFF, WebP, HEIC) into the manual workspace; the worker reads and checks them."""
    sessions = session_factory()
    with sessions.begin() as s:
        ws_id = _manual_workspace(s).id
    results = []
    for f in files:
        data = await f.read()
        try:
            sniff(data)
        except ValueError as exc:
            results.append(out.UploadOut(filename=f.filename or "?", error=str(exc)))
            continue
        doc_id = await asyncio.to_thread(receive, data=data, filename=f.filename or "upload", workspace_id=ws_id,
                                         run_id=None, settings=settings(), sessions=sessions, next_seq=True)
        await read_document.defer_async(document_id=doc_id)
        results.append(out.UploadOut(filename=f.filename or "?", document_id=doc_id, workspace_id=ws_id))
    return results


def _listen_url() -> str:
    return make_url(settings().database_url).set(drivername="postgresql").render_as_string(hide_password=False)


# Not in the OpenAPI description: the app reads it with EventSource, and the generator can't read stream schemas
@router.get("/api/events", response_class=EventSourceResponse, name="streamEvents", include_in_schema=False)
async def stream_events(request: Request, workspace_id: int | None = None) -> AsyncIterator[ServerSentEvent]:
    """Server-sent events: one per audit event (a document arrived, was read, an invoice was checked, ...)."""
    async with await psycopg.AsyncConnection.connect(_listen_url(), autocommit=True) as conn:
        await conn.execute("LISTEN audit_events")
        yield ServerSentEvent(event="ready", data={})
        while not await request.is_disconnected():
            async for note in conn.notifies(timeout=15):
                payload = json.loads(note.payload)
                if workspace_id is None or payload.get("workspace_id") == workspace_id:
                    yield ServerSentEvent(event="audit", data=payload)
                if await request.is_disconnected():
                    return
            yield ServerSentEvent(comment="keep-alive")
