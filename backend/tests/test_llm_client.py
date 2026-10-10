"""The LLM client against a fake NVIDIA endpoint: cache replay, retries, the 429 cooldown, redacted logging."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import sessionmaker
from tenacity import wait_none

from invoice_analyst.config import Settings
from invoice_analyst.db.models import LlmCall
from invoice_analyst.llm import client as llm


class Tiny(BaseModel):
    answer: str


def tool_reply(args: dict) -> dict:
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "Tiny", "arguments": json.dumps(args)}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}}


class FakeNvidia:
    """Replies with the queued (status, body) pairs in order, recording each request."""

    def __init__(self, *replies: tuple[int, dict]):
        self.replies = list(replies)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        status, body = self.replies.pop(0)
        return httpx.Response(status, json=body)


@pytest.fixture
def sessions(engine: Engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    yield factory
    with engine.begin() as conn:  # leave no cooldown behind for other tests
        conn.execute(text("UPDATE llm_state SET cooldown_until = NULL, consecutive_429 = 0"))


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(llm, "RETRY_WAIT", wait_none())


def make_client(sessions, fake: FakeNvidia) -> llm.LlmClient:
    settings = Settings(nvidia_api_key="test-key", llm_rpm=6000, llm_max_in_flight=2)
    return llm.LlmClient(settings, sessions, inner_transport=httpx.MockTransport(fake))


def image_message() -> list[dict]:
    data = base64.b64encode(b"\xff\xd8\xff fake jpeg").decode()
    return [{"role": "user", "content": [{"type": "text", "text": "read"},
                                         {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}]}]


async def ask(client: llm.LlmClient, inputs: dict, **kw) -> llm.StructuredResult:
    return await client.structured(model="meta/test", messages=image_message(), response_model=Tiny,
                                   params={"max_tokens": 64}, prompt_hash="p1", schema_ver="1", inputs=inputs, **kw)


async def test_second_identical_call_comes_from_the_cache(sessions) -> None:
    fake = FakeNvidia((200, tool_reply({"answer": "INV-1"})))
    client = make_client(sessions, fake)
    first = await ask(client, {"file": "abc"})
    second = await ask(client, {"file": "abc"})
    await client.aclose()
    assert (first.value.answer, first.cache_hit) == ("INV-1", False)
    assert (second.value.answer, second.cache_hit) == ("INV-1", True)
    assert len(fake.requests) == 1  # the second answer cost no API call
    with sessions() as s:
        row = s.scalars(select(LlmCall).where(LlmCall.id == first.call_id)).one()
    assert row.ok and row.tokens_in == 10
    logged = json.dumps(row.request)
    assert "base64" not in logged and '"sha256"' in logged  # images are logged by hash, never inline


async def test_server_error_is_retried(sessions) -> None:
    fake = FakeNvidia((503, {"error": "busy"}), (200, tool_reply({"answer": "ok"})))
    client = make_client(sessions, fake)
    result = await ask(client, {"file": "retry"})
    await client.aclose()
    assert result.value.answer == "ok" and result.attempts == 2 and len(fake.requests) == 2


async def test_rate_limit_starts_a_shared_cooldown(sessions) -> None:
    fake = FakeNvidia((429, {"status": 429, "title": "Too Many Requests"}))
    client = make_client(sessions, fake)
    with pytest.raises(llm.CooldownError) as first:
        await ask(client, {"file": "limited"})
    with pytest.raises(llm.CooldownError):  # refused locally: nothing more is sent during the cooldown
        await ask(client, {"file": "other"})
    await client.aclose()
    assert len(fake.requests) == 1
    with sessions() as s:
        until, count = s.execute(text("SELECT cooldown_until, consecutive_429 FROM llm_state")).one()
    assert count == 1 and until == first.value.until
    # A new client (a new process) honours the stored cooldown too
    fresh = make_client(sessions, FakeNvidia())
    with pytest.raises(llm.CooldownError):
        await ask(fresh, {"file": "later"})
    await fresh.aclose()


async def test_validation_failure_gets_one_retry(sessions) -> None:
    fake = FakeNvidia((200, tool_reply({"wrong": 1})), (200, tool_reply({"answer": "fixed"})))
    client = make_client(sessions, fake)
    result = await ask(client, {"file": "invalid-first"})
    await client.aclose()
    assert result.value.answer == "fixed" and len(fake.requests) == 2


async def test_incomplete_invoice_gets_a_retry_then_is_kept_as_partial(sessions) -> None:
    from invoice_analyst.extraction.schemas import STRICT, FileExtraction

    sparse = {"invoices": [{"document_type": "invoice", "invoice_number": {"text": "A-1"}}]}
    reply = tool_reply(sparse)
    reply["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "FileExtraction"
    fake = FakeNvidia((200, reply), (200, reply))
    client = make_client(sessions, fake)
    result = await client.structured(model="z-ai/test", messages=[{"role": "user", "content": "x"}],
                                     response_model=FileExtraction, params={"max_tokens": 64}, prompt_hash="p2",
                                     schema_ver="2", inputs={"file": "sparse"}, context=STRICT)
    await client.aclose()
    assert len(fake.requests) == 2  # the original call and one retry naming what is missing
    assert "total is missing" in json.dumps(fake.requests[1]["messages"])
    assert result.partial and result.value.invoices[0].invoice_number.text == "A-1"
