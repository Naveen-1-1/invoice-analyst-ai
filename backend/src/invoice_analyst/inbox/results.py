"""Write an inbox run's outcome (per arrival: vendor, company, PO, every check raised, and the decision with its
review labels) for bench/'s AP eval."""

from __future__ import annotations

import json

from sqlalchemy import select

from ..config import ROOT
from ..db.engine import session_factory
from ..db.models import CheckResult, Document, Entity, Invoice, PurchaseOrder, Run, Vendor

RUNS_DIR = ROOT / "data" / "runs"


def write_results(run_id: int) -> dict:
    with session_factory()() as s:
        run = s.get(Run, run_id)
        items = []
        for doc in s.scalars(select(Document).where(Document.workspace_id == run.workspace_id,
                                                    Document.feed_seq.is_not(None)).order_by(Document.feed_seq)).all():
            entry = {"seq": doc.feed_seq, "ref": doc.bench_doc_id, "document_state": doc.state, "invoices": []}
            for inv in s.scalars(select(Invoice).where(Invoice.document_id == doc.id).order_by(Invoice.child_index)):
                vendor = s.get(Vendor, inv.vendor_id) if inv.vendor_id else None
                entity = s.get(Entity, inv.entity_id) if inv.entity_id else None
                po = s.get(PurchaseOrder, inv.po_id) if inv.po_id else None
                checks = s.scalars(select(CheckResult).where(CheckResult.invoice_id == inv.id).order_by(CheckResult.id))
                entry["invoices"].append({
                    "child_index": inv.child_index, "doc_type": inv.doc_type, "state": inv.state,
                    "conf_tier": inv.conf_tier, "vendor_code": vendor.code if vendor else None,
                    "entity": entity.name if entity else None, "po_number": po.po_number if po else None,
                    "invoice_number": inv.invoice_number, "total": float(inv.total) if inv.total is not None else None,
                    "currency": inv.currency, "vendor_name": inv.vendor_name,
                    "review_labels": list(inv.review_labels or []),
                    "checks": [{"code": c.code, "outcome": c.outcome, "severity": c.severity} for c in checks]})
            items.append(entry)
    out = {"run": run_id, "set": (run.config or {}).get("set"), "items": items}
    path = RUNS_DIR / f"{run_id:04d}" / "ap_results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1) + "\n")
    return out
