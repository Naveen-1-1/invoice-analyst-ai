"""The jobs: a document arrives, is read, and the workspace's sequencer checks whatever can now be checked.

  arrive(...)            store the file, record the document (once, even if the job is retried), then read it
  read_document(id)      prepare + model + locate values; a rate-limit cooldown reschedules the job for when it ends
  advance_workspace(id)  check invoices in arrival order; at most one queued and one running per workspace
"""

from __future__ import annotations

import asyncio
import datetime as dt

from procrastinate import BaseRetryStrategy, RetryDecision, RetryStrategy
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import select

from ..config import ROOT, settings
from ..db.engine import session_factory
from ..db.models import Document, Workspace
from ..extraction.extract import read, receive
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
