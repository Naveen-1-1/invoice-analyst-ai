"""What a check reports, and how serious each finding is (decision 28)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import Document, Entity, Invoice, PurchaseOrder, Vendor
from ..extraction.resolve import Resolved
from ..extraction.schemas import InvoiceDoc

CHECK_VERSION = "checks-1"

# decision 28: these stop payment until someone overrides them with a note
BLOCK = {"dup.same_file", "dup.vendor_number", "bank.changed", "vendor.blocked"}
# information only: shown, but never a reason to hold an invoice
INFO = {"pattern.round_total", "doctype.credit_note", "doctype.not_invoice"}
CATEGORY = {"arith": "arithmetic", "doctype": "doctype", "dup": "duplicate", "vendor": "vendor", "bank": "bank",
            "match": "match", "pattern": "pattern"}


@dataclass
class Finding:
    code: str  # e.g. dup.vendor_number, bank.changed, match.price_over_po
    outcome: str  # pass / warn / fail / skip
    message: str  # plain language, shown to the reviewer
    evidence: dict[str, Any] = field(default_factory=dict)
    related_invoice_id: int | None = None

    @property
    def category(self) -> str:
        return CATEGORY.get(self.code.split(".", 1)[0], "other")

    @property
    def severity(self) -> str:
        if self.outcome in ("pass", "skip") or self.code in INFO:
            return "info"
        return "block" if self.code in BLOCK else "review"


@dataclass
class Context:
    """One invoice being checked, with what the checks have learned so far (vendor, PO, ...)."""
    s: Session
    workspace_id: int
    invoice: Invoice
    document: Document
    inv: InvoiceDoc
    r: Resolved
    received: dt.date
    invoices_in_file: int
    vendor: Vendor | None = None
    entity: Entity | None = None
    po: PurchaseOrder | None = None
