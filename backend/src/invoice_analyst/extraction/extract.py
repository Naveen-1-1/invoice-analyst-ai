"""One document through intake and extraction: store -> prepare (text layer, page images) -> model -> save.

Route (plan, step 2): a PDF whose every page has a real text layer goes to the text model with that text;
anything else (images, scanned or partly scanned PDFs) goes to the vision model as page images.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import datetime as dt
import hashlib
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from ..audit import chain
from ..config import Settings
from ..db.models import Document, Extraction, FieldValue, Invoice, InvoiceLine, Page
from ..ingest import files, images, pdf
from ..ingest.quality import measure as quality_measure
from ..ocr import rapid
from ..llm.client import CooldownError, LlmClient
from . import prompts
from .confidence import calibrated_accuracy
from .evidence import FieldEvidence, gather, invoice_tier
from .grounding import PageIndex
from .resolve import resolve
from .schemas import SCHEMA_VERSION, STRICT, FileExtraction

TEXT_PREPROCESS = "pdf-text-1"
OCR_HINT_VERSION = "rapidocr-ppocrv6-lines-1"

# Decision 22 (2026-10-10): low reasoning on both models; GLM also clears its thinking from the chat template
TEXT_PARAMS: dict[str, Any] = {"max_tokens": 16000, "reasoning_effort": "low",
                               "extra_body": {"chat_template_kwargs": {"clear_thinking": True}}}
IMAGE_PARAMS: dict[str, Any] = {"max_tokens": 8192, "reasoning_effort": "low"}


@dataclass
class Outcome:
    document_id: int
    route: str
    model: str
    extraction: FileExtraction | None
    cache_hit: bool = False
    seconds: float = 0.0
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    evidence: list[list[FieldEvidence]] = field(default_factory=list)  # per invoice in the file


def _transition(s: Session, doc: Document, state: str, data: dict | None = None) -> None:
    old, doc.state = doc.state, state
    chain.record(s, doc.workspace_id, "system", "document.state_changed", "document", doc.id,
                 {"from": old, "to": state, **(data or {})})


def _prepare(s: Session, doc: Document, stored: files.StoredFile, pages_dir: Path) -> tuple[str, Any]:
    """Fill in pages and the route. Returns (route, payload): layout text for the text route, else PIL pages."""
    if stored.mime == "application/pdf":
        text = pdf.read_text(stored.path)
        rendered = pdf.render_pages(stored.path, pages_dir)
        doc.text_layer, doc.page_count = text.layer, len(text.pages)
        if text.layer == "full":
            for p, img_path in zip(text.pages, rendered, strict=True):
                s.add(Page(document_id=doc.id, page_no=p.page_no, width=p.width_pt, height=p.height_pt, unit="pt",
                           image_path=str(img_path), words=p.words, quality={}))
            return "text_llm", text.marked_text()
        # Scanned or partly scanned: the page renders go to the vision model, and OCR supplies the words
        imgs = [Image.open(pth).convert("RGB") for pth in rendered]
        for n, (img, img_path) in enumerate(zip(imgs, rendered, strict=True), 1):
            s.add(_image_page(doc.id, n, img, img_path, pages_dir))
        return "image_llm", imgs
    pages = images.load_pages(stored.path)
    doc.text_layer, doc.page_count = "n/a", len(pages)
    for i, img in enumerate(pages, 1):
        if len(pages) == 1 and stored.mime in ("image/jpeg", "image/png"):
            img_path = stored.path  # the original is already viewable
        else:
            img_path = pages_dir / f"{i}.png"
            pages_dir.mkdir(parents=True, exist_ok=True)
            if not img_path.exists():
                img.save(img_path, "PNG")
        s.add(_image_page(doc.id, i, img, img_path, pages_dir))
    return "image_llm", pages


def _image_page(document_id: int, page_no: int, img: Image.Image, img_path: Path, pages_dir: Path) -> Page:
    """A photo/scan page: OCR words (to locate values) and readability measures (for confidence)."""
    words = rapid.cached_words(img, pages_dir, page_no)
    confs = [w["conf"] for w in words]
    quality = {**quality_measure(img), "ocr_words": len(words),
               "ocr_conf": round(sum(confs) / len(confs), 3) if confs else 0.0}
    return Page(document_id=document_id, page_no=page_no, width=img.width, height=img.height, unit="px",
                image_path=str(img_path), words=words, quality=quality)


def _messages(route: str, payload: Any, source: bytes, ocr_hint: bool = False) -> tuple[list[dict], dict]:
    """Chat messages and the input fingerprint for the cache key."""
    system = {"role": "system", "content": prompts.SYSTEM}
    if route == "text_llm":
        return [system, {"role": "user", "content": prompts.USER_TEXT.format(text=payload)}], {
            "preprocess": TEXT_PREPROCESS}
    parts: list[dict] = []
    encoded = []
    for img in payload:
        jpeg, _info = images.encode_for_model(img, source if len(payload) == 1 else None)
        encoded.append(hashlib.sha256(jpeg).hexdigest())
        parts.append({"type": "image_url",
                      "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}})
    n = len(payload)
    intro = prompts.USER_IMAGE.format(pages="It has 1 page." if n == 1 else f"It has {n} pages, in order.")
    fingerprint: dict[str, Any] = {"preprocess": images.PREPROCESS_VERSION, "images": encoded}
    if ocr_hint:
        pages = [f"--- page {i} ---\n" + "\n".join(rapid.text_lines(img)) for i, img in enumerate(payload, 1)]
        intro += prompts.OCR_HINT.format(ocr="\n".join(pages))
        fingerprint["ocr_hint"] = OCR_HINT_VERSION
    return [system, {"role": "user", "content": [{"type": "text", "text": intro}, *parts]}], fingerprint


def _dec(x: float | None) -> Decimal | None:
    return None if x is None else Decimal(str(x))


def _jsonable(x: Any) -> Any:
    return x if x is None or isinstance(x, (str, int, float, bool)) else str(x)


def _field_rows(invoice_id: int, line_ids: dict[int, int], evidence: list[FieldEvidence], route: str) -> list[FieldValue]:
    rows = []
    for ev in evidence:
        loc = ev.located
        source = ("pdf_text" if route == "text_llm" else "ocr") if loc.grounding in ("exact", "fuzzy") else "llm"
        rows.append(FieldValue(invoice_id=invoice_id, invoice_line_id=line_ids.get(ev.line_no) if ev.line_no else None,
                               field=ev.field, raw_text=ev.raw_text, value=_jsonable(ev.value), page_no=loc.page_no,
                               bbox=loc.bbox, evidence_text=loc.matched, grounding=loc.grounding, source=source,
                               tier=ev.tier, confidence=calibrated_accuracy(route, ev.tier), reasons=ev.reasons))
    return rows


def _save_extraction(s: Session, doc: Document, route: str, model: str, params: dict,
                     value: FileExtraction | None, error: str | None,
                     evidence: list[list[FieldEvidence]] | None = None) -> None:
    resolved = [dataclasses.asdict(resolve(inv)) for inv in value.invoices] if value else None
    ext = Extraction(document_id=doc.id, route=route, model=model, prompt_hash=prompts.PROMPT_HASH,
                     schema_ver=SCHEMA_VERSION, preprocess_ver=TEXT_PREPROCESS if route == "text_llm"
                     else images.PREPROCESS_VERSION, status="failed" if error else "ok",
                     raw=value.model_dump() if value else None, normalized={"invoices": resolved, "params": params},
                     error=error)
    s.add(ext)
    s.flush()
    if value is None:
        return
    for idx, (inv, r) in enumerate(zip(value.invoices, resolved, strict=True)):
        row = Invoice(workspace_id=doc.workspace_id, document_id=doc.id, extraction_id=ext.id, child_index=idx,
                      doc_type=inv.document_type, vendor_name=r["vendor_name"], currency=r["currency"],
                      invoice_number=r["invoice_number"],
                      invoice_number_norm=r["invoice_number_norm"],
                      invoice_date=dt.date.fromisoformat(r["invoice_date"]) if r["invoice_date"] else None,
                      due_date=dt.date.fromisoformat(r["due_date"]) if r["due_date"] else None,
                      due_date_source="printed" if r["due_date"] else None, subtotal=_dec(r["subtotal"]),
                      tax=_dec(r["tax"]), total=_dec(r["total"]), payable_amount=_dec(r["total"]),
                      payable_currency=r["currency"],
                      conf_tier=invoice_tier(evidence[idx]) if evidence else None)
        s.add(row)
        s.flush()
        line_ids: dict[int, int] = {}
        for n, ln in enumerate(inv.lines, 1):
            line_row = InvoiceLine(invoice_id=row.id, line_no=n, kind="item", description=ln.description,
                                   item_code=ln.item_code, qty=_dec(ln.quantity), unit_price=_dec(ln.unit_price),
                                   amount=_dec(ln.amount), vat_rate=_dec(ln.vat_rate_percent))
            s.add(line_row)
            s.flush()
            line_ids[n] = line_row.id
        for n, ch in enumerate(inv.charges, len(inv.lines) + 1):
            s.add(InvoiceLine(invoice_id=row.id, line_no=n, kind="discount" if ch.kind == "discount" else "charge",
                              description=ch.description, amount=_dec(ch.amount)))
        if evidence:
            s.add_all(_field_rows(row.id, line_ids, evidence[idx], route))


async def extract_document(*, path: Path, workspace_id: int, run_id: int | None, settings: Settings,
                           sessions: sessionmaker[Session], llm: LlmClient, bench_doc_id: str | None = None,
                           use_cache: bool = True, overrides: dict | None = None, ocr_hint: bool = False,
                           feed_seq: int | None = None, received_at: dt.datetime | None = None) -> Outcome:
    started = time.monotonic()
    data = path.read_bytes()
    stored = files.store(data, settings.storage_dir)
    with sessions.begin() as s:
        doc = Document(workspace_id=workspace_id, run_id=run_id, sha256=stored.sha256, filename=path.name,
                       mime=stored.mime, bytes=stored.bytes, bench_doc_id=bench_doc_id, feed_seq=feed_seq,
                       received_at=received_at or dt.datetime.now(dt.timezone.utc), state="RECEIVED",
                       storage_path=str(stored.path))
        s.add(doc)
        s.flush()
        chain.record(s, workspace_id, "system", "document.received", "document", doc.id,
                     {"filename": path.name, "sha256": stored.sha256, "mime": stored.mime})
        route, payload = _prepare(s, doc, stored, settings.storage_dir / "pages" / stored.sha256)
        doc.route = route
        _transition(s, doc, "PREPARED", {"route": route, "text_layer": doc.text_layer, "pages": doc.page_count})
        doc_id = doc.id
    model = settings.text_model if route == "text_llm" else settings.image_model
    params = {**(TEXT_PARAMS if route == "text_llm" else IMAGE_PARAMS), **(overrides or {})}
    messages, fingerprint = await asyncio.to_thread(_messages, route, payload, data, ocr_hint)
    inputs = {"file_sha256": stored.sha256, "route": route, **fingerprint}
    value, error, hit, partial = None, None, False, False
    try:
        result = await llm.structured(model=model, messages=messages, response_model=FileExtraction, params=params,
                                      prompt_hash=prompts.PROMPT_HASH, schema_ver=SCHEMA_VERSION, inputs=inputs,
                                      document_id=doc_id, use_cache=use_cache, context=STRICT)
        value, hit = result.value, result.cache_hit  # type: ignore[assignment]
        partial = result.partial
    except CooldownError:
        raise  # the caller waits for the cooldown and retries this document
    except Exception as exc:  # anything else is recorded on the document as a failed extraction
        error = f"{type(exc).__name__}: {exc}"[:2000]
    evidence = await asyncio.to_thread(_finish, sessions, doc_id, route, model, params, value, error, hit, partial)
    return Outcome(doc_id, route, model, value, hit, round(time.monotonic() - started, 2), error,
                   ["partial"] if partial else [], evidence)


def _finish(sessions: sessionmaker[Session], doc_id: int, route: str, model: str, params: dict,
            value: FileExtraction | None, error: str | None, hit: bool, partial: bool) -> list[list[FieldEvidence]]:
    """Locate every value on the page, assess confidence, and save the extraction (in a worker thread)."""
    with sessions.begin() as s:
        doc = s.get(Document, doc_id)
        pages = sorted(s.query(Page).filter(Page.document_id == doc_id).all(), key=lambda p: p.page_no)
        index = [PageIndex(p.page_no, p.words) for p in pages]
        quality = {p.page_no: p.quality for p in pages}
        evidence = [gather(inv, resolve(inv), index, route, quality) for inv in value.invoices] if value else []
        _save_extraction(s, doc, route, model, params, value, error, evidence)
        if error:
            _transition(s, doc, "FAILED", {"error": error[:300]})
        else:
            kinds = {inv.document_type for inv in value.invoices}  # type: ignore[union-attr]
            state = ("SPLIT" if len(value.invoices) > 1  # type: ignore[union-attr]
                     else "NOT_INVOICE" if kinds <= {"statement", "other"} else "EXTRACTED")
            tiers = [invoice_tier(ev) for ev in evidence]
            _transition(s, doc, state, {"model": model, "cache_hit": hit, "partial": partial,  # type: ignore[union-attr]
                                        "invoices": len(value.invoices), "tiers": tiers})
    return evidence
