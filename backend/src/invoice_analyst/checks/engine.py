"""Run every check on one invoice, store the findings, and move it to CHECKED then MATCHED (each step audited).

Order matters: the vendor is identified first (bank, duplicate, match and history checks use it), and duplicate and
three-way-match checks look only at invoices that arrived earlier, so the inbox must be checked in arrival order.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

from sqlalchemy.orm import Session

from ..audit import chain
from ..db.models import CheckResult, Document, Extraction, Invoice
from ..extraction.resolve import resolve
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
    ctx = Context(s=s, workspace_id=invoice.workspace_id, invoice=invoice, document=document, inv=inv, r=resolve(inv),
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


def _summary(findings: list[Finding]) -> dict:
    raised = [f for f in findings if f.outcome in ("fail", "warn")]
    return {"raised": [f.code for f in raised], "severity": dict(Counter(f.severity for f in raised))}
