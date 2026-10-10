"""Payments, the ledger and the audit trail of a workspace: what's planned and paid, the books, and the chain check."""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit.verify import verify
from ..checks.normalize import last4
from ..db.models import (Account, AuditEvent, Entity, Invoice, JournalEntry, JournalLine, PaymentItem, PaymentRun,
                         Vendor, VendorBankAccount, Workspace)
from ..ledger import reports
from ..payments import clock, runs
from . import schemas as out
from .deps import db

router = APIRouter()


def _workspace(s: Session, workspace_id: int) -> Workspace:
    ws = s.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "no such workspace")
    return ws


def _today(s: Session, ws: Workspace) -> out.TodayOut:
    t = clock.today(s, ws)
    return out.TodayOut(date=t.date, live=t.live)


def item_out(s: Session, item: PaymentItem) -> out.PaymentItemOut:
    inv = s.get(Invoice, item.invoice_id)
    vendor = s.get(Vendor, item.vendor_id) if item.vendor_id else None
    entity = s.get(Entity, inv.entity_id) if inv.entity_id else None
    run = s.get(PaymentRun, item.run_id) if item.run_id else None
    account = s.get(VendorBankAccount, item.bank_account_id) if item.bank_account_id else (
        runs.open_account(s, item.vendor_id) if item.status != "paid" else None)
    return out.PaymentItemOut(
        id=item.id, invoice_id=inv.id, document_id=inv.document_id, vendor=vendor.name if vendor else inv.vendor_name,
        entity=entity.name if entity else None, invoice_number=inv.invoice_number, doc_type=inv.doc_type,
        amount=item.amount, discount=item.discount, paid=item.amount - item.discount, currency=item.currency,
        status=item.status, due_date=item.due_date, due_source=item.due_source, run_date=run.run_date if run else None,
        to_account=last4(account.account_norm) if account else None, reason=item.reason)


def entry_out(s: Session, entry: JournalEntry, names: dict[str, str]) -> out.JournalEntryOut:
    inv = s.get(Invoice, entry.invoice_id) if entry.invoice_id else None
    entity = s.get(Entity, entry.entity_id) if entry.entity_id else None
    lines = s.scalars(select(JournalLine).where(JournalLine.entry_id == entry.id).order_by(JournalLine.id)).all()
    vendors = {v.id: v.name for v in s.scalars(select(Vendor).where(Vendor.id.in_({ln.vendor_id for ln in lines
                                                                                    if ln.vendor_id})))}
    return out.JournalEntryOut(
        id=entry.id, entry_date=entry.entry_date, kind=entry.kind, memo=entry.memo,
        entity=entity.name if entity else None, invoice_id=entry.invoice_id,
        document_id=inv.document_id if inv else None, posted_at=entry.posted_at,
        lines=[out.JournalLineOut(account=ln.account_code, account_name=names.get(ln.account_code, ln.account_code),
                                  currency=ln.currency, debit=ln.debit, credit=ln.credit,
                                  vendor=vendors.get(ln.vendor_id)) for ln in lines])


def account_names(s: Session) -> dict[str, str]:
    return {a.code: a.name for a in s.scalars(select(Account))}


def _run_out(s: Session, run: PaymentRun) -> out.PaymentRunOut:
    items = s.scalars(select(PaymentItem).where(PaymentItem.run_id == run.id)).all()
    totals: dict[str, Decimal] = defaultdict(Decimal)
    discounts: dict[str, Decimal] = defaultdict(Decimal)
    for i in items:
        totals[i.currency] += i.amount - i.discount
        discounts[i.currency] += i.discount
    return out.PaymentRunOut(id=run.id, run_date=run.run_date, status=run.status, executed_at=run.executed_at,
                             items=len(items), totals=dict(sorted(totals.items())),
                             discounts={k: v for k, v in sorted(discounts.items()) if v})


# ---------------------------------------------------------------- payments


@router.get("/api/workspaces/{workspace_id}/payments", response_model=out.PaymentsOut, name="listPayments")
def list_payments(workspace_id: int, s: Session = Depends(db)) -> out.PaymentsOut:
    """Every payment run (planned and paid) and the items that wait outside a run, with why."""
    ws = _workspace(s, workspace_id)
    today = _today(s, ws)
    all_runs = s.scalars(select(PaymentRun).where(PaymentRun.workspace_id == ws.id).order_by(PaymentRun.run_date)).all()
    waiting = s.scalars(select(PaymentItem).join(Invoice, Invoice.id == PaymentItem.invoice_id)
                        .where(Invoice.workspace_id == ws.id, PaymentItem.status == "waiting")
                        .order_by(PaymentItem.id)).all()
    return out.PaymentsOut(workspace_id=ws.id, today=today, runs=[_run_out(s, r) for r in all_runs],
                           waiting=[item_out(s, i) for i in waiting],
                           due_now=sum(1 for r in all_runs if r.status == "planned" and r.run_date <= today.date))


@router.get("/api/payment-runs/{run_id}", response_model=out.PaymentRunDetail, name="getPaymentRun")
def get_payment_run(run_id: int, s: Session = Depends(db)) -> out.PaymentRunDetail:
    """One run: a transfer per vendor and currency, with the invoices (and credit notes) in it."""
    run = s.get(PaymentRun, run_id)
    if run is None:
        raise HTTPException(404, "no such payment run")
    groups: dict[tuple[int | None, str], list[PaymentItem]] = defaultdict(list)
    for i in s.scalars(select(PaymentItem).where(PaymentItem.run_id == run.id).order_by(PaymentItem.id)):
        groups[(i.vendor_id, i.currency)].append(i)
    transfers = []
    for (_, ccy), items in groups.items():
        rows = [item_out(s, i) for i in items]
        transfers.append(out.TransferOut(vendor=rows[0].vendor, currency=ccy, amount=sum((r.paid for r in rows),
                                                                                         Decimal(0)),
                                         to_account=rows[0].to_account, items=rows))
    transfers.sort(key=lambda t: (t.currency, -t.amount))
    return out.PaymentRunDetail(run=_run_out(s, run), today=_today(s, s.get(Workspace, run.workspace_id)),
                                transfers=transfers)


def _refuse(s: Session, exc: runs.PaymentError) -> HTTPException:
    s.rollback()
    return HTTPException(409, str(exc))


@router.post("/api/payment-runs/{run_id}/execute", response_model=out.RunResultOut, name="executePaymentRun")
def execute_payment_run(run_id: int, s: Session = Depends(db)) -> out.RunResultOut:
    run = s.get(PaymentRun, run_id)
    if run is None:
        raise HTTPException(404, "no such payment run")
    ws = s.get(Workspace, run.workspace_id)
    try:
        summary = runs.execute(s, run, clock.today(s, ws).date, actor="user")
    except runs.PaymentError as exc:
        raise _refuse(s, exc) from None
    s.commit()
    return out.RunResultOut(executed=[summary["run_date"]], today=_today(s, ws),
                            message=f"Paid the run of {summary['run_date']}: {len(summary['transfers'])} transfer(s).")


@router.post("/api/workspaces/{workspace_id}/payments/next", response_model=out.RunResultOut, name="runNextPayment")
def run_next_payment(workspace_id: int, s: Session = Depends(db)) -> out.RunResultOut:
    """Move today to the next planned run (if it's later) and pay it."""
    ws = _workspace(s, workspace_id)
    try:
        summary = runs.next_run(s, ws)
    except runs.PaymentError as exc:
        raise _refuse(s, exc) from None
    s.commit()
    return out.RunResultOut(executed=[summary["run_date"]], today=_today(s, ws),
                            message=f"Paid the run of {summary['run_date']}: {len(summary['transfers'])} transfer(s).")


@router.post("/api/workspaces/{workspace_id}/payments/run-due", response_model=out.RunResultOut, name="runDuePayments")
def run_due_payments(workspace_id: int, s: Session = Depends(db)) -> out.RunResultOut:
    """Pay every planned run dated today or earlier, oldest first."""
    ws = _workspace(s, workspace_id)
    try:
        done = runs.run_due(s, ws, clock.today(s, ws).date, actor="user")
    except runs.PaymentError as exc:
        raise _refuse(s, exc) from None
    s.commit()
    return out.RunResultOut(executed=[d["run_date"] for d in done], today=_today(s, ws),
                            message=f"Paid {len(done)} run(s)." if done else "No run is due yet.")


@router.put("/api/workspaces/{workspace_id}/today", response_model=out.TodayOut, name="setToday")
def set_today(workspace_id: int, body: out.TodayIn, s: Session = Depends(db)) -> out.TodayOut:
    """Set the workspace's current date (not while a live inbox is replaying); runs aren't paid until you say so."""
    ws = _workspace(s, workspace_id)
    try:
        runs.set_today(s, ws, body.date)
    except runs.PaymentError as exc:
        raise _refuse(s, exc) from None
    s.commit()
    return _today(s, ws)


# ---------------------------------------------------------------- the books


@router.get("/api/workspaces/{workspace_id}/entities", response_model=list[out.EntityOut], name="listEntities")
def list_entities(workspace_id: int, s: Session = Depends(db)) -> list[out.EntityOut]:
    """Our companies that have entries in the books (for filtering)."""
    used = select(JournalEntry.entity_id).where(JournalEntry.workspace_id == workspace_id).distinct()
    return [out.EntityOut(id=e.id, name=e.name)
            for e in s.scalars(select(Entity).where(Entity.id.in_(used)).order_by(Entity.name))]


@router.get("/api/workspaces/{workspace_id}/journal", response_model=out.JournalOut, name="listJournal")
def list_journal(workspace_id: int, entity_id: int | None = None, limit: int = 100, offset: int = 0,
                 s: Session = Depends(db)) -> out.JournalOut:
    q = select(JournalEntry).where(JournalEntry.workspace_id == workspace_id)
    if entity_id is not None:
        q = q.where(JournalEntry.entity_id == entity_id)
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    names = account_names(s)
    entries = s.scalars(q.order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
                        .limit(min(limit, 500)).offset(offset)).all()
    return out.JournalOut(total=total, entries=[entry_out(s, e, names) for e in entries])


@router.get("/api/workspaces/{workspace_id}/trial-balance", response_model=out.TrialBalanceOut, name="getTrialBalance")
def get_trial_balance(workspace_id: int, entity_id: int | None = None, s: Session = Depends(db)) -> out.TrialBalanceOut:
    rows = reports.trial_balance(s, workspace_id, entity_id)
    return out.TrialBalanceOut(
        rows=[out.TrialRowOut(currency=r.currency, account=r.account, name=r.name, type=r.type, debit=r.debit,
                              credit=r.credit, balance=r.balance) for r in rows],
        balanced=reports.balanced(rows),
        reconciled=[out.ReconciliationOut(currency=r.currency, ledger=r.ledger, open_items=r.open_items, ok=r.ok)
                    for r in reports.reconcile(s, workspace_id)] if entity_id is None else [])


@router.get("/api/workspaces/{workspace_id}/payables", response_model=out.PayablesOut, name="getPayables")
def get_payables(workspace_id: int, entity_id: int | None = None, s: Session = Depends(db)) -> out.PayablesOut:
    """What we owe and haven't paid, aged by due date as of today."""
    ws = _workspace(s, workspace_id)
    today = _today(s, ws)
    items = reports.payables(s, ws.id, today.date, entity_id)
    totals: dict[str, dict[str, Decimal]] = defaultdict(lambda: {b: Decimal(0) for b in reports.BUCKETS})
    for i in items:
        totals[i.currency][i.bucket] += i.amount
    return out.PayablesOut(today=today, buckets=list(reports.BUCKETS), totals=dict(sorted(totals.items())),
                           items=[out.OpenItemOut(**i.__dict__) for i in items])


def _csv(name: str, header: list[str], rows: list[list]) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@router.get("/api/workspaces/{workspace_id}/export/{name}.csv", include_in_schema=False)
def export_csv(workspace_id: int, name: str, entity_id: int | None = None, s: Session = Depends(db)):
    """CSV downloads: journal (one row per line), trial-balance, payables."""
    ws = _workspace(s, workspace_id)
    stem = f"{ws.name}-{name}"
    if name == "journal":
        names = account_names(s)
        q = select(JournalEntry, JournalLine, Entity.name).join(JournalLine, JournalLine.entry_id == JournalEntry.id) \
            .outerjoin(Entity, Entity.id == JournalEntry.entity_id).where(JournalEntry.workspace_id == ws.id)
        if entity_id is not None:
            q = q.where(JournalEntry.entity_id == entity_id)
        rows = [[e.id, e.entry_date, e.kind, entity, e.memo, ln.account_code, names.get(ln.account_code), ln.currency,
                 ln.debit, ln.credit, e.invoice_id] for e, ln, entity in s.execute(q.order_by(JournalEntry.id,
                                                                                               JournalLine.id))]
        return _csv(stem, ["entry", "date", "kind", "company", "memo", "account", "account_name", "currency", "debit",
                           "credit", "invoice_id"], rows)
    if name == "trial-balance":
        rows = [[r.currency, r.account, r.name, r.type, r.debit, r.credit, r.balance]
                for r in reports.trial_balance(s, ws.id, entity_id)]
        return _csv(stem, ["currency", "account", "name", "type", "debit", "credit", "balance"], rows)
    if name == "payables":
        today = clock.today(s, ws).date
        rows = [[i.vendor, i.entity, i.invoice_number, i.currency, i.amount, i.due_date, i.days_overdue, i.bucket,
                 i.status, i.run_date] for i in reports.payables(s, ws.id, today, entity_id)]
        return _csv(f"{stem}-{today}", ["vendor", "company", "invoice", "currency", "amount", "due_date",
                                        "days_overdue", "age", "status", "run_date"], rows)
    raise HTTPException(404, "exports: journal, trial-balance, payables")


# ---------------------------------------------------------------- the audit trail


@router.get("/api/workspaces/{workspace_id}/audit", response_model=out.AuditOut, name="listAudit")
def list_audit(workspace_id: int, actor: str | None = None, action: str | None = None,
               subject_type: str | None = None, subject_id: int | None = None, limit: int = 200, offset: int = 0,
               s: Session = Depends(db)) -> out.AuditOut:
    q = select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
    for column, value in ((AuditEvent.actor, actor), (AuditEvent.action, action),
                          (AuditEvent.subject_type, subject_type), (AuditEvent.subject_id, subject_id)):
        if value is not None:
            q = q.where(column == value)
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    actions = sorted(s.scalars(select(AuditEvent.action).where(AuditEvent.workspace_id == workspace_id).distinct()))
    events = s.scalars(q.order_by(AuditEvent.seq.desc()).limit(min(limit, 1000)).offset(offset)).all()
    return out.AuditOut(total=total, actions=actions, events=[
        out.AuditEventOut(seq=e.seq, occurred_at=e.occurred_at, actor=e.actor, action=e.action,
                          subject_type=e.subject_type, subject_id=e.subject_id, data=e.data, hash=e.hash)
        for e in events])


@router.get("/api/workspaces/{workspace_id}/audit/verify", response_model=out.VerdictOut, name="verifyAudit")
def verify_audit(workspace_id: int, s: Session = Depends(db)) -> out.VerdictOut:
    """Recompute the workspace's hash chain: any changed, missing or reordered event is found."""
    v = verify(s, workspace_id)
    return out.VerdictOut(ok=v.ok, events=v.events, broken_at=v.broken_at, problem=v.problem, words=v.words())
