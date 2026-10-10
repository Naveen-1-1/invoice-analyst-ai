"""The one way the backend talks to NVIDIA's models.

Every HTTP request passes through `PacedTransport`, which:
  - waits for the shared 30-requests-per-minute pace (both models share one limit) and caps calls in flight;
  - retries short failures (5xx, timeouts, dropped connections) a few times;
  - on HTTP 429 records a cooldown in the database (llm_state) and raises CooldownError; until it ends, new requests
    are refused locally instead of being sent (NVIDIA sends no Retry-After, and users report hour-long lockouts);
  - logs every attempt to llm_calls (images recorded by hash and size, never inline).

`LlmClient.structured` adds the cache on top: a successful answer for the same model, prompt, template, input and
parameters is replayed from llm_calls, so re-running a dataset costs no API calls.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import datetime as dt
import hashlib
import json
import random
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
import instructor
import rfc8785
from aiolimiter import AsyncLimiter
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from ..config import Settings
from ..db.models import LlmCall, LlmState

T = TypeVar("T", bound=BaseModel)

COOLDOWN_START_S = 60
COOLDOWN_MAX_S = 3600
SHORT_RETRIES = 4  # attempts for 5xx / timeouts / connection errors
RETRY_WAIT = wait_exponential_jitter(multiplier=2, max=30)


class CooldownError(Exception):
    """The API rate-limited us (or a cooldown is still running): retry after `until`."""

    def __init__(self, until: dt.datetime):
        super().__init__(f"LLM paused until {until.isoformat(timespec='seconds')}")
        self.until = until


def _cooldown_in_chain(exc: BaseException) -> CooldownError | None:
    """The openai SDK and instructor wrap transport errors; find a CooldownError among the causes."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, CooldownError):
            return cur
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return None


class TransientHTTPError(Exception):
    """A 5xx or a 202 'still processing' reply; worth retrying shortly."""


@dataclass
class CallContext:
    cache_key: str
    request_sha256: str
    model: str
    prompt_hash: str
    document_id: int | None = None
    call_ids: list[int] = field(default_factory=list)


_ctx: contextvars.ContextVar[CallContext | None] = contextvars.ContextVar("llm_call_ctx", default=None)


def cache_key(model: str, prompt_hash: str, schema_ver: str, inputs: dict, params: dict) -> str:
    payload = {"model": model, "prompt_hash": prompt_hash, "schema_ver": schema_ver, "input": inputs,
               "params": params}
    return hashlib.sha256(rfc8785.dumps(payload)).hexdigest()


def _redact_images(body: dict) -> dict:
    """Replace inline base64 images with their hash and size before the request is logged."""
    body = json.loads(json.dumps(body))
    for msg in body.get("messages", []):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            url = (part.get("image_url") or {}).get("url", "") if isinstance(part, dict) else ""
            if url.startswith("data:"):
                raw = base64.b64decode(url.split(",", 1)[1])
                part["image_url"] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return body


class Cooldown:
    """Shared rate-limit pause, persisted in llm_state so a new process honours a running cooldown."""

    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions
        self._until: dt.datetime | None = None
        self._consecutive = 0
        self._loaded = False
        self._lock = asyncio.Lock()

    def _load(self) -> None:
        with self._sessions() as s:
            state = s.get(LlmState, 1)
            if state:
                self._until, self._consecutive = state.cooldown_until, state.consecutive_429

    def _save(self, last_429: dt.datetime | None) -> None:
        with self._sessions.begin() as s:
            values: dict[str, Any] = {"cooldown_until": self._until, "consecutive_429": self._consecutive}
            if last_429:
                values["last_429_at"] = last_429
            s.execute(update(LlmState).where(LlmState.id == 1).values(**values))

    async def check(self) -> None:
        if not self._loaded:
            await asyncio.to_thread(self._load)
            self._loaded = True
        now = dt.datetime.now(dt.timezone.utc)
        if self._until and self._until > now:
            raise CooldownError(self._until)

    async def trip(self) -> dt.datetime:
        async with self._lock:
            now = dt.datetime.now(dt.timezone.utc)
            if self._until and self._until > now:  # another call already tripped it
                return self._until
            self._consecutive += 1
            seconds = min(COOLDOWN_MAX_S, COOLDOWN_START_S * 2 ** (self._consecutive - 1))
            self._until = now + dt.timedelta(seconds=seconds * random.uniform(1.0, 1.2))
            await asyncio.to_thread(self._save, now)
            return self._until

    async def succeeded(self) -> None:
        if self._consecutive:
            async with self._lock:
                self._consecutive, self._until = 0, None
                await asyncio.to_thread(self._save, None)


def env_transport(base_url: str) -> httpx.AsyncHTTPTransport:
    """A plain transport that still honours HTTPS_PROXY / NO_PROXY (httpx ignores them once a custom transport
    is supplied)."""
    url = httpx.URL(base_url)
    proxies = urllib.request.getproxies()
    proxy = proxies.get(url.scheme) or proxies.get("all")
    if proxy and not urllib.request.proxy_bypass(url.host):
        return httpx.AsyncHTTPTransport(retries=0, proxy=proxy)
    return httpx.AsyncHTTPTransport(retries=0)


class PacedTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, rpm: int, max_in_flight: int, cooldown: Cooldown, sessions: sessionmaker[Session],
                 inner: httpx.AsyncBaseTransport | None = None):
        self._inner = inner or httpx.AsyncHTTPTransport(retries=0)
        self._limiter = AsyncLimiter(1, 60 / rpm)  # evenly spaced, no bursts
        self._in_flight = asyncio.Semaphore(max_in_flight)
        self._cooldown = cooldown
        self._sessions = sessions

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(SHORT_RETRIES), wait=RETRY_WAIT, reraise=True,
            retry=retry_if_exception(lambda e: isinstance(e, (TransientHTTPError, httpx.TransportError))))
        async for attempt in retrying:
            with attempt:
                return await self._send_once(request)
        raise AssertionError("unreachable")

    async def _send_once(self, request: httpx.Request) -> httpx.Response:
        await self._cooldown.check()
        async with self._in_flight:
            await self._limiter.acquire()
            await self._cooldown.check()  # a 429 may have arrived while this request waited for its slot
            started = time.monotonic()
            error = None
            try:
                raw = await self._inner.handle_async_request(request)
                body = await raw.aread()
                response = httpx.Response(raw.status_code, headers=raw.headers, content=body, request=request)
            except httpx.TransportError as exc:
                error, response = f"{type(exc).__name__}: {exc}", None
            latency_ms = round((time.monotonic() - started) * 1000)
        await asyncio.to_thread(self._log, request, response, latency_ms, error)
        if response is None:
            raise httpx.TransportError(error or "transport error")
        if response.status_code == 429:
            raise CooldownError(await self._cooldown.trip())
        if response.status_code == 202 or response.status_code >= 500:
            raise TransientHTTPError(f"HTTP {response.status_code}")
        if response.status_code == 200:
            await self._cooldown.succeeded()
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()

    def _log(self, request: httpx.Request, response: httpx.Response | None, latency_ms: int,
             error: str | None) -> None:
        ctx = _ctx.get()
        try:
            req_body = _redact_images(json.loads(request.content or b"{}"))
        except ValueError:
            req_body = {"_unparsed": True}
        data: dict[str, Any] = {}
        if response is not None:
            try:
                data = response.json()
            except ValueError:
                data = {"_text": response.text[:4000]}
        if not isinstance(data, dict):
            data = {"_body": data}
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        row = LlmCall(
            cache_key=ctx.cache_key if ctx else "", request_sha256=ctx.request_sha256 if ctx else "",
            model=req_body.get("model", ""), prompt_hash=ctx.prompt_hash if ctx else "",
            attempt=len(ctx.call_ids) + 1 if ctx else 1, request=req_body, response=data or None,
            content=message.get("content"), reasoning=message.get("reasoning_content") or message.get("reasoning"),
            finish_reason=choice.get("finish_reason"), http_status=response.status_code if response else None,
            nvcf_reqid=response.headers.get("nvcf-reqid") if response else None, latency_ms=latency_ms,
            tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"), ok=False,
            error=error or (None if response is None or response.status_code == 200 else json.dumps(data)[:2000]),
            document_id=ctx.document_id if ctx else None)
        with self._sessions.begin() as s:
            s.add(row)
            s.flush()
            if ctx:
                ctx.call_ids.append(row.id)


@dataclass
class StructuredResult:
    value: BaseModel
    cache_hit: bool
    call_id: int | None
    attempts: int
    partial: bool = False  # the strict check failed even after the retry; this is the last answer, leniently read


def _lenient(exc: Exception, response_model: type[T]) -> T | None:
    """After the retry is used up, keep the last answer if it parses without the strict checks."""
    completion = getattr(exc, "last_completion", None)
    try:
        args = completion.choices[0].message.tool_calls[0].function.arguments  # type: ignore[union-attr]
        return response_model.model_validate_json(args)
    except (AttributeError, IndexError, TypeError, ValidationError):
        return None


class LlmClient:
    def __init__(self, settings: Settings, sessions: sessionmaker[Session],
                 inner_transport: httpx.AsyncBaseTransport | None = None):
        if settings.nvidia_api_key is None:
            raise RuntimeError("NVIDIA_API_KEY is not set (project-root .env)")
        self._sessions = sessions
        self.cooldown = Cooldown(sessions)
        transport = PacedTransport(rpm=settings.llm_rpm, max_in_flight=settings.llm_max_in_flight,
                                   cooldown=self.cooldown, sessions=sessions,
                                   inner=inner_transport or env_transport(settings.nvidia_base_url))
        self._http = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(300.0, connect=20.0))
        openai = AsyncOpenAI(base_url=settings.nvidia_base_url, api_key=settings.nvidia_api_key.get_secret_value(),
                             http_client=self._http, max_retries=0)
        self._instructor = instructor.from_openai(openai, mode=instructor.Mode.TOOLS)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _cached(self, key: str, response_model: type[T], since: dt.datetime | None) -> tuple[T, int] | None:
        with self._sessions() as s:
            query = select(LlmCall).where(LlmCall.cache_key == key, LlmCall.ok)
            if since is not None:  # a 'fresh' run reuses only answers it got itself (e.g. before a restart)
                query = query.where(LlmCall.created_at >= since)
            row = s.scalars(query.order_by(LlmCall.id.desc()).limit(1)).first()
        if row is None or not row.response:
            return None
        try:
            args = row.response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
            return response_model.model_validate_json(args), row.id
        except (KeyError, IndexError, TypeError, ValidationError):
            return None

    def _mark_ok(self, key: str, call_id: int) -> None:
        with self._sessions.begin() as s:
            s.execute(update(LlmCall).where(LlmCall.cache_key == key, LlmCall.ok).values(ok=False))
            s.execute(update(LlmCall).where(LlmCall.id == call_id).values(ok=True))

    async def structured(self, *, model: str, messages: list[dict], response_model: type[T], params: dict,
                         prompt_hash: str, schema_ver: str, inputs: dict, document_id: int | None = None,
                         use_cache: bool = True, cache_since: dt.datetime | None = None,
                         context: dict | None = None) -> StructuredResult:
        """Fill `response_model` via a forced tool call. `inputs` fingerprints the input (file hash, pages,
        preprocessing) for the cache key; `params` are the request parameters (max_tokens, reasoning, ...).
        `context` is passed to validation on live calls (stricter checks that earn one retry); cached answers are
        validated without it."""
        key = cache_key(model, prompt_hash, schema_ver, inputs, params)
        if use_cache:
            hit = await asyncio.to_thread(self._cached, key, response_model, cache_since)
            if hit:
                return StructuredResult(hit[0], True, hit[1], 0)
        request_sha = hashlib.sha256(rfc8785.dumps({"model": model, "messages": messages, "params": params}))
        ctx = CallContext(key, request_sha.hexdigest(), model, prompt_hash, document_id)
        token = _ctx.set(ctx)
        try:
            extra_body = params.get("extra_body")
            kwargs = {k: v for k, v in params.items() if k != "extra_body"}
            value, _completion = await self._instructor.chat.completions.create_with_completion(
                model=model, messages=messages, response_model=response_model, max_retries=1,
                extra_body=extra_body, context=context, **kwargs)
            partial = False
        except Exception as exc:
            cooldown = _cooldown_in_chain(exc)
            if cooldown is not None:
                raise cooldown from None
            value = _lenient(exc, response_model)
            if value is None:
                raise
            partial = True
        finally:
            _ctx.reset(token)
        await asyncio.to_thread(self._mark_ok, key, ctx.call_ids[-1])
        return StructuredResult(value, False, ctx.call_ids[-1], len(ctx.call_ids), partial)
