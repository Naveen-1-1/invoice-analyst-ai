"""From approval to payment: book it, plan it into a weekly run, and execute runs.

  approved(s, approval, today)  book the approval and plan its payment (both once)
  execute(s, run, today)        pay a run: one transfer per vendor and currency (credit notes subtracted)
  run_due(s, ws, today)         execute every planned run dated today or earlier, oldest first
  catch_up(s, ws)               book and plan approvals that have neither yet (e.g. made before payments existed)

A credit note waits until a planned run holds enough of the same vendor's invoices in its currency, then is
subtracted there. A vendor without an open bank account on file can't be paid: the item waits, saying why.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import chain
from ..checks.normalize import last4
from ..db.models import (Approval, Document, Extraction, Invoice, JournalEntry, PaymentItem, PaymentRun, Vendor,
                         VendorBankAccount, Workspace)
from ..ledger import posting
from . import calendar, clock

CENT = Decimal("0.01")


def money(x: Decimal) -> str:
    return str(Decimal(x).quantize(CENT))


class PaymentError(Exception):
    """A payment action that can't be taken now, with a message for the user."""


def open_account(s: Session, vendor_id: int | None) -> VendorBankAccount | None:
    if vendor_id is None:
        return None
    return s.scalars(select(VendorBankAccount).where(VendorBankAccount.vendor_id == vendor_id,
                                                     VendorBankAccount.valid_to.is_(None))
                     .order_by(VendorBankAccount.id.desc())).first()


def first_open_run(s: Session, workspace_id: int, today: dt.date) -> dt.date:
    """The first Thursday on or after today whose run hasn't been executed yet."""
    d = calendar.next_run(today)
    done = set(s.scalars(select(PaymentRun.run_date).where(PaymentRun.workspace_id == workspace_id,
                                                           PaymentRun.status == "executed",
                                                           PaymentRun.run_date >= d)))
    while d in done:
        d += dt.timedelta(days=7)
    return d


def _run(s: Session, workspace_id: int, d: dt.date) -> PaymentRun:
    run = s.scalars(select(PaymentRun).where(PaymentRun.workspace_id == workspace_id, PaymentRun.run_date == d)).first()
    if run is None:
        run = PaymentRun(workspace_id=workspace_id, run_date=d, status="planned")
        s.add(run)
        s.flush()
    return run


def _terms_text(s: Session, inv: Invoice) -> str | None:
    ext = s.get(Extraction, inv.extraction_id) if inv.extraction_id else None
    raw = ((ext.raw or {}).get("invoices") or [{}])[inv.child_index] if ext else {}
    return (raw.get("payment_terms") or {}).get("text")


def approved(s: Session, approval: Approval, today: dt.date) -> PaymentItem | None:
    """Book the approval on `today` (decision 42) and plan its payment."""
    booking = posting.book_approval(s, approval, today)
    if booking is None:
        return None
    item = s.scalars(select(PaymentItem).where(PaymentItem.approval_id == approval.id)).first()
    if item is not None:
        return item
    inv = s.get(Invoice, approval.invoice_id)
    vendor = s.get(Vendor, inv.vendor_id) if inv.vendor_id else None
    due = calendar.due_date(inv.due_date, inv.invoice_date, _terms_text(s, inv), vendor.terms_days if vendor else None,
                            today)
    item = PaymentItem(invoice_id=inv.id, approval_id=approval.id, vendor_id=inv.vendor_id, amount=booking.payable,
                       currency=approval.currency, due_date=due.date, due_source=due.source, status="waiting",
                       reason="")
    s.add(item)
    if booking.payable < 0:
        item.reason = (f"Credit note: subtracted from the next payment to {inv.vendor_name or 'this vendor'} in "
                       f"{approval.currency} that covers it.")
    elif open_account(s, inv.vendor_id) is None:
        item.reason = "No bank account on file for this vendor, so it can't be paid until one is added."
    else:
        discount_by = None
        if vendor and vendor.discount_pct and vendor.discount_days and inv.invoice_date:
            discount_by = inv.invoice_date + dt.timedelta(days=vendor.discount_days)
        p = calendar.plan(first_open_run(s, inv.workspace_id, today), due.date, discount_by)
        item.run_id, item.status = _run(s, inv.workspace_id, p.run_date).id, "planned"
        if p.take_discount:
            item.discount = (booking.payable * vendor.discount_pct / 100).quantize(CENT)  # type: ignore[union-attr]
        item.reason = calendar.explain(p, due, float(vendor.discount_pct) if p.take_discount else None, discount_by,
                                       today)
    s.flush()
    chain.record(s, inv.workspace_id, "system", "payment.planned", "invoice", inv.id,
                 {"amount": money(item.amount), "discount": money(item.discount), "currency": item.currency,
                  "status": item.status, "run_date": _run_date(s, item), "due_date": due.date.isoformat(),
                  "due_source": due.source, "reason": item.reason, "entry_id": booking.entry.id})
    _attach_credits(s, inv.workspace_id, inv.vendor_id, approval.currency)
    return item


def _run_date(s: Session, item: PaymentItem) -> str | None:
    return s.get(PaymentRun, item.run_id).run_date.isoformat() if item.run_id else None


def _attach_credits(s: Session, workspace_id: int, vendor_id: int | None, currency: str) -> None:
    """Put waiting credit notes of this vendor and currency into the first planned run whose invoices cover them."""
    if vendor_id is None:
        return
    credits = s.scalars(select(PaymentItem).where(PaymentItem.vendor_id == vendor_id, PaymentItem.currency == currency,
                                                  PaymentItem.status == "waiting", PaymentItem.amount < 0)
                        .order_by(PaymentItem.id)).all()
    if not credits:
        return
    runs = s.scalars(select(PaymentRun).where(PaymentRun.workspace_id == workspace_id, PaymentRun.status == "planned")
                     .order_by(PaymentRun.run_date)).all()
    for credit in credits:
        for run in runs:
            net = sum((i.amount - i.discount for i in s.scalars(select(PaymentItem).where(
                PaymentItem.run_id == run.id, PaymentItem.vendor_id == vendor_id, PaymentItem.currency == currency,
                PaymentItem.status == "planned"))), Decimal(0))
            if net + credit.amount >= 0 and net > 0:
                credit.run_id, credit.status = run.id, "planned"
                credit.reason = (f"Credit note: subtracted from the payment to this vendor on Thu "
                                 f"{run.run_date:%d %b %Y}.")
                s.flush()
                break


def execute(s: Session, run: PaymentRun, today: dt.date, actor: str = "system") -> dict:
    """Pay a run: book each payment, mark the items paid, and record one transfer per vendor and currency."""
    if run.status == "executed":
        raise PaymentError(f"The run of {run.run_date} has already been paid.")
    if run.run_date > today:
        raise PaymentError(f"This run is on {run.run_date}; today is {today}. Move today forward first.")
    items = s.scalars(select(PaymentItem).where(PaymentItem.run_id == run.id, PaymentItem.status == "planned")
                      .order_by(PaymentItem.id)).all()
    groups: dict[tuple[int | None, str], list[PaymentItem]] = defaultdict(list)
    for item in items:
        groups[(item.vendor_id, item.currency)].append(item)
    transfers = []
    for (vendor_id, ccy), group in groups.items():
        if sum(i.amount - i.discount for i in group) < 0:  # credits exceed invoices: they wait for a later run
            for i in group:
                if i.amount < 0:
                    i.run_id, i.status = None, "waiting"
                    i.reason = "Credit note: more than this vendor's invoices in the run; it waits for the next ones."
            group = [i for i in group if i.amount >= 0]
        account = open_account(s, vendor_id)
        if account is None:
            for i in group:
                i.run_id, i.status = None, "waiting"
                i.reason = "No bank account on file for this vendor, so it can't be paid until one is added."
            continue
        for i in group:
            posting.book_payment(s, i, run)
            i.status, i.bank_account_id = "paid", account.id
        if group:
            vendor = s.get(Vendor, vendor_id)
            transfers.append({"vendor": vendor.name if vendor else None, "currency": ccy, "to": last4(account.account_norm),
                              "amount": money(sum((i.amount - i.discount for i in group), Decimal(0))),
                              "invoices": [i.invoice_id for i in group]})
    run.status, run.executed_at = "executed", dt.datetime.now(dt.timezone.utc)
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for t in transfers:
        totals[t["currency"]] += Decimal(t["amount"])
    summary = {"run_date": run.run_date.isoformat(), "transfers": transfers,
               "totals": {k: money(v) for k, v in sorted(totals.items())}, "executed_on": today.isoformat()}
    s.flush()
    chain.record(s, run.workspace_id, actor, "payment_run.executed", "payment_run", run.id, summary)
    for t in transfers:
        for invoice_id in t["invoices"]:
            chain.record(s, run.workspace_id, actor, "invoice.paid", "invoice", invoice_id,
                         {"run_date": run.run_date.isoformat(), "to": t["to"], "currency": t["currency"]})
    return summary


def planned_runs(s: Session, workspace_id: int, until: dt.date | None = None) -> list[PaymentRun]:
    q = select(PaymentRun).where(PaymentRun.workspace_id == workspace_id, PaymentRun.status == "planned")
    if until is not None:
        q = q.where(PaymentRun.run_date <= until)
    return list(s.scalars(q.order_by(PaymentRun.run_date)))


def run_due(s: Session, ws: Workspace, today: dt.date, actor: str = "system") -> list[dict]:
    return [execute(s, run, today, actor) for run in planned_runs(s, ws.id, today)]


def set_today(s: Session, ws: Workspace, new: dt.date) -> None:
    now = clock.today(s, ws)
    if now.live:
        raise PaymentError("This inbox is replaying on its simulated clock; today moves on its own.")
    last_paid = s.scalars(select(PaymentRun.run_date).where(PaymentRun.workspace_id == ws.id,
                                                            PaymentRun.status == "executed")
                          .order_by(PaymentRun.run_date.desc())).first()
    if last_paid and new < last_paid:
        raise PaymentError(f"The run of {last_paid} has been paid; today can't go back before it.")
    ws.as_of_date = new
    chain.record(s, ws.id, "user", "workspace.today_set", "workspace", ws.id,
                 {"from": now.date.isoformat(), "to": new.isoformat()})


def next_run(s: Session, ws: Workspace) -> dict:
    """Execute the earliest planned run, moving today forward to its date first if needed."""
    runs = planned_runs(s, ws.id)
    if not runs:
        raise PaymentError("No payment run is planned.")
    now = clock.today(s, ws)
    if runs[0].run_date > now.date:
        set_today(s, ws, runs[0].run_date)
    return execute(s, runs[0], max(now.date, runs[0].run_date), actor="user")


def catch_up(s: Session, ws: Workspace) -> int:
    """Book and plan approvals that have no entry yet (automatic ones on their arrival date, others on today)."""
    booked = select(JournalEntry.approval_id).where(JournalEntry.approval_id.is_not(None))
    pending = s.scalars(select(Approval).join(Invoice, Invoice.id == Approval.invoice_id)
                        .where(Invoice.workspace_id == ws.id, Approval.id.not_in(booked)).order_by(Approval.id)).all()
    today = clock.today(s, ws).date
    for approval in pending:
        inv = s.get(Invoice, approval.invoice_id)
        on = s.get(Document, inv.document_id).received_at.date() if approval.kind == "auto" else today
        approved(s, approval, on)
    return len(pending)
