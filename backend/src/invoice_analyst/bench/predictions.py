"""Benchmark mode output: one prediction per document in the official scorer's format."""

from __future__ import annotations

from typing import Any

from ..checks.arithmetic import arithmetic_issues
from ..extraction.resolve import primary, resolve
from ..extraction.schemas import FileExtraction


def flag_reasons(extraction: FileExtraction) -> list[str]:
    """Why a person must look at this file: several invoices in it, or the first one doesn't add up."""
    reasons = []
    numbers = {(inv.invoice_number.text or "").strip() for inv in extraction.invoices}
    if len(extraction.invoices) > 1 and len(numbers) > 1:
        reasons.append("file.multiple_invoices")
    reasons += [i.code for i in arithmetic_issues(primary(extraction))]
    return reasons


def to_prediction(extraction: FileExtraction) -> dict[str, Any]:
    r = resolve(primary(extraction))
    return {
        "vendor_name": r.vendor_name,
        "invoice_number": r.invoice_number,
        "invoice_date": r.invoice_date,
        "due_date": r.due_date,
        "currency": r.currency,
        "subtotal": r.subtotal,
        "tax": r.tax,
        "total_amount": r.total,
        "document_type": r.document_type,
        "line_items": r.lines,
        "flagged": bool(flag_reasons(extraction)),
        "references": r.references,
    }
