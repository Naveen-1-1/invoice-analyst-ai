"""API response shapes. These also define the typed client the frontend generates (orval, from /openapi.json)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

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
    vendor_id: int | None
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
    payments: list["PaymentItemOut"]
    entries: list["JournalEntryOut"]


class EventOut(BaseModel):
    seq: int
    occurred_at: dt.datetime
    actor: str
    action: str
    data: dict[str, Any]
    subject_type: str
    subject_id: int | None


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
    invoice_id: int | None
    labels: list[str]
    payment: str | None  # planned / waiting / paid, for the invoice's approved amounts


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


class ReviewItemOut(BaseModel):
    task_id: int
    invoice_id: int
    document_id: int
    workspace_id: int
    workspace: str
    seq: int | None
    filename: str
    doc_type: str
    state: str
    vendor_name: str | None
    invoice_number: str | None
    due_date: dt.date | None
    total: Decimal | None
    currency: str | None
    conf_tier: str | None
    labels: list[str]
    blocks: int
    created_at: dt.datetime


class ReasonLine(BaseModel):
    kind: str  # ok / review / block / note / override / partial
    code: str | None = None
    text: str


class TaskOut(BaseModel):
    id: int
    status: str
    labels: list[str]
    reasons: list[ReasonLine]
    created_at: dt.datetime
    resolved_at: dt.datetime | None


class DecisionOut(BaseModel):
    id: int
    action: str
    note: str
    decided_at: dt.datetime


class ApprovalOut(BaseModel):
    id: int
    kind: str  # auto / manual
    amount: Decimal
    currency: str
    reasons: list[ReasonLine]
    rules_ver: str
    created_at: dt.datetime


class MatchOut(BaseModel):
    line_no: int
    description: str | None
    po_line_no: int | None
    qty_invoiced: Decimal | None
    qty_received_avail: Decimal | None
    qty_approved: Decimal | None
    price_inv: Decimal | None
    price_po: Decimal | None
    variance_pct: Decimal | None
    outcome: str


class SplitLineOut(BaseModel):
    line_no: int
    description: str | None
    amount: Decimal
    approved: Decimal
    why: str


class SplitOut(BaseModel):
    currency: str
    total: Decimal
    approved_net: Decimal
    approved_tax: Decimal
    approved: Decimal
    held: Decimal
    lines: list[SplitLineOut]


class StepOut(BaseModel):
    status: str
    explanation: str | None
    started_at: dt.datetime | None
    finished_at: dt.datetime | None


class CapOut(BaseModel):
    usd_estimate: Decimal | None
    cap_usd: Decimal
    rates_as_of: str


class ReviewDetail(BaseModel):
    invoice_id: int
    document_id: int
    workspace_id: int
    state: str
    doc_type: str
    labels: list[str]
    task: TaskOut | None
    decisions: list[DecisionOut]
    approvals: list[ApprovalOut]
    approved_so_far: Decimal
    po_id: int | None
    po_number: str | None
    matches: list[MatchOut]
    partial: SplitOut | None
    blocks: list[str]
    actions: list[str]
    correctable: list[str]
    reread: StepOut | None
    cap: CapOut


class CorrectionIn(BaseModel):
    field: str
    text: str | None
    page_no: int | None = None
    bbox: list[float] | None = None


class DecisionIn(BaseModel):
    action: Literal["approve", "approve_partial", "reject", "hold", "release"]
    note: str
    override: bool = False


class ReprocessIn(BaseModel):
    note: str


class WordOut(BaseModel):
    t: str
    bbox: list[float]


class TodayOut(BaseModel):
    date: dt.date
    live: bool  # the simulated clock is running; today can't be set


class PaymentItemOut(BaseModel):
    id: int
    invoice_id: int
    document_id: int
    vendor: str | None
    entity: str | None
    invoice_number: str | None
    doc_type: str
    amount: Decimal  # adds to what we owe; negative for a credit note
    discount: Decimal
    paid: Decimal  # amount less discount
    currency: str
    status: str  # planned / waiting / paid
    due_date: dt.date | None
    due_source: str | None
    run_date: dt.date | None
    to_account: str | None  # last digits of the account paid (or to be paid)
    reason: str


class PaymentRunOut(BaseModel):
    id: int
    run_date: dt.date
    status: str  # planned / executed
    executed_at: dt.datetime | None
    items: int
    totals: dict[str, Decimal]  # per currency, what leaves the bank
    discounts: dict[str, Decimal]


class PaymentsOut(BaseModel):
    workspace_id: int
    today: TodayOut
    runs: list[PaymentRunOut]
    waiting: list[PaymentItemOut]
    due_now: int  # planned runs dated today or earlier


class TransferOut(BaseModel):
    vendor: str | None
    currency: str
    amount: Decimal
    to_account: str | None
    items: list[PaymentItemOut]


class PaymentRunDetail(BaseModel):
    run: PaymentRunOut
    today: TodayOut
    transfers: list[TransferOut]


class RunResultOut(BaseModel):
    executed: list[str]  # run dates
    today: TodayOut
    message: str


class TodayIn(BaseModel):
    date: dt.date


class JournalLineOut(BaseModel):
    account: str
    account_name: str
    currency: str
    debit: Decimal
    credit: Decimal
    vendor: str | None


class JournalEntryOut(BaseModel):
    id: int
    entry_date: dt.date
    kind: str
    memo: str
    entity: str | None
    invoice_id: int | None
    document_id: int | None
    posted_at: dt.datetime
    lines: list[JournalLineOut]


class JournalOut(BaseModel):
    total: int
    entries: list[JournalEntryOut]


class TrialRowOut(BaseModel):
    currency: str
    account: str
    name: str
    type: str
    debit: Decimal
    credit: Decimal
    balance: Decimal  # debit-positive


class ReconciliationOut(BaseModel):
    currency: str
    ledger: Decimal
    open_items: Decimal
    ok: bool


class TrialBalanceOut(BaseModel):
    rows: list[TrialRowOut]
    balanced: dict[str, bool]
    reconciled: list[ReconciliationOut]


class OpenItemOut(BaseModel):
    item_id: int
    invoice_id: int
    document_id: int
    vendor: str | None
    entity: str | None
    invoice_number: str | None
    currency: str
    amount: Decimal
    due_date: dt.date | None
    status: str
    run_date: dt.date | None
    bucket: str
    days_overdue: int


class PayablesOut(BaseModel):
    today: TodayOut
    buckets: list[str]
    totals: dict[str, dict[str, Decimal]]  # currency -> bucket -> amount
    items: list[OpenItemOut]


class EntityOut(BaseModel):
    id: int
    name: str


class VerdictOut(BaseModel):
    ok: bool
    events: int
    broken_at: int | None
    problem: str | None
    words: str


class AuditEventOut(BaseModel):
    seq: int
    occurred_at: dt.datetime
    actor: str
    action: str
    subject_type: str
    subject_id: int | None
    data: dict[str, Any]
    hash: str


class AuditOut(BaseModel):
    total: int
    actions: list[str]
    events: list[AuditEventOut]


class EvalEntryOut(BaseModel):
    kind: str  # dev / final / after_final
    set: str
    logged_at: dt.datetime
    commit: str
    bench_run: str
    inbox_run: str | None
    official: dict[str, Any]
    extended: dict[str, Any]
    confidence: dict[str, Any]
    ap: dict[str, Any] | None
    documents: dict[str, int]  # benchmark doc id -> document id in that run (to link errors)


class BankAccountOut(BaseModel):
    kind: str
    account: str  # last digits only
    bank_name: str | None
    bic: str | None
    branch: str | None
    valid_from: dt.date | None
    valid_to: dt.date | None  # set when replaced; payments go to the open one
    source: str | None


class VendorRow(BaseModel):
    id: int
    code: str
    name: str
    country: str | None
    status: str
    origin: str | None
    terms_days: int | None
    discount: str | None  # e.g. "2% in 10 days"
    account: str | None  # the open account's last digits
    invoices: int
    purchase_orders: int


class HistoryOut(BaseModel):
    invoice_number: str | None
    invoice_date: dt.date
    amount: Decimal
    currency: str


class VendorInvoiceOut(BaseModel):
    invoice_id: int
    document_id: int
    invoice_number: str | None
    invoice_date: dt.date | None
    total: Decimal | None
    currency: str | None
    state: str
    payment: str | None


class PoRow(BaseModel):
    id: int
    po_number: str
    vendor_id: int | None
    vendor: str | None
    entity: str | None
    currency: str
    order_date: dt.date | None
    status: str
    lines: int
    ordered: Decimal  # amount
    received_share: float | None  # of ordered quantity
    invoiced_share: float | None


class VendorDetail(BaseModel):
    id: int
    workspace_id: int
    code: str
    name: str
    aliases: list[str]
    country: str | None
    address: str | None
    tax_id: str | None
    email: str | None
    status: str
    origin: str | None
    terms_days: int | None
    discount: str | None
    default_gl: str | None
    accounts: list[BankAccountOut]
    history: list[HistoryOut]
    invoices: list[VendorInvoiceOut]
    purchase_orders: list[PoRow]


class PoLineOut(BaseModel):
    line_no: int
    item_code: str | None
    description: str | None
    qty: Decimal | None  # ordered; none for services (matched on amount)
    unit_price: Decimal | None
    amount: Decimal | None
    gl_account: str | None
    received: Decimal | None
    invoiced: Decimal | None  # quantity invoices have used up (rejected invoices excluded)
    open: Decimal | None  # received and not yet invoiced


class ReceiptLineOut(BaseModel):
    line_no: int
    description: str | None
    qty: Decimal


class ReceiptOut(BaseModel):
    id: int
    grn_number: str
    received_date: dt.date
    lines: list[ReceiptLineOut]


class PoInvoiceOut(BaseModel):
    invoice_id: int
    document_id: int
    invoice_number: str | None
    state: str
    total: Decimal | None
    currency: str | None


class PoDetail(BaseModel):
    po: PoRow
    lines: list[PoLineOut]
    receipts: list[ReceiptOut]
    invoices: list[PoInvoiceOut]


class DeliveryRow(BaseModel):
    id: int
    grn_number: str
    received_date: dt.date
    po_id: int
    po_number: str
    vendor: str | None
    lines: int
    quantity: Decimal


class CallPartOut(BaseModel):
    kind: str  # text / image
    text: str | None = None
    image_bytes: int | None = None  # images aren't stored with the call: their size and fingerprint are


class CallMessageOut(BaseModel):
    role: str
    parts: list[CallPartOut]


class ModelCallOut(BaseModel):
    id: int
    model: str
    created_at: dt.datetime
    attempt: int
    ok: bool
    http_status: int | None
    finish_reason: str | None
    latency_ms: int | None
    tokens_in: int | None
    tokens_out: int | None
    error: str | None
    params: dict[str, Any]  # reasoning effort, max tokens, ...
    this_document: bool  # made for this document (else for an earlier copy of the same file)
    workspace: str | None
    messages: list[CallMessageOut]
    answer: str | None  # the tool-call arguments (or text), pretty-printed
    reasoning: str | None
