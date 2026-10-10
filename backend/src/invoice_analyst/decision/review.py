"""What a reviewer can do with an invoice that needs a person.

  correct          replace one value (typed, or clicked on the page); the checks run again on it (decision 35)
  approve          pay it in full; findings that block payment need an explicit override (decision 28)
  approve_partial  pay the part that was received, VAT in proportion; the rest waits on hold (decision 36)
  reject           don't pay; the goods it had counted as invoiced are free again for a later invoice
  hold / release   park it, and bring it back
  reprocess        have the model read it again, ignoring the cache (queued; corrections are kept)

Every action needs a note and goes into the audit trail with the reviewer as the actor.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import chain
from ..checks.engine import human_corrections, recheck
from ..checks.match import qty_text
from ..db.models import (Approval, CheckResult, Document, Extraction, FieldValue, Invoice, InvoiceLine, MatchLine,
                         ReviewDecision, ReviewTask)
from ..extraction import corrections
from ..extraction.evidence import tier_from_values
from .rules import FIELD_WORDS, RULES_VERSION, decide

IN_REVIEW = ("NEEDS_REVIEW", "ON_HOLD")
CENT = Decimal("0.01")
STATE_WORDS = {"AUTO_APPROVED": "approved automatically", "APPROVED": "approved", "APPROVED_PARTIAL": "partly approved",
               "REJECTED": "rejected", "ON_HOLD": "on hold", "NEEDS_REVIEW": "waiting for review"}


class ReviewError(Exception):
    """An action that can't be taken now, with a message for the reviewer."""


def open_task(s: Session, invoice: Invoice) -> ReviewTask | None:
    return s.scalars(select(ReviewTask).where(ReviewTask.invoice_id == invoice.id, ReviewTask.status == "open")
                     .order_by(ReviewTask.id.desc())).first()


def blocking(s: Session, invoice: Invoice) -> list[CheckResult]:
    return list(s.scalars(select(CheckResult).where(CheckResult.invoice_id == invoice.id,
                                                    CheckResult.severity == "block",
                                                    CheckResult.outcome.in_(("fail", "warn")))))


def _note(note: str | None) -> str:
    note = (note or "").strip()
    if not note:
        raise ReviewError("Add a note saying why: it goes into the audit trail.")
    return note


def _task(s: Session, invoice: Invoice, states: tuple[str, ...] = IN_REVIEW) -> ReviewTask:
    task = open_task(s, invoice)
    if invoice.state not in states or task is None:
        raise ReviewError(f"This invoice is {STATE_WORDS.get(invoice.state, invoice.state.lower())}: "
                          "there is nothing to decide on it now.")
    return task


def _overrides(s: Session, invoice: Invoice, override: bool) -> list[dict]:
    blocks = blocking(s, invoice)
    if blocks and not override:
        raise ReviewError("Payment is blocked by: " + "; ".join(b.message for b in blocks)
                          + " Tick 'override' and say in the note why it should be paid anyway.")
    return [{"kind": "override", "code": b.code, "text": b.message} for b in blocks]


def _close(s: Session, invoice: Invoice, task: ReviewTask, action: str, note: str, state: str,
           data: dict[str, Any] | None = None) -> ReviewDecision:
    """Record the decision, resolve the task, move the invoice, and write it to the audit trail."""
    data = data or {}
    decision = ReviewDecision(task_id=task.id, action=action, note=note,
                              corrections={"fields": human_corrections(s, invoice.id), **data})
    s.add(decision)
    task.status, task.resolved_at = "resolved", dt.datetime.now(dt.timezone.utc)
    old, invoice.state = invoice.state, state
    invoice.review_labels = []
    invoice.version += 1
    s.flush()
    chain.record(s, invoice.workspace_id, "user", "review.decided", "invoice", invoice.id,
                 {"action": action, "note": note, "from": old, "to": state, "task_id": task.id, **data})
    return decision


def _approval(s: Session, invoice: Invoice, amount: Decimal, note: str, extra: list[dict]) -> Approval:
    if not invoice.currency:
        raise ReviewError("Correct the currency first: an approval needs one.")
    row = Approval(invoice_id=invoice.id, kind="manual", amount=amount, currency=invoice.currency,
                   reasons=[{"kind": "note", "text": note}, *extra], rules_ver=RULES_VERSION)
    s.add(row)
    return row


def approved_so_far(s: Session, invoice: Invoice) -> Decimal:
    return Decimal(s.scalar(select(func.coalesce(func.sum(Approval.amount), 0))
                            .where(Approval.invoice_id == invoice.id)) or 0)


# ---------------------------------------------------------------- corrections


def correct(s: Session, invoice: Invoice, field_name: str, text: str | None, *, page_no: int | None = None,
            bbox: list[float] | None = None) -> FieldValue:
    """Save the reviewer's value for one field, supersede the old reading, and check the invoice again."""
    _task(s, invoice)
    ext = s.get(Extraction, invoice.extraction_id)
    order = ((ext.normalized or {}).get("invoices") or [{}])[invoice.child_index].get("date_order", "dmy")
    try:
        value = corrections.normalize(field_name, text, order)
    except corrections.CorrectionError as exc:
        raise ReviewError(str(exc)) from None
    current = s.scalars(select(FieldValue).where(FieldValue.invoice_id == invoice.id, FieldValue.field == field_name,
                                                 FieldValue.invoice_line_id.is_(None), FieldValue.is_current)).all()
    if any(fv.value == value for fv in current):
        raise ReviewError("That is already the value: nothing to change.")
    clicked = bool(bbox and page_no)
    row = FieldValue(invoice_id=invoice.id, field=field_name, raw_text=(text or "").strip() or None, value=value,
                     page_no=page_no if clicked else None, bbox=bbox if clicked else None,
                     evidence_text=(text or "").strip() if clicked else None,
                     grounding="exact" if clicked else "none", source="human", confidence=1.0, tier="high",
                     reasons=[{"code": "human", "effect": "+",
                               "text": "Picked on the page by the reviewer" if clicked else "Entered by the reviewer"}])
    s.add(row)
    s.flush()
    for fv in current:
        fv.is_current, fv.superseded_by = False, row.id
    chain.record(s, invoice.workspace_id, "user", "field.corrected", "invoice", invoice.id,
                 {"field": field_name, "from": current[0].value if current else None, "to": value,
                  "field_value_id": row.id, "picked_on_page": clicked})
    refresh(s, invoice, f"{FIELD_WORDS.get(field_name, field_name)} corrected")
    return row


def refresh(s: Session, invoice: Invoice, why: str) -> None:
    """Run the checks again and update the review task's labels; the decision stays with the reviewer."""
    recheck(s, invoice, s.get(Document, invoice.document_id), why)
    rows = s.scalars(select(FieldValue).where(FieldValue.invoice_id == invoice.id)).all()
    invoice.conf_tier = tier_from_values(list(rows)) or invoice.conf_tier
    decide(s, invoice, allow_auto=False)
    invoice.version += 1


# ---------------------------------------------------------------- decisions


def approve(s: Session, invoice: Invoice, note: str, *, override: bool = False) -> Approval:
    note = _note(note)
    if invoice.state == "APPROVED_PARTIAL":
        return _approve_rest(s, invoice, note)
    task = _task(s, invoice)
    if invoice.total is None:
        raise ReviewError("Correct the total first: there is no amount to approve.")
    overridden = _overrides(s, invoice, override)
    approval = _approval(s, invoice, invoice.total, note, overridden)
    _close(s, invoice, task, "approve", note, "APPROVED",
           {"amount": str(invoice.total), "overrode": [o["code"] for o in overridden]})
    return approval


@dataclass
class Split:
    """What approve-partial would pay: the received share of the item lines, plus the same share of the tax."""
    currency: str
    total: Decimal
    approved_net: Decimal
    approved_tax: Decimal
    approved: Decimal
    held: Decimal
    lines: list[dict] = field(default_factory=list)

    def as_json(self) -> dict:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.__dict__.items()}


def partial_split(s: Session, invoice: Invoice) -> Split | None:
    """None when nothing on the invoice is waiting for delivery (then approve-partial doesn't apply)."""
    if invoice.total is None or not invoice.currency:
        return None
    matches = {m.invoice_line_id: m for m in s.scalars(select(MatchLine).where(MatchLine.invoice_id == invoice.id))}
    if not any(m.qty_invoiced is not None and m.qty_approved is not None and m.qty_approved < m.qty_invoiced
               for m in matches.values()):
        return None
    ext = s.get(Extraction, invoice.extraction_id)
    raw = (ext.raw or {}).get("invoices", [{}])[invoice.child_index]
    resolved = ((ext.normalized or {}).get("invoices") or [{}])[invoice.child_index]
    base = covered = Decimal(0)
    lines = []
    for ln in s.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id, InvoiceLine.kind == "item")
                        .order_by(InvoiceLine.line_no)):
        amount = ln.amount if ln.amount is not None else (
            ln.qty * ln.unit_price if ln.qty is not None and ln.unit_price is not None else None)
        if amount is None:
            continue
        m = matches.get(ln.id)
        if m is None or m.outcome == "unmatched" or m.po_line_id is None:
            part, why = Decimal(0), "not on the PO"
        elif m.qty_invoiced is None:
            part, why = amount, "service (amount checked against the PO)"
        elif m.qty_invoiced:
            part = (amount * (m.qty_approved or 0) / m.qty_invoiced).quantize(CENT)
            why = f"{qty_text(m.qty_approved or Decimal(0))} of {qty_text(m.qty_invoiced)} received"
        else:
            part, why = Decimal(0), "no quantity"
        base += amount
        covered += part
        lines.append({"line_no": ln.line_no, "description": ln.description, "amount": str(amount),
                      "approved": str(part), "why": why})
    if not base:
        return None
    share = covered / base
    tax = invoice.tax or Decimal(0)
    approved_tax = (tax * share).quantize(CENT)
    dual = resolved.get("line_currency") not in (None, invoice.currency)
    if dual:  # lines in one currency, payable in another: pay the same share of the amount due
        approved = (invoice.total * share).quantize(CENT)
    elif raw.get("prices_include_tax"):
        approved = covered.quantize(CENT)
    else:
        approved = (covered + approved_tax).quantize(CENT)
    approved_net = approved - approved_tax
    return Split(invoice.currency, invoice.total, approved_net, approved_tax, approved,
                 (invoice.total - approved).quantize(CENT), lines)


def approve_partial(s: Session, invoice: Invoice, note: str, *, override: bool = False) -> Approval:
    note = _note(note)
    task = _task(s, invoice)
    split = partial_split(s, invoice)
    if split is None:
        raise ReviewError("Nothing on this invoice is waiting for delivery: approve it, or reject it.")
    overridden = _overrides(s, invoice, override)
    text = (f"Received part approved: {split.approved:,.2f} of {split.total:,.2f} {split.currency} "
            f"(tax {split.approved_tax:,.2f} in proportion); {split.held:,.2f} on hold.")
    approval = _approval(s, invoice, split.approved, note, [*overridden, {"kind": "partial", "text": text,
                                                                         "split": split.as_json()}])
    _close(s, invoice, task, "approve_partial", note, "APPROVED_PARTIAL",
           {"amount": str(split.approved), "held": str(split.held), "overrode": [o["code"] for o in overridden]})
    rest = ReviewTask(invoice_id=invoice.id, labels=["rest on hold"], status="open", reasons=[
        {"kind": "review", "code": "partial.rest",
         "text": f"{split.held:,.2f} {split.currency} is on hold until the rest is delivered. Approve it when it "
                 "arrives, or reject the rest."}])
    s.add(rest)
    invoice.review_labels = ["rest on hold"]
    return approval


def _approve_rest(s: Session, invoice: Invoice, note: str) -> Approval:
    task = _task(s, invoice, ("APPROVED_PARTIAL",))
    rest = (invoice.total or Decimal(0)) - approved_so_far(s, invoice)
    if rest <= 0:
        raise ReviewError("Everything on this invoice is already approved.")
    for m in s.scalars(select(MatchLine).where(MatchLine.invoice_id == invoice.id, MatchLine.qty_invoiced.is_not(None))):
        m.qty_approved = m.qty_invoiced  # the rest of the goods now count as invoiced
    approval = _approval(s, invoice, rest, note, [{"kind": "partial", "text": f"Rest approved: {rest:,.2f}."}])
    _close(s, invoice, task, "approve", note, "APPROVED", {"amount": str(rest), "rest_of_partial": True})
    return approval


def reject(s: Session, invoice: Invoice, note: str) -> ReviewDecision:
    note = _note(note)
    if invoice.state == "APPROVED_PARTIAL":
        task = _task(s, invoice, ("APPROVED_PARTIAL",))
        rest = (invoice.total or Decimal(0)) - approved_so_far(s, invoice)
        return _close(s, invoice, task, "reject", note, "APPROVED_PARTIAL", {"rest_cancelled": str(rest)})
    task = _task(s, invoice)
    return _close(s, invoice, task, "reject", note, "REJECTED")


def hold(s: Session, invoice: Invoice, note: str) -> ReviewDecision:
    note = _note(note)
    task = _task(s, invoice, ("NEEDS_REVIEW",))
    decision = ReviewDecision(task_id=task.id, action="hold", note=note,
                              corrections={"fields": human_corrections(s, invoice.id)})
    s.add(decision)
    invoice.state = "ON_HOLD"
    invoice.version += 1
    chain.record(s, invoice.workspace_id, "user", "review.decided", "invoice", invoice.id,
                 {"action": "hold", "note": note, "from": "NEEDS_REVIEW", "to": "ON_HOLD", "task_id": task.id})
    return decision


def release(s: Session, invoice: Invoice, note: str) -> None:
    note = _note(note)
    task = _task(s, invoice, ("ON_HOLD",))
    invoice.state = "NEEDS_REVIEW"
    invoice.version += 1
    chain.record(s, invoice.workspace_id, "user", "review.released", "invoice", invoice.id,
                 {"note": note, "from": "ON_HOLD", "to": "NEEDS_REVIEW", "task_id": task.id})


def request_reprocess(s: Session, invoice: Invoice, note: str) -> ReviewDecision:
    """Record the request; the caller queues the re-read (it uses the model, so it runs in the worker)."""
    note = _note(note)
    task = _task(s, invoice)
    decision = ReviewDecision(task_id=task.id, action="reprocess", note=note,
                              corrections={"fields": human_corrections(s, invoice.id)})
    s.add(decision)
    chain.record(s, invoice.workspace_id, "user", "review.reprocess_requested", "invoice", invoice.id,
                 {"note": note, "task_id": task.id})
    return decision


ACTIONS = {"approve": approve, "approve_partial": approve_partial, "reject": reject, "hold": hold, "release": release}


def allowed_actions(s: Session, invoice: Invoice) -> list[str]:
    if open_task(s, invoice) is None:
        return []
    if invoice.state == "APPROVED_PARTIAL":
        return ["approve", "reject"]
    if invoice.state == "ON_HOLD":
        return ["release", "approve", "reject", "correct", "reprocess"] + (
            ["approve_partial"] if partial_split(s, invoice) else [])
    if invoice.state == "NEEDS_REVIEW":
        return ["approve", *(["approve_partial"] if partial_split(s, invoice) else []), "reject", "hold", "correct",
                "reprocess"]
    return []
