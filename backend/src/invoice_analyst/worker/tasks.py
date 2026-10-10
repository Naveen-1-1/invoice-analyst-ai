"""The jobs: a document arrives, is read, and the workspace's sequencer checks whatever can now be checked.

  arrive(...)            store the file, record the document (once, even if the job is retried), then read it
  read_document(id)      prepare + model + locate values; a rate-limit cooldown reschedules the job for when it ends
  advance_workspace(id)  check invoices in arrival order, then decide; at most one queued and one running each
  reread_invoice(id)     review 'reprocess': read the document again ignoring the cache, then check it again
"""

from __future__ import annotations

import asyncio
import datetime as dt

from procrastinate import BaseRetryStrategy, RetryDecision, RetryStrategy
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import select

from ..config import ROOT, settings
from ..db.engine import session_factory
from ..db.models import Document, Invoice, StepRun, Workspace
from ..decision import review
from ..extraction.extract import read, receive, reread
from ..llm.client import CooldownError, LlmClient
from . import sequencer
from .app import app

_llm: LlmClient | None = None


def llm() -> LlmClient:
    """One client per worker process, so its pace (requests per minute) is shared by every job."""
    global _llm
    if _llm is None:
        _llm = LlmClient(settings(), session_factory())
    return _llm


class WaitOutCooldown(BaseRetryStrategy):
    """A rate-limit cooldown: try again exactly when it ends (any number of times). Other errors: a few retries."""

    def __init__(self, max_attempts: int = 4):
        self.max_attempts = max_attempts

    def get_retry_decision(self, *, exception: BaseException, job) -> RetryDecision | None:
        if isinstance(exception, CooldownError):
            return RetryDecision(retry_at=exception.until)
        if job.attempts + 1 >= self.max_attempts:
            return None
        return RetryDecision(retry_in={"seconds": 5 * 2 ** job.attempts})


def _cache_since(workspace_id: int) -> dt.datetime | None:
    with session_factory()() as s:
        value = (s.get(Workspace, workspace_id).settings or {}).get("cache_since")
    return dt.datetime.fromisoformat(value) if value else None


def _existing(workspace_id: int, seq: int) -> int | None:
    with session_factory()() as s:
        return s.scalar(select(Document.id).where(Document.workspace_id == workspace_id, Document.feed_seq == seq))


async def nudge(workspace_id: int) -> None:
    """Ask the sequencer to look at the workspace (one queued request per workspace is enough)."""
    try:
        await advance_workspace.configure(queueing_lock=f"advance-{workspace_id}",
                                          lock=f"advance-{workspace_id}").defer_async(workspace_id=workspace_id)
    except AlreadyEnqueued:
        pass


@app.task(queue="intake", retry=RetryStrategy(max_attempts=3, wait=5))
async def arrive(workspace_id: int, run_id: int | None, path: str, ref: str | None, seq: int,
                 received_date: str) -> None:
    doc_id = await asyncio.to_thread(_existing, workspace_id, seq)
    if doc_id is None:
        file = ROOT / path
        received_at = dt.datetime.combine(dt.date.fromisoformat(received_date), dt.time(9), dt.timezone.utc)
        doc_id = await asyncio.to_thread(receive, data=file.read_bytes(), filename=file.name,
                                         workspace_id=workspace_id, run_id=run_id, settings=settings(),
                                         sessions=session_factory(), ref=ref, feed_seq=seq, received_at=received_at)
    await read_document.defer_async(document_id=doc_id)


@app.task(queue="read", retry=WaitOutCooldown())
async def read_document(document_id: int) -> None:
    with session_factory()() as s:
        workspace_id = s.get(Document, document_id).workspace_id
    await read(document_id, settings=settings(), sessions=session_factory(), llm=llm(),
               cache_since=await asyncio.to_thread(_cache_since, workspace_id))
    await nudge(workspace_id)


@app.task(queue="checks", retry=RetryStrategy(max_attempts=3, wait=5))
async def advance_workspace(workspace_id: int) -> None:
    await asyncio.to_thread(sequencer.advance, workspace_id)


def _step(step_run_id: int, status: str, explanation: str | None = None) -> tuple[int, set[int]]:
    """Update the re-read's step and return (document id, ids of the document's invoices under review)."""
    with session_factory().begin() as s:
        step = s.get(StepRun, step_run_id)
        step.status = status
        now = dt.datetime.now(dt.timezone.utc)
        if status == "running":
            step.started_at = now
        else:
            step.finished_at, step.explanation = now, explanation
        ids = set(s.scalars(select(Invoice.id).where(Invoice.document_id == step.document_id,
                                                     Invoice.state.in_(review.IN_REVIEW))))
        return step.document_id, ids


def _after_reread(step_run_id: int, ids: set[int], message: str) -> None:
    with session_factory().begin() as s:
        for invoice in s.scalars(select(Invoice).where(Invoice.id.in_(ids))):
            if invoice.state in review.IN_REVIEW:
                review.refresh(s, invoice, "read again by the model")
    _step(step_run_id, "done", message)


@app.task(queue="read", retry=WaitOutCooldown())
async def reread_invoice(step_run_id: int) -> None:
    doc_id, ids = await asyncio.to_thread(_step, step_run_id, "running")
    try:
        message = await reread(doc_id, ids, settings=settings(), sessions=session_factory(), llm=llm())
    except CooldownError:
        await asyncio.to_thread(_step, step_run_id, "queued", "Waiting for the model API's cooldown.")
        raise
    except Exception as exc:
        await asyncio.to_thread(_step, step_run_id, "failed", f"{type(exc).__name__}: {exc}"[:500])
        raise
    await asyncio.to_thread(_after_reread, step_run_id, ids, message)
