"""Review: the queue of invoices waiting for a person, one invoice's review state, and the reviewer's actions."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (Approval, CheckResult, Document, Invoice, InvoiceLine, MatchLine, Page, PoLine,
                         PurchaseOrder, ReviewDecision, ReviewTask, StepRun, Workspace)
from ..decision import review, rules
from ..extraction.corrections import CORRECTABLE
from ..worker.tasks import reread_invoice
from . import schemas as out
from .deps import db

router = APIRouter()


def _invoice(s: Session, invoice_id: int) -> Invoice:
    inv = s.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404, "no such invoice")
    return inv


@router.get("/api/review", response_model=list[out.ReviewItemOut], name="listReviewQueue")
def list_review_queue(workspace_id: int | None = None, s: Session = Depends(db)) -> list[out.ReviewItemOut]:
    """Open review tasks: payment-blocking ones first, then by due date (soonest first), then arrival order."""
    blocks = (select(CheckResult.invoice_id, func.count().label("n")).where(
        CheckResult.severity == "block", CheckResult.outcome.in_(("fail", "warn"))).group_by(CheckResult.invoice_id)
        .subquery())
    q = (select(ReviewTask, Invoice, Document, Workspace, func.coalesce(blocks.c.n, 0))
         .join(Invoice, Invoice.id == ReviewTask.invoice_id).join(Document, Document.id == Invoice.document_id)
         .join(Workspace, Workspace.id == Invoice.workspace_id).outerjoin(blocks, blocks.c.invoice_id == Invoice.id)
         .where(ReviewTask.status == "open"))
    if workspace_id is not None:
        q = q.where(Invoice.workspace_id == workspace_id)
    rows = s.execute(q).all()
    rows.sort(key=lambda r: (r[4] == 0, r[1].due_date is None, r[1].due_date or 0, r[2].feed_seq or 0, r[1].child_index))
    return [out.ReviewItemOut(task_id=t.id, invoice_id=i.id, document_id=d.id, workspace_id=w.id, workspace=w.name,
                              seq=d.feed_seq, filename=d.filename, doc_type=i.doc_type, state=i.state,
                              vendor_name=i.vendor_name, invoice_number=i.invoice_number, due_date=i.due_date,
                              total=i.total, currency=i.currency, conf_tier=i.conf_tier, labels=t.labels or [],
                              blocks=n, created_at=t.created_at)
            for t, i, d, w, n in rows]


def _detail(s: Session, inv: Invoice) -> out.ReviewDetail:
    task = review.open_task(s, inv)
    tasks = s.scalars(select(ReviewTask.id).where(ReviewTask.invoice_id == inv.id)).all()
    decisions = s.scalars(select(ReviewDecision).where(ReviewDecision.task_id.in_(tasks))
                          .order_by(ReviewDecision.id)).all() if tasks else []
    approvals = s.scalars(select(Approval).where(Approval.invoice_id == inv.id).order_by(Approval.id)).all()
    po = s.get(PurchaseOrder, inv.po_id) if inv.po_id else None
    lines = {ln.id: ln for ln in s.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id))}
    po_lines = {pl.id: pl.line_no for pl in s.scalars(select(PoLine).where(PoLine.po_id == inv.po_id))} if po else {}
    matches = sorted(s.scalars(select(MatchLine).where(MatchLine.invoice_id == inv.id)),
                     key=lambda m: lines[m.invoice_line_id].line_no if m.invoice_line_id in lines else 0)
    split = review.partial_split(s, inv)
    step = s.scalars(select(StepRun).where(StepRun.invoice_id == inv.id, StepRun.step == "reread")
                     .order_by(StepRun.id.desc())).first()
    return out.ReviewDetail(
        invoice_id=inv.id, document_id=inv.document_id, workspace_id=inv.workspace_id, state=inv.state,
        doc_type=inv.doc_type, labels=inv.review_labels or [],
        task=out.TaskOut(id=task.id, status=task.status, labels=task.labels or [], reasons=task.reasons or [],
                         created_at=task.created_at, resolved_at=task.resolved_at) if task else None,
        decisions=[out.DecisionOut(id=d.id, action=d.action, note=d.note, decided_at=d.decided_at) for d in decisions],
        approvals=[out.ApprovalOut(id=a.id, kind=a.kind, amount=a.amount, currency=a.currency, reasons=a.reasons or [],
                                   rules_ver=a.rules_ver, created_at=a.created_at) for a in approvals],
        approved_so_far=sum((a.amount for a in approvals), Decimal(0)), po_id=po.id if po else None,
        po_number=po.po_number if po else None,
        matches=[out.MatchOut(line_no=lines[m.invoice_line_id].line_no, description=lines[m.invoice_line_id].description,
                              po_line_no=po_lines.get(m.po_line_id), qty_invoiced=m.qty_invoiced,
                              qty_received_avail=m.qty_received_avail, qty_approved=m.qty_approved,
                              price_inv=m.price_inv, price_po=m.price_po, variance_pct=m.variance_pct,
                              outcome=m.outcome) for m in matches if m.invoice_line_id in lines],
        partial=out.SplitOut(**split.as_json()) if split else None,
        blocks=[b.message for b in review.blocking(s, inv)], actions=review.allowed_actions(s, inv),
        correctable=list(CORRECTABLE),
        reread=out.StepOut(status=step.status, explanation=step.explanation, started_at=step.started_at,
                           finished_at=step.finished_at) if step else None,
        cap=out.CapOut(usd_estimate=rules.usd(inv.total, inv.currency), cap_usd=rules.AUTO_APPROVE_CAP_USD,
                       rates_as_of=rules.FX_AS_OF))


@router.get("/api/invoices/{invoice_id}/review", response_model=out.ReviewDetail, name="getInvoiceReview")
def get_invoice_review(invoice_id: int, s: Session = Depends(db)) -> out.ReviewDetail:
    return _detail(s, _invoice(s, invoice_id))


def _act(s: Session, invoice_id: int, act) -> out.ReviewDetail:
    """Run one reviewer action in its own transaction: a refusal (409, with the reason) changes nothing."""
    inv = _invoice(s, invoice_id)
    try:
        act(inv)
    except review.ReviewError as exc:
        s.rollback()
        raise HTTPException(409, str(exc)) from None
    s.commit()
    return _detail(s, inv)


@router.post("/api/invoices/{invoice_id}/corrections", response_model=out.ReviewDetail, name="correctField")
def correct_field(invoice_id: int, body: out.CorrectionIn, s: Session = Depends(db)) -> out.ReviewDetail:
    """Replace one value (typed, or picked on the page); the checks run again on the corrected invoice."""
    return _act(s, invoice_id, lambda inv: review.correct(s, inv, body.field, body.text, page_no=body.page_no,
                                                          bbox=body.bbox))


@router.post("/api/invoices/{invoice_id}/decision", response_model=out.ReviewDetail, name="decideInvoice")
def decide_invoice(invoice_id: int, body: out.DecisionIn, s: Session = Depends(db)) -> out.ReviewDetail:
    """Approve, approve the received part, reject, hold or release; every action needs a note."""
    action = review.ACTIONS[body.action]
    if body.action in ("approve", "approve_partial"):
        return _act(s, invoice_id, lambda inv: action(s, inv, body.note, override=body.override))
    return _act(s, invoice_id, lambda inv: action(s, inv, body.note))


@router.post("/api/invoices/{invoice_id}/reprocess", response_model=out.ReviewDetail, name="reprocessInvoice")
async def reprocess_invoice(invoice_id: int, body: out.ReprocessIn, s: Session = Depends(db)) -> out.ReviewDetail:
    """Queue a fresh read by the model (ignoring the cache); the checks run again when it is done."""
    inv = _invoice(s, invoice_id)
    busy = s.scalars(select(StepRun).where(StepRun.document_id == inv.document_id, StepRun.step == "reread",
                                           StepRun.status.in_(("queued", "running")))).first()
    if busy:
        raise HTTPException(409, "It is already being read again.")
    step = StepRun(document_id=inv.document_id, invoice_id=inv.id, step="reread", status="queued")

    def act(inv: Invoice) -> None:
        review.request_reprocess(s, inv, body.note)
        s.add(step)

    detail = _act(s, invoice_id, act)
    await reread_invoice.defer_async(step_run_id=step.id)
    return detail


@router.get("/api/pages/{page_id}/words", response_model=list[out.WordOut], name="getPageWords")
def get_page_words(page_id: int, s: Session = Depends(db)) -> list[out.WordOut]:
    """The words found on a page (text layer or OCR), with boxes, so the reviewer can click one to fill a field."""
    page = s.get(Page, page_id)
    if page is None:
        raise HTTPException(404, "no such page")
    return [out.WordOut(t=w["t"], bbox=w["bbox"]) for w in page.words or [] if w.get("t") and w.get("bbox")]
