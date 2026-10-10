"""Decision 41, pay what the reviewer confirmed: approving over a 'bank details changed' block makes the printed
account the one on file (the old one is closed, both kept); approving an unknown vendor's invoice adds the vendor,
with its printed bank details. The reviewer's note is the record, and both changes go into the audit trail.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import chain
from ..checks.normalize import last4, norm_account, norm_code, norm_name
from ..db.models import CheckResult, Extraction, Invoice, Vendor, VendorBankAccount
from ..extraction.schemas import InvoiceDoc
from ..payments.calendar import terms_days


def _printed(s: Session, inv: Invoice) -> InvoiceDoc:
    ext = s.get(Extraction, inv.extraction_id)
    return InvoiceDoc.model_validate(ext.raw["invoices"][inv.child_index])


def _account(vendor_id: int, doc: InvoiceDoc, today: dt.date, source: str) -> VendorBankAccount | None:
    b = doc.vendor.bank
    acct = norm_account(b.iban, b.account_number, b.sort_code) if b else None
    if acct is None:
        return None
    kind = "iban" if b.iban and norm_code(b.iban) else "sort_code" if b.sort_code else "account"  # type: ignore[union-attr]
    return VendorBankAccount(vendor_id=vendor_id, kind=kind, account_norm=acct, bank_name=b.bank_name,  # type: ignore[union-attr]
                             bic=b.swift_bic, branch=b.branch_code or b.sort_code, valid_from=today,  # type: ignore[union-attr]
                             source=source)


def _raised(s: Session, inv: Invoice, code: str) -> bool:
    return bool(s.scalar(select(func.count()).select_from(CheckResult).where(
        CheckResult.invoice_id == inv.id, CheckResult.code == code, CheckResult.outcome.in_(("fail", "warn")))))


def confirm(s: Session, inv: Invoice, today: dt.date, note: str) -> list[str]:
    """Apply what an approval confirms about the payee. Returns what changed, in words (empty: nothing)."""
    changes = []
    source = f"review of invoice {inv.id}"
    if inv.vendor_id is None and _raised(s, inv, "vendor.unknown"):
        doc = _printed(s, inv)
        n = s.scalar(select(func.count()).select_from(Vendor).where(Vendor.workspace_id == inv.workspace_id,
                                                                    Vendor.origin == "review")) or 0
        name = doc.vendor.name.text or inv.vendor_name or "Unnamed vendor"
        vendor = Vendor(workspace_id=inv.workspace_id, code=f"R{n + 1:04d}", name=name, name_norm=norm_name(name),
                        aliases=[], country=(doc.vendor.country or "").upper() or None, address=doc.vendor.address,
                        tax_id=doc.vendor.tax_id.text, tax_id_norm=norm_code(doc.vendor.tax_id.text) or None,
                        email=doc.vendor.email, terms_days=terms_days(doc.payment_terms.text), default_gl="5000",
                        status="active", origin="review")
        s.add(vendor)
        s.flush()
        inv.vendor_id = vendor.id
        account = _account(vendor.id, doc, today, source)
        if account:
            s.add(account)
        changes.append(f"added {name} to the vendor list"
                       + (f" with account {last4(account.account_norm)}" if account else " (no bank details printed)"))
        chain.record(s, inv.workspace_id, "user", "vendor.added", "vendor", vendor.id,
                     {"code": vendor.code, "name": name, "account": last4(account.account_norm) if account else None,
                      "invoice_id": inv.id, "note": note})
    elif inv.vendor_id is not None and _raised(s, inv, "bank.changed"):
        account = _account(inv.vendor_id, _printed(s, inv), today, source)
        if account is not None:
            old = s.scalars(select(VendorBankAccount).where(VendorBankAccount.vendor_id == inv.vendor_id,
                                                            VendorBankAccount.valid_to.is_(None))).all()
            for a in old:
                a.valid_to = today
            s.add(account)
            s.flush()
            changes.append(f"bank account on file is now {last4(account.account_norm)} "
                           f"(was {', '.join(last4(a.account_norm) for a in old) or 'none'})")
            chain.record(s, inv.workspace_id, "user", "vendor.bank_account_changed", "vendor", inv.vendor_id,
                         {"from": [last4(a.account_norm) for a in old], "to": last4(account.account_norm),
                          "invoice_id": inv.id, "note": note})
    return changes
