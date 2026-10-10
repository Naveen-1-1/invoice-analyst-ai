"""Run every check on one invoice, store the findings, and move it to CHECKED then MATCHED (each step audited).

Order matters: the vendor is identified first (bank, duplicate, match and history checks use it), and duplicate and
three-way-match checks look only at invoices that arrived earlier, so the inbox must be checked in arrival order.
A reviewer's corrections (field values with source 'human') are laid over the model's reading first (decision 35).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..audit import chain
from ..db.models import CheckResult, Document, Extraction, FieldValue, Invoice, MatchLine
from ..extraction import corrections
from ..extraction.resolve import Resolved, resolve
from ..extraction.schemas import FileExtraction
from . import basic, duplicates, match, parties, patterns
from .base import CHECK_VERSION, Context, Finding


def _transition(s: Session, inv: Invoice, state: str, data: dict) -> None:
    old, inv.state = inv.state, state
    chain.record(s, inv.workspace_id, "system", "invoice.state_changed", "invoice", inv.id,
                 {"from": old, "to": state, **data})


def check_invoice(s: Session, invoice: Invoice, document: Document, received: dt.date) -> list[Finding]:
    ext = s.get(Extraction, invoice.extraction_id)
    extraction = FileExtraction.model_validate(ext.raw)
    inv = extraction.invoices[invoice.child_index]
    fixes = human_corrections(s, invoice.id)
    if fixes:
        inv = corrections.apply(inv, fixes)
    r = corrections.pin(resolve(inv), fixes) if fixes else resolve(inv)
    if fixes:
        _refresh_columns(invoice, r)
    ctx = Context(s=s, workspace_id=invoice.workspace_id, invoice=invoice, document=document, inv=inv, r=r,
                  received=received, invoices_in_file=len(extraction.invoices))

    findings = basic.doctype(ctx)
    if inv.document_type in ("statement", "other"):
        checks = findings  # nothing to pay: no further checks
    else:
        checks = findings + basic.arithmetic(ctx) + parties.vendor(ctx) + parties.bank(ctx) + parties.addressee(ctx)
        checks += duplicates.duplicates(ctx) + patterns.patterns(ctx)
    invoice.vendor_id = ctx.vendor.id if ctx.vendor else None
    invoice.entity_id = ctx.entity.id if ctx.entity else None
    s.add_all(CheckResult(invoice_id=invoice.id, code=f.code, category=f.category, outcome=f.outcome,
                          severity=f.severity, message=f.message, evidence=f.evidence,
                          related_invoice_id=f.related_invoice_id, check_ver=CHECK_VERSION) for f in checks)
    _transition(s, invoice, "CHECKED", _summary(checks))
    if inv.document_type in ("statement", "other"):
        return checks
    matched = match.three_way(ctx)
    invoice.po_id = ctx.po.id if ctx.po else None
    s.add_all(CheckResult(invoice_id=invoice.id, code=f.code, category=f.category, outcome=f.outcome,
                          severity=f.severity, message=f.message, evidence=f.evidence, check_ver=CHECK_VERSION)
              for f in matched)
    _transition(s, invoice, "MATCHED", _summary(matched))
    return checks + matched


def human_corrections(s: Session, invoice_id: int) -> dict[str, object]:
    """The reviewer's current corrections to header fields, as {field: value}."""
    rows = s.scalars(select(FieldValue).where(FieldValue.invoice_id == invoice_id, FieldValue.source == "human",
                                              FieldValue.is_current, FieldValue.invoice_line_id.is_(None))).all()
    return {row.field: row.value for row in rows}


def _refresh_columns(invoice: Invoice, r: Resolved) -> None:
    def dec(x: float | None) -> Decimal | None:
        return None if x is None else Decimal(str(x))

    invoice.vendor_name, invoice.currency, invoice.payable_currency = r.vendor_name, r.currency, r.currency
    invoice.invoice_number, invoice.invoice_number_norm = r.invoice_number, r.invoice_number_norm
    invoice.invoice_date = dt.date.fromisoformat(r.invoice_date) if r.invoice_date else None
    invoice.due_date = dt.date.fromisoformat(r.due_date) if r.due_date else None
    invoice.due_date_source = "printed" if r.due_date else None
    invoice.subtotal, invoice.tax, invoice.total = dec(r.subtotal), dec(r.tax), dec(r.total)
    invoice.payable_amount = invoice.total


def recheck(s: Session, invoice: Invoice, document: Document, why: str) -> list[Finding]:
    """Check an invoice again (after a correction or a re-read): its findings and line matches are replaced.
    Duplicate and match checks still compare it only with invoices that arrived before it."""
    before = sorted({c.code for c in s.scalars(select(CheckResult).where(CheckResult.invoice_id == invoice.id))
                     if c.outcome in ("fail", "warn")})
    s.execute(delete(CheckResult).where(CheckResult.invoice_id == invoice.id))
    s.execute(delete(MatchLine).where(MatchLine.invoice_id == invoice.id))
    s.flush()
    chain.record(s, invoice.workspace_id, "system", "invoice.rechecked", "invoice", invoice.id,
                 {"why": why, "raised_before": before})
    return check_invoice(s, invoice, document, document.received_at.date())


def _summary(findings: list[Finding]) -> dict:
    raised = [f for f in findings if f.outcome in ("fail", "warn")]
    return {"raised": [f.code for f in raised], "severity": dict(Counter(f.severity for f in raised))}
