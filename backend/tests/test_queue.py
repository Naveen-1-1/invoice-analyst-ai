"""The job queue's guarantees: strict arrival order, resumable reading, and waiting out rate limits."""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker
from tenacity import wait_none

from invoice_analyst.config import ROOT, Settings
from invoice_analyst.db.models import Document, Invoice, LlmCall
from invoice_analyst.extraction import extract
from invoice_analyst.llm import client as llm
from invoice_analyst.llm.client import CooldownError
from invoice_analyst.worker import sequencer
from invoice_analyst.worker.tasks import WaitOutCooldown

from .conftest import make_invoice
from .test_llm_client import FakeNvidia, tool_reply

PDF = ROOT / "third_party" / "invoice-extraction-benchmark" / "corpus" / "v1" / "documents" / "clean-us-net30-terms.pdf"


def test_sequencer_checks_in_order_and_waits_at_a_gap(ws, monkeypatch) -> None:
    sessions, wid = ws
    monkeypatch.setattr(sequencer, "session_factory", lambda: sessions)
    with sessions.begin() as s:
        first, _ = make_invoice(s, wid, number="S1", feed_seq=1)
        second, doc2 = make_invoice(s, wid, number="S2", feed_seq=2, state="PREPARED")  # arrived, not read yet
        third, _ = make_invoice(s, wid, number="S3", feed_seq=3)
        ids = (first.id, second.id, third.id)
    assert sequencer.advance(wid) == 1  # only arrival 1: arrival 3 waits behind unread arrival 2
    with sessions() as s:
        # checked, then decided: hand-built invoices carry no confidence tier, so they go to review
        assert [s.get(Invoice, i).state for i in ids] == ["NEEDS_REVIEW", "EXTRACTED", "EXTRACTED"]
    with sessions.begin() as s:
        s.get(Document, doc2.id).state = "EXTRACTED"
    assert sequencer.advance(wid) == 2
    assert sequencer.advance(wid) == 0  # running it again changes nothing
    with sessions() as s:
        assert [s.get(Invoice, i).state for i in ids] == ["NEEDS_REVIEW"] * 3


def test_retry_waits_exactly_until_a_cooldown_ends() -> None:
    until = dt.datetime(2026, 10, 10, 15, 0, tzinfo=dt.timezone.utc)
    strategy = WaitOutCooldown(max_attempts=3)
    assert strategy.get_retry_decision(exception=CooldownError(until), job=SimpleNamespace(attempts=9)).retry_at == until
    assert strategy.get_retry_decision(exception=RuntimeError("x"), job=SimpleNamespace(attempts=0)) is not None
    assert strategy.get_retry_decision(exception=RuntimeError("x"), job=SimpleNamespace(attempts=2)) is None


async def test_reading_resumes_after_a_rate_limit_without_a_second_document(engine: Engine, ws, tmp_path,
                                                                            monkeypatch) -> None:
    sessions = sessionmaker(engine, expire_on_commit=False)
    _, wid = ws
    monkeypatch.setattr(llm, "RETRY_WAIT", wait_none())
    settings = Settings(nvidia_api_key="test", llm_rpm=6000, storage_dir=tmp_path)
    answer = {"invoices": [{"document_type": "invoice", "invoice_number": {"text": "ACM-100401"},
                            "lines": [{"description": "Kit", "amount": 1629.7}], "total": {"value": 1629.7}}]}
    reply = tool_reply(answer)
    reply["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "FileExtraction"
    fake = FakeNvidia((429, {"title": "Too Many Requests"}), (200, reply))
    client = llm.LlmClient(settings, sessions, inner_transport=httpx.MockTransport(fake))

    doc_id = extract.receive(data=PDF.read_bytes(), filename=PDF.name, workspace_id=wid, run_id=None,
                             settings=settings, sessions=sessions, feed_seq=1)
    with pytest.raises(CooldownError):
        await extract.read(doc_id, settings=settings, sessions=sessions, llm=client)
    with sessions() as s:
        assert s.get(Document, doc_id).state == "PREPARED"  # prepared, then stopped by the rate limit
    client.cooldown._until = None  # the cooldown has passed
    with sessions.begin() as s:
        s.execute(llm.update(llm.LlmState).values(cooldown_until=None, consecutive_429=0))
    outcome = await extract.read(doc_id, settings=settings, sessions=sessions, llm=client)
    again = await extract.read(doc_id, settings=settings, sessions=sessions, llm=client)  # already done: no call
    await client.aclose()
    assert outcome.error is None and outcome.extraction.invoices[0].invoice_number.text == "ACM-100401"
    assert again.cache_hit and len(fake.requests) == 2
    with sessions() as s:
        assert s.scalar(select(Document.state).where(Document.id == doc_id)) == "EXTRACTED"
        assert len(s.scalars(select(Document).where(Document.workspace_id == wid, Document.feed_seq == 1)).all()) == 1
        sent = [json.dumps(c.request) for c in s.scalars(select(LlmCall).where(LlmCall.document_id == doc_id))]
    assert len(sent) == 2 and sent[0] == sent[1]  # the resumed read sent exactly the same request
