"""After the checks: approve automatically, or send to a person with plain-language labels saying why.

Auto-approval (decision 11) needs all of: no finding that needs review or blocks payment; every key field rated
high confidence; a matched purchase order; and a total of at most USD 10,000 at the approximate rates below
(decision 34). Every auto-approval is recorded with its reasons; everything else gets a review task.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import chain
from ..db.models import Approval, CheckResult, Document, FieldValue, Invoice, InvoiceLine, PurchaseOrder, ReviewTask
from ..extraction.evidence import KEY_FIELDS, counts_for_tier
from ..payments import runs

RULES_VERSION = "rules-1"
AUTO_APPROVE_CAP_USD = Decimal(10_000)
# Decision 34: round, approximate rates (USD per unit) for the cap only; never used to book amounts
FX_TO_USD = {"USD": Decimal("1"), "EUR": Decimal("1.10"), "GBP": Decimal("1.30"), "ZAR": Decimal("0.055"),
             "NAD": Decimal("0.055"), "ZMW": Decimal("0.040")}
FX_AS_OF = "2026-10-01 (approximate)"

# what each finding means to a reviewer, in a few words
LABELS = {
    "dup.same_file": "duplicate: same file", "dup.vendor_number": "duplicate: same invoice number",
    "dup.same_amount": "possible duplicate", "dup.same_content": "possible duplicate",
    "bank.changed": "bank details changed", "vendor.unknown": "unknown vendor", "vendor.blocked": "vendor blocked",
    "vendor.tax_id_mismatch": "tax ID mismatch", "match.no_po": "no PO", "match.po_not_found": "PO not found",
    "match.po_vendor_mismatch": "PO from another vendor", "match.price_over_po": "price over PO",
    "match.qty_over_received": "more than received", "match.line_not_on_po": "line not on PO",
    "match.charges_not_on_po": "charges not on PO", "pattern.above_history": "unusual amount",
    "pattern.future_dated": "future date", "pattern.stamp": "stamped PAID/APPROVED",
    "pattern.wrong_addressee": "not addressed to us", "doctype.multiple_invoices": "several invoices in file",
    "arith.lines_vs_subtotal": "doesn't add up", "arith.total_mismatch": "doesn't add up",
    "arith.tax_rate_mismatch": "VAT doesn't match rate", "arith.lines_vs_total": "doesn't add up",
}


FIELD_WORDS = {"vendor_name": "vendor", "invoice_number": "invoice number", "invoice_date": "invoice date",
               "po_number": "PO number", "vendor_tax_id": "vendor tax ID",
               "due_date": "due date", "currency": "currency", "subtotal": "subtotal", "tax": "tax", "total": "total",
               "line_amount": "amount", "line_description": "description"}


def doubtful_fields(s: Session, invoice: Invoice) -> list[str]:
    """The values that keep the invoice below high confidence, in words ('subtotal', 'line 3 amount')."""
    lines = {ln.id: ln.line_no for ln in s.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id))}
    rows = [r for r in s.scalars(select(FieldValue).where(FieldValue.invoice_id == invoice.id))
            if counts_for_tier(r) and r.tier != "high"]
    order = {f: k for k, f in enumerate(KEY_FIELDS)}
    rows.sort(key=lambda r: (r.invoice_line_id is not None, order.get(r.field, 99), lines.get(r.invoice_line_id, 0)))
    words = []
    for r in rows:
        word = FIELD_WORDS.get(r.field, r.field)
        word = f"line {lines.get(r.invoice_line_id, '?')} {word}" if r.invoice_line_id else word
        if word not in words:
            words.append(word)
    return words


def usd(amount: Decimal | None, currency: str | None) -> Decimal | None:
    rate = FX_TO_USD.get((currency or "").upper())
    return None if amount is None or rate is None else (amount * rate).quantize(Decimal("0.01"))


def assess(s: Session, invoice: Invoice) -> tuple[bool, list[str], list[dict]]:
    """(auto-approve?, labels, reasons). Reasons are plain-language lines with a 'kind': ok / review / block."""
    findings = s.scalars(select(CheckResult).where(CheckResult.invoice_id == invoice.id).order_by(CheckResult.id)).all()
    labels: list[str] = []
    reasons: list[dict] = []
    for f in findings:
        if f.outcome in ("fail", "warn") and f.severity in ("review", "block"):
            label = LABELS.get(f.code, f.code)
            if label not in labels:
                labels.append(label)
            reasons.append({"kind": f.severity, "code": f.code, "text": f.message})
    tier = invoice.conf_tier or "low"
    if tier != "high":
        doubtful = doubtful_fields(s, invoice)
        what = (", ".join(doubtful[:6]) + (f" and {len(doubtful) - 6} more" if len(doubtful) > 6 else "")) if doubtful \
            else "at least one value"
        labels.append(f"{tier} confidence")
        reasons.append({"kind": "review", "code": f"confidence.{tier}",
                        "text": f"Not sure enough of {what}: check against the page (and correct if misread)."})
    po = s.get(PurchaseOrder, invoice.po_id) if invoice.po_id else None
    in_usd = usd(invoice.total, invoice.currency)
    if invoice.total is None:
        labels.append("total not read")
        reasons.append({"kind": "review", "code": "cap.no_total",
                        "text": "The amount to pay couldn't be read, so it can't be approved automatically."})
    elif in_usd is None:
        labels.append("currency not in FX table")
        reasons.append({"kind": "review", "code": "cap.no_rate",
                        "text": f"No rate for {invoice.currency or 'an unknown currency'}, so the "
                                f"USD {AUTO_APPROVE_CAP_USD:,.0f} cap can't be checked."})
    elif in_usd > AUTO_APPROVE_CAP_USD:
        labels.append(f"over USD {AUTO_APPROVE_CAP_USD:,.0f}")
        reasons.append({"kind": "review", "code": "cap.over",
                        "text": f"About USD {in_usd:,.0f} (rates of {FX_AS_OF}): above the "
                                f"USD {AUTO_APPROVE_CAP_USD:,.0f} limit for automatic approval."})
    if po is None and not reasons:  # the match check explains a missing PO on an invoice; this is the safety net
        if invoice.doc_type == "credit_note":
            labels.append("credit note")
            reasons.append({"kind": "review", "code": "po.credit_note",
                            "text": "Credit notes aren't matched to a purchase order, so a person confirms them "
                                    "before they reduce what we pay."})
        else:
            labels.append("no PO matched")
            reasons.append({"kind": "review", "code": "po.none", "text": "No purchase order was matched."})
    auto = not reasons
    if invoice.doc_type == "credit_note" and "credit note" not in labels:
        labels.append("credit note")  # context for the reviewer
    if auto:
        reasons = [{"kind": "ok", "code": "checks", "text": "Every check passed."},
                   {"kind": "ok", "code": "confidence", "text": "Every key value was read with high confidence."},
                   {"kind": "ok", "code": "po", "text": f"Matches PO {po.po_number} and what was received."},
                   {"kind": "ok", "code": "cap", "text": f"About USD {in_usd:,.0f}, within the "
                                                         f"USD {AUTO_APPROVE_CAP_USD:,.0f} limit."}]
    return auto, labels, reasons


def decide(s: Session, invoice: Invoice, *, allow_auto: bool = True) -> str:
    """Approve automatically or open (or refresh) a review task. Returns the invoice's new state.

    allow_auto=False is used when a person is already reviewing it (after a correction): the task's labels are
    refreshed, but the decision stays theirs.
    """
    if invoice.doc_type not in ("invoice", "credit_note"):
        return invoice.state  # not a bill: nothing to decide
    auto, labels, reasons = assess(s, invoice)
    old = invoice.state
    task = s.scalars(select(ReviewTask).where(ReviewTask.invoice_id == invoice.id, ReviewTask.status == "open")).first()
    if auto and allow_auto and task is None:
        invoice.state = "AUTO_APPROVED"
        invoice.review_labels = []
        approval = Approval(invoice_id=invoice.id, kind="auto", amount=invoice.total, currency=invoice.currency,
                            reasons=reasons, rules_ver=RULES_VERSION)
        s.add(approval)
        s.flush()
        chain.record(s, invoice.workspace_id, "system", "invoice.state_changed", "invoice", invoice.id,
                     {"from": old, "to": "AUTO_APPROVED", "rules": RULES_VERSION,
                      "reasons": [r["text"] for r in reasons]})
        runs.approved(s, approval, s.get(Document, invoice.document_id).received_at.date())  # decision 42
        return invoice.state
    if task is None:
        s.add(ReviewTask(invoice_id=invoice.id, labels=labels, reasons=reasons, status="open"))
    else:
        task.labels, task.reasons = labels, reasons
    invoice.review_labels = labels
    if old != "NEEDS_REVIEW":
        invoice.state = "NEEDS_REVIEW"
        chain.record(s, invoice.workspace_id, "system", "invoice.state_changed", "invoice", invoice.id,
                     {"from": old, "to": "NEEDS_REVIEW", "rules": RULES_VERSION, "labels": labels})
    return invoice.state
