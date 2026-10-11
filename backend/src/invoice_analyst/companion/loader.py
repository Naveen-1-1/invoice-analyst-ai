"""Load generated companion data (our companies, vendors, POs, deliveries, vendor history) into a workspace.

Reads only data/<set>/companion/ and data/<set>/inbox/stream.json: never the labels (what the checks should
find), which only bench/ uses to score the results.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..audit import chain
from ..checks.normalize import norm_code, norm_name
from ..config import ROOT
from ..db.models import (Entity, GoodsReceipt, GoodsReceiptLine, PoLine, PurchaseOrder, Vendor, VendorBankAccount,
                         VendorHistory)

SETS = {"dev": ROOT / "data" / "dev", "heldout": ROOT / "data" / "heldout"}  # held-out: only in the final run


def _dir(set_name: str) -> Path:
    if set_name not in SETS:
        raise SystemExit(f"unknown companion set {set_name!r}; known: {', '.join(SETS)}")
    return SETS[set_name]


def _read(set_name: str, name: str) -> Any:
    return json.loads((_dir(set_name) / "companion" / name).read_text())


def stream(set_name: str) -> list[dict]:
    """The inbox: [{seq, ref, path (relative to the repo), received_date}] in arrival order."""
    items = json.loads((_dir(set_name) / "inbox" / "stream.json").read_text())
    return sorted(items, key=lambda s: s["seq"])


def _dec(x: Any) -> Decimal | None:
    return None if x is None else Decimal(str(x))


def load(s: Session, workspace_id: int, set_name: str) -> dict[str, int]:
    entities = {}
    for e in _read(set_name, "entities.json"):
        row = Entity(workspace_id=workspace_id, name=e["name"], aliases=[], country=e["country"], tax_id=e["tax_id"],
                     address=e["address"])
        s.add(row)
        entities[e["code"]] = row
    vendors = {}
    for v in _read(set_name, "vendors.json"):
        row = Vendor(workspace_id=workspace_id, code=v["code"], name=v["name"], name_norm=norm_name(v["name"]),
                     aliases=[], country=v["country"], address=v["address"], tax_id=v["tax_id"],
                     tax_id_norm=norm_code(v["tax_id"]) or None, email=v["email"], terms_days=v["terms_days"],
                     discount_pct=_dec(v["discount_pct"]), discount_days=v["discount_days"],
                     default_gl=v["default_gl"], status="active", origin=f"companion:{set_name}")
        s.add(row)
        s.flush()
        b = v["bank"]
        s.add(VendorBankAccount(vendor_id=row.id, kind=b["kind"], account_norm=b["account_norm"],
                                bank_name=b["bank_name"], bic=b["bic"], branch=b["branch"], source=f"companion:{set_name}"))
        vendors[v["code"]] = row
    s.flush()
    for code, items in _read(set_name, "vendor_history.json").items():
        for h in items:
            s.add(VendorHistory(vendor_id=vendors[code].id, invoice_number=h["invoice_number"],
                                invoice_date=dt.date.fromisoformat(h["invoice_date"]), amount=_dec(h["amount"]),
                                currency=h["currency"]))
    n_pos = 0
    for p in _read(set_name, "purchase_orders.json"):
        po = PurchaseOrder(workspace_id=workspace_id, po_number=p["po_number"], po_number_norm=norm_code(p["po_number"]),
                           vendor_id=vendors[p["vendor_code"]].id if p["vendor_code"] in vendors else None,
                           entity_id=entities[p["entity_code"]].id if p["entity_code"] in entities else None,
                           currency=p["currency"], order_date=dt.date.fromisoformat(p["order_date"]), status="open")
        s.add(po)
        s.flush()
        lines = {}
        for ln in p["lines"]:
            row = PoLine(po_id=po.id, line_no=ln["line_no"], item_code=ln["item_code"], description=ln["description"],
                         qty=_dec(ln["qty"]), unit_price=_dec(ln["unit_price"]), amount=_dec(ln["amount"]),
                         vat_rate=_dec(ln["vat_rate"]), gl_account=ln["gl_account"])
            s.add(row)
            lines[ln["line_no"]] = row
        s.flush()
        for r in p["receipts"]:
            gr = GoodsReceipt(po_id=po.id, grn_number=r["grn_number"],
                              received_date=dt.date.fromisoformat(r["received_date"]))
            s.add(gr)
            s.flush()
            for rl in r["lines"]:
                s.add(GoodsReceiptLine(goods_receipt_id=gr.id, po_line_id=lines[rl["po_line_no"]].id,
                                       qty=_dec(rl["qty"])))
        n_pos += 1
    counts = {"entities": len(entities), "vendors": len(vendors), "purchase_orders": n_pos}
    chain.record(s, workspace_id, "system", "companion.loaded", "workspace", workspace_id,
                 {"set": set_name, **counts})
    return counts
