"""API response shapes. These also define the typed client the frontend generates (orval, from /openapi.json)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class RunOut(BaseModel):
    id: int
    workspace_id: int
    mode: str
    label: str | None
    dataset: str | None
    status: str
    started_at: dt.datetime
    finished_at: dt.datetime | None
    documents: int
    tiers: dict[str, int]


class DocumentRow(BaseModel):
    id: int
    bench_doc_id: str | None
    filename: str
    route: str | None
    state: str
    page_count: int | None
    tier: str | None
    vendor: str | None
    invoice_number: str | None
    total: Decimal | None
    currency: str | None


class PageOut(BaseModel):
    id: int
    page_no: int
    width: float
    height: float
    unit: str
    image_url: str
    quality: dict[str, Any]


class Reason(BaseModel):
    code: str
    text: str
    effect: str


class FieldOut(BaseModel):
    id: int
    field: str
    line_no: int | None
    value: Any
    raw_text: str | None
    page_no: int | None
    bbox: list[float] | None
    evidence_text: str | None
    grounding: str
    source: str
    tier: str | None
    confidence: float | None
    reasons: list[Reason]


class LineOut(BaseModel):
    id: int
    line_no: int
    kind: str
    description: str | None
    qty: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


class CheckOut(BaseModel):
    id: int
    code: str
    category: str
    outcome: str
    severity: str
    message: str
    evidence: dict[str, Any]
    related_invoice_id: int | None


class InvoiceOut(BaseModel):
    id: int
    child_index: int
    doc_type: str
    state: str
    conf_tier: str | None
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: dt.date | None
    due_date: dt.date | None
    currency: str | None
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    fields: list[FieldOut]
    lines: list[LineOut]
    checks: list[CheckOut]


class EventOut(BaseModel):
    seq: int
    occurred_at: dt.datetime
    actor: str
    action: str
    data: dict[str, Any]


class DocumentDetail(BaseModel):
    id: int
    bench_doc_id: str | None
    filename: str
    mime: str
    route: str | None
    text_layer: str | None
    state: str
    received_at: dt.datetime
    file_url: str
    pages: list[PageOut]
    invoices: list[InvoiceOut]
    events: list[EventOut]
    llm_calls: int


class WorkspaceOut(BaseModel):
    id: int
    name: str
    kind: str
    dataset: str
    created_at: dt.datetime
    documents: int
    states: dict[str, int]


class ClockOut(BaseModel):
    """The simulated clock: simulated date = start_sim + (now - start_real) / seconds_per_day days."""
    start_real: dt.datetime
    start_sim: dt.date
    seconds_per_day: float


class CardOut(BaseModel):
    document_id: int
    seq: int | None
    ref: str | None
    filename: str
    state: str
    route: str | None
    received_at: dt.datetime
    invoices: int
    vendor: str | None
    invoice_number: str | None
    total: Decimal | None
    currency: str | None
    tier: str | None
    invoice_state: str | None
    findings: dict[str, int]
    codes: list[str]


class BoardOut(BaseModel):
    workspace: WorkspaceOut
    clock: ClockOut | None
    run_status: str | None
    expected: int | None
    llm_paused_until: dt.datetime | None
    worker_online: bool
    cards: list[CardOut]


class UploadOut(BaseModel):
    filename: str
    document_id: int | None = None
    workspace_id: int | None = None
    error: str | None = None
