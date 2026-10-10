"""What the books say: trial balance, what we owe (with ageing), and the two checks that must always hold.

  balanced    debits = credits in every currency (each entry balances, so the whole ledger must)
  reconciled  the accounts-payable balance = the payment items not yet paid, in every currency
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Account, Entity, Invoice, JournalEntry, JournalLine, PaymentItem, PaymentRun, Vendor
from .posting import AP

BUCKETS = ("not yet due", "1-30 days overdue", "31-60 days overdue", "61-90 days overdue", "over 90 days overdue")


@dataclass
class TrialRow:
    currency: str
    account: str
    name: str
    type: str
    debit: Decimal
    credit: Decimal

    @property
    def balance(self) -> Decimal:  # debit-positive
        return self.debit - self.credit


def trial_balance(s: Session, workspace_id: int, entity_id: int | None = None) -> list[TrialRow]:
    q = (select(JournalLine.currency, Account.code, Account.name, Account.type,
                func.sum(JournalLine.debit), func.sum(JournalLine.credit))
         .join(JournalEntry, JournalEntry.id == JournalLine.entry_id).join(Account, Account.code == JournalLine.account_code)
         .where(JournalEntry.workspace_id == workspace_id)
         .group_by(JournalLine.currency, Account.code, Account.name, Account.type)
         .order_by(JournalLine.currency, Account.code))
    if entity_id is not None:
        q = q.where(JournalEntry.entity_id == entity_id)
    return [TrialRow(*row) for row in s.execute(q).all()]


def balanced(rows: list[TrialRow]) -> dict[str, bool]:
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        sums[r.currency] += r.balance
    return {ccy: total == 0 for ccy, total in sorted(sums.items())}


@dataclass
class OpenItem:
    item_id: int
    invoice_id: int
    document_id: int
    vendor: str | None
    entity: str | None
    invoice_number: str | None
    currency: str
    amount: Decimal
    due_date: dt.date | None
    status: str
    run_date: dt.date | None
    bucket: str
    days_overdue: int


def _bucket(days: int) -> str:
    if days <= 0:
        return BUCKETS[0]
    return BUCKETS[1] if days <= 30 else BUCKETS[2] if days <= 60 else BUCKETS[3] if days <= 90 else BUCKETS[4]


def payables(s: Session, workspace_id: int, today: dt.date, entity_id: int | None = None) -> list[OpenItem]:
    """Everything booked and not yet paid, aged by due date as of today."""
    q = (select(PaymentItem, Invoice, Vendor, Entity, PaymentRun)
         .join(Invoice, Invoice.id == PaymentItem.invoice_id)
         .outerjoin(Vendor, Vendor.id == PaymentItem.vendor_id).outerjoin(Entity, Entity.id == Invoice.entity_id)
         .outerjoin(PaymentRun, PaymentRun.id == PaymentItem.run_id)
         .where(Invoice.workspace_id == workspace_id, PaymentItem.status != "paid")
         .order_by(PaymentItem.due_date, PaymentItem.id))
    if entity_id is not None:
        q = q.where(Invoice.entity_id == entity_id)
    out = []
    for item, inv, vendor, entity, run in s.execute(q).all():
        days = (today - item.due_date).days if item.due_date else 0
        out.append(OpenItem(item.id, inv.id, inv.document_id, vendor.name if vendor else inv.vendor_name,
                            entity.name if entity else None, inv.invoice_number, item.currency, item.amount,
                            item.due_date, item.status, run.run_date if run else None, _bucket(days), max(days, 0)))
    return out


@dataclass
class Reconciliation:
    currency: str
    ledger: Decimal  # accounts payable balance (credit-positive)
    open_items: Decimal
    ok: bool = field(init=False)

    def __post_init__(self) -> None:
        self.ok = self.ledger == self.open_items


def reconcile(s: Session, workspace_id: int) -> list[Reconciliation]:
    ledger = dict(s.execute(select(JournalLine.currency, func.sum(JournalLine.credit - JournalLine.debit))
                            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                            .where(JournalEntry.workspace_id == workspace_id, JournalLine.account_code == AP)
                            .group_by(JournalLine.currency)).all())
    items = dict(s.execute(select(PaymentItem.currency, func.sum(PaymentItem.amount))
                           .join(Invoice, Invoice.id == PaymentItem.invoice_id)
                           .where(Invoice.workspace_id == workspace_id, PaymentItem.status != "paid")
                           .group_by(PaymentItem.currency)).all())
    return [Reconciliation(ccy, Decimal(ledger.get(ccy) or 0), Decimal(items.get(ccy) or 0))
            for ccy in sorted(set(ledger) | set(items))]
