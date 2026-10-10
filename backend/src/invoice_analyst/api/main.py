"""The HTTP API behind the web app (local, single user). Run: make api"""

from __future__ import annotations

import contextlib
from collections import Counter
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db.models import (AuditEvent, CheckResult, Document, FieldValue, Invoice, InvoiceLine, JournalEntry, LlmCall,
                         Page, PaymentItem, Run, Workspace)
from ..worker.app import app as queue
from . import board, money, review
from . import schemas as out
from .deps import db


def _operation_id(route: APIRoute) -> str:
    return route.name  # clean names for the generated client: listRuns, getDocument, ...


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    async with queue.open_async():  # uploads schedule their reading on the job queue
        yield


app = FastAPI(title="invoice-analyst-ai", version="0.1", generate_unique_id_function=_operation_id, lifespan=lifespan)
app.include_router(board.router)
app.include_router(review.router)
app.include_router(money.router)


def _inside_storage(path: str) -> Path:
    """Only serve files the app itself stored (never an arbitrary path from the database)."""
    resolved = Path(path).resolve()
    root = settings().storage_dir.resolve()
    if root not in resolved.parents:
        raise HTTPException(404, "file not available")
    if not resolved.is_file():
        raise HTTPException(404, "file missing")
    return resolved


@app.get("/api/runs", response_model=list[out.RunOut], name="listRuns")
def list_runs(s: Session = Depends(db)) -> list[out.RunOut]:
    runs = s.execute(select(Run, Workspace).join(Workspace, Workspace.id == Run.workspace_id)
                     .order_by(Run.id.desc())).all()
    result = []
    for run, ws in runs:
        tiers = Counter(t for (t,) in s.execute(
            select(Invoice.conf_tier).join(Document, Document.id == Invoice.document_id)
            .where(Document.run_id == run.id, Invoice.child_index == 0)).all())
        docs = s.scalar(select(func.count()).select_from(Document).where(Document.run_id == run.id)) or 0
        result.append(out.RunOut(id=run.id, workspace_id=ws.id, mode=run.mode, label=(run.config or {}).get("label"),
                                 dataset=ws.dataset, status=run.status, started_at=run.started_at,
                                 finished_at=run.finished_at, documents=docs,
                                 tiers={str(k): v for k, v in tiers.items()}))
    return result


@app.get("/api/runs/{run_id}/documents", response_model=list[out.DocumentRow], name="listRunDocuments")
def list_run_documents(run_id: int, s: Session = Depends(db)) -> list[out.DocumentRow]:
    rows = s.execute(select(Document, Invoice).outerjoin(
        Invoice, (Invoice.document_id == Document.id) & (Invoice.child_index == 0))
        .where(Document.run_id == run_id).order_by(Document.bench_doc_id, Document.id)).all()
    return [out.DocumentRow(id=d.id, bench_doc_id=d.bench_doc_id, filename=d.filename, route=d.route, state=d.state,
                            page_count=d.page_count, tier=i.conf_tier if i else None,
                            vendor=i.vendor_name if i else None, invoice_number=i.invoice_number if i else None,
                            total=i.total if i else None, currency=i.currency if i else None)
            for d, i in rows]


@app.get("/api/documents/{document_id}", response_model=out.DocumentDetail, name="getDocument")
def get_document(document_id: int, s: Session = Depends(db)) -> out.DocumentDetail:
    doc = s.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "no such document")
    pages = s.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.page_no)).all()
    invoices = s.scalars(select(Invoice).where(Invoice.document_id == doc.id).order_by(Invoice.child_index)).all()
    inv_out = []
    names = money.account_names(s)
    for inv in invoices:
        lines = s.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id).order_by(InvoiceLine.line_no)).all()
        line_no = {ln.id: ln.line_no for ln in lines}
        fvs = s.scalars(select(FieldValue).where(FieldValue.invoice_id == inv.id, FieldValue.is_current)
                        .order_by(FieldValue.id)).all()
        vendor = inv.vendor_name
        checks = s.scalars(select(CheckResult).where(CheckResult.invoice_id == inv.id).order_by(CheckResult.id)).all()
        inv_out.append(out.InvoiceOut(
            id=inv.id, child_index=inv.child_index, doc_type=inv.doc_type, state=inv.state, conf_tier=inv.conf_tier,
            vendor_name=vendor, invoice_number=inv.invoice_number, invoice_date=inv.invoice_date,
            due_date=inv.due_date, currency=inv.currency, subtotal=inv.subtotal, tax=inv.tax, total=inv.total,
            fields=[out.FieldOut(id=f.id, field=f.field, line_no=line_no.get(f.invoice_line_id), value=f.value,
                                 raw_text=f.raw_text, page_no=f.page_no, bbox=f.bbox, evidence_text=f.evidence_text,
                                 grounding=f.grounding, source=f.source, tier=f.tier, confidence=f.confidence,
                                 reasons=f.reasons) for f in fvs],
            lines=[out.LineOut(id=ln.id, line_no=ln.line_no, kind=ln.kind, description=ln.description, qty=ln.qty,
                               unit_price=ln.unit_price, amount=ln.amount) for ln in lines],
            checks=[out.CheckOut(id=c.id, code=c.code, category=c.category, outcome=c.outcome, severity=c.severity,
                                 message=c.message, evidence=c.evidence, related_invoice_id=c.related_invoice_id)
                    for c in checks],
            payments=[money.item_out(s, i) for i in s.scalars(select(PaymentItem).where(PaymentItem.invoice_id == inv.id)
                                                                .order_by(PaymentItem.id))],
            entries=[money.entry_out(s, e, names) for e in s.scalars(select(JournalEntry).where(
                JournalEntry.invoice_id == inv.id).order_by(JournalEntry.id))]))
    subjects = [("document", doc.id)] + [("invoice", i.id) for i in invoices]
    events = s.scalars(select(AuditEvent).where(AuditEvent.workspace_id == doc.workspace_id).where(
        or_(*[(AuditEvent.subject_type == t) & (AuditEvent.subject_id == i) for t, i in subjects]))
        .order_by(AuditEvent.seq)).all()
    calls = s.scalar(select(func.count()).select_from(LlmCall).where(LlmCall.document_id == doc.id)) or 0
    return out.DocumentDetail(
        id=doc.id, bench_doc_id=doc.bench_doc_id, filename=doc.filename, mime=doc.mime, route=doc.route,
        text_layer=doc.text_layer, state=doc.state, received_at=doc.received_at,
        file_url=f"/api/documents/{doc.id}/file",
        pages=[out.PageOut(id=p.id, page_no=p.page_no, width=p.width, height=p.height, unit=p.unit,
                           image_url=f"/api/pages/{p.id}/image", quality=p.quality) for p in pages],
        invoices=inv_out,
        events=[out.EventOut(seq=e.seq, occurred_at=e.occurred_at, actor=e.actor, action=e.action, data=e.data)
                for e in events],
        llm_calls=calls)


@app.get("/api/pages/{page_id}/image", name="getPageImage", response_class=FileResponse)
def get_page_image(page_id: int, s: Session = Depends(db)) -> FileResponse:
    page = s.get(Page, page_id)
    if page is None:
        raise HTTPException(404, "no such page")
    return FileResponse(_inside_storage(page.image_path), headers={"Cache-Control": "max-age=86400"})


@app.get("/api/documents/{document_id}/file", name="getDocumentFile", response_class=FileResponse)
def get_document_file(document_id: int, s: Session = Depends(db)) -> FileResponse:
    doc = s.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "no such document")
    return FileResponse(_inside_storage(doc.storage_path), media_type=doc.mime, filename=doc.filename)
