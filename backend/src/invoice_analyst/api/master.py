"""Vendors, purchase orders and deliveries of a workspace, view-only (decision 48)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..checks.normalize import last4
from ..db.models import (Entity, GoodsReceipt, GoodsReceiptLine, Invoice, MatchLine, PaymentItem, PoLine,
                         PurchaseOrder, Vendor, VendorBankAccount, VendorHistory)
from . import schemas as out
from .deps import db

router = APIRouter()


def _discount(v: Vendor) -> str | None:
    return f"{v.discount_pct.normalize():f}% in {v.discount_days} days" if v.discount_pct and v.discount_days else None


def _payment(s: Session, invoice_ids: list[int]) -> dict[int, str]:
    statuses: dict[int, set[str]] = defaultdict(set)
    for inv_id, status in s.execute(select(PaymentItem.invoice_id, PaymentItem.status)
                                    .where(PaymentItem.invoice_id.in_(invoice_ids))):
        statuses[inv_id].add(status)
    return {k: "paid" if v == {"paid"} else "waiting" if "waiting" in v else "planned" for k, v in statuses.items()}


def _po_quantities(s: Session, po_ids: list[int]) -> dict[int, dict[str, Decimal]]:
    """Per PO line: received (all deliveries) and invoiced (used up by invoices that weren't rejected)."""
    received = dict(s.execute(select(GoodsReceiptLine.po_line_id, func.sum(GoodsReceiptLine.qty))
                              .join(PoLine, PoLine.id == GoodsReceiptLine.po_line_id).where(PoLine.po_id.in_(po_ids))
                              .group_by(GoodsReceiptLine.po_line_id)).all())
    invoiced = dict(s.execute(select(MatchLine.po_line_id, func.sum(MatchLine.qty_approved))
                              .join(Invoice, Invoice.id == MatchLine.invoice_id)
                              .join(PoLine, PoLine.id == MatchLine.po_line_id)
                              .where(PoLine.po_id.in_(po_ids), Invoice.state != "REJECTED")
                              .group_by(MatchLine.po_line_id)).all())
    return {"received": received, "invoiced": invoiced}  # type: ignore[return-value]


def _po_rows(s: Session, pos: list[PurchaseOrder]) -> list[out.PoRow]:
    if not pos:
        return []
    ids = [p.id for p in pos]
    lines: dict[int, list[PoLine]] = defaultdict(list)
    for pl in s.scalars(select(PoLine).where(PoLine.po_id.in_(ids)).order_by(PoLine.line_no)):
        lines[pl.po_id].append(pl)
    q = _po_quantities(s, ids)
    vendors = {v.id: v.name for v in s.scalars(select(Vendor).where(Vendor.id.in_({p.vendor_id for p in pos})))}
    entities = {e.id: e.name for e in s.scalars(select(Entity).where(Entity.id.in_({p.entity_id for p in pos})))}
    rows = []
    for p in pos:
        counted = [pl for pl in lines[p.id] if pl.qty]
        ordered = sum((pl.qty for pl in counted), Decimal(0))
        got = sum((Decimal(q["received"].get(pl.id) or 0) for pl in counted), Decimal(0))
        billed = sum((Decimal(q["invoiced"].get(pl.id) or 0) for pl in counted), Decimal(0))
        rows.append(out.PoRow(id=p.id, po_number=p.po_number, vendor_id=p.vendor_id, vendor=vendors.get(p.vendor_id),
                              entity=entities.get(p.entity_id), currency=p.currency, order_date=p.order_date,
                              status=p.status, lines=len(lines[p.id]),
                              ordered=sum((pl.amount or Decimal(0) for pl in lines[p.id]), Decimal(0)),
                              received_share=float(got / ordered) if ordered else None,
                              invoiced_share=float(billed / ordered) if ordered else None))
    return rows


@router.get("/api/workspaces/{workspace_id}/vendors", response_model=list[out.VendorRow], name="listVendors")
def list_vendors(workspace_id: int, s: Session = Depends(db)) -> list[out.VendorRow]:
    vendors = s.scalars(select(Vendor).where(Vendor.workspace_id == workspace_id).order_by(Vendor.name)).all()
    invoices = dict(s.execute(select(Invoice.vendor_id, func.count()).where(Invoice.workspace_id == workspace_id)
                              .group_by(Invoice.vendor_id)).all())
    pos = dict(s.execute(select(PurchaseOrder.vendor_id, func.count()).where(PurchaseOrder.workspace_id == workspace_id)
                         .group_by(PurchaseOrder.vendor_id)).all())
    accounts = {a.vendor_id: a for a in s.scalars(select(VendorBankAccount).join(Vendor, Vendor.id == VendorBankAccount.vendor_id)
                                                  .where(Vendor.workspace_id == workspace_id,
                                                         VendorBankAccount.valid_to.is_(None)))}
    return [out.VendorRow(id=v.id, code=v.code, name=v.name, country=v.country, status=v.status, origin=v.origin,
                          terms_days=v.terms_days, discount=_discount(v),
                          account=last4(accounts[v.id].account_norm) if v.id in accounts else None,
                          invoices=invoices.get(v.id, 0), purchase_orders=pos.get(v.id, 0)) for v in vendors]


@router.get("/api/vendors/{vendor_id}", response_model=out.VendorDetail, name="getVendor")
def get_vendor(vendor_id: int, s: Session = Depends(db)) -> out.VendorDetail:
    v = s.get(Vendor, vendor_id)
    if v is None:
        raise HTTPException(404, "no such vendor")
    accounts = s.scalars(select(VendorBankAccount).where(VendorBankAccount.vendor_id == v.id)
                         .order_by(VendorBankAccount.id)).all()
    history = s.scalars(select(VendorHistory).where(VendorHistory.vendor_id == v.id)
                        .order_by(VendorHistory.invoice_date)).all()
    invoices = s.scalars(select(Invoice).where(Invoice.vendor_id == v.id).order_by(Invoice.invoice_date)).all()
    pay = _payment(s, [i.id for i in invoices])
    pos = s.scalars(select(PurchaseOrder).where(PurchaseOrder.vendor_id == v.id).order_by(PurchaseOrder.po_number)).all()
    return out.VendorDetail(
        id=v.id, workspace_id=v.workspace_id, code=v.code, name=v.name, aliases=v.aliases or [], country=v.country,
        address=v.address, tax_id=v.tax_id, email=v.email, status=v.status, origin=v.origin, terms_days=v.terms_days,
        discount=_discount(v), default_gl=v.default_gl,
        accounts=[out.BankAccountOut(kind=a.kind, account=last4(a.account_norm), bank_name=a.bank_name, bic=a.bic,
                                     branch=a.branch, valid_from=a.valid_from, valid_to=a.valid_to, source=a.source)
                  for a in accounts],
        history=[out.HistoryOut(invoice_number=h.invoice_number, invoice_date=h.invoice_date, amount=h.amount,
                                currency=h.currency) for h in history],
        invoices=[out.VendorInvoiceOut(invoice_id=i.id, document_id=i.document_id, invoice_number=i.invoice_number,
                                       invoice_date=i.invoice_date, total=i.total, currency=i.currency, state=i.state,
                                       payment=pay.get(i.id)) for i in invoices],
        purchase_orders=_po_rows(s, list(pos)))


@router.get("/api/workspaces/{workspace_id}/purchase-orders", response_model=list[out.PoRow], name="listPurchaseOrders")
def list_purchase_orders(workspace_id: int, s: Session = Depends(db)) -> list[out.PoRow]:
    pos = s.scalars(select(PurchaseOrder).where(PurchaseOrder.workspace_id == workspace_id)
                    .order_by(PurchaseOrder.po_number)).all()
    return _po_rows(s, list(pos))


@router.get("/api/purchase-orders/{po_id}", response_model=out.PoDetail, name="getPurchaseOrder")
def get_purchase_order(po_id: int, s: Session = Depends(db)) -> out.PoDetail:
    po = s.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(404, "no such purchase order")
    lines = s.scalars(select(PoLine).where(PoLine.po_id == po.id).order_by(PoLine.line_no)).all()
    q = _po_quantities(s, [po.id])
    line_rows = []
    for pl in lines:
        got = Decimal(q["received"].get(pl.id) or 0) if pl.qty is not None else None
        billed = Decimal(q["invoiced"].get(pl.id) or 0) if pl.qty is not None else None
        line_rows.append(out.PoLineOut(line_no=pl.line_no, item_code=pl.item_code, description=pl.description,
                                       qty=pl.qty, unit_price=pl.unit_price, amount=pl.amount,
                                       gl_account=pl.gl_account, received=got, invoiced=billed,
                                       open=(got - billed) if got is not None and billed is not None else None))
    by_line = {pl.id: pl for pl in lines}
    receipts = []
    for gr in s.scalars(select(GoodsReceipt).where(GoodsReceipt.po_id == po.id).order_by(GoodsReceipt.received_date)):
        grl = s.scalars(select(GoodsReceiptLine).where(GoodsReceiptLine.goods_receipt_id == gr.id)).all()
        receipts.append(out.ReceiptOut(id=gr.id, grn_number=gr.grn_number, received_date=gr.received_date, lines=[
            out.ReceiptLineOut(line_no=by_line[x.po_line_id].line_no, description=by_line[x.po_line_id].description,
                               qty=x.qty) for x in grl if x.po_line_id in by_line]))
    invoices = s.scalars(select(Invoice).where(Invoice.po_id == po.id).order_by(Invoice.invoice_date)).all()
    return out.PoDetail(po=_po_rows(s, [po])[0], lines=line_rows, receipts=receipts, invoices=[
        out.PoInvoiceOut(invoice_id=i.id, document_id=i.document_id, invoice_number=i.invoice_number, state=i.state,
                         total=i.total, currency=i.currency) for i in invoices])


@router.get("/api/workspaces/{workspace_id}/deliveries", response_model=list[out.DeliveryRow], name="listDeliveries")
def list_deliveries(workspace_id: int, s: Session = Depends(db)) -> list[out.DeliveryRow]:
    rows = s.execute(select(GoodsReceipt, PurchaseOrder, Vendor.name, func.count(GoodsReceiptLine.id),
                            func.coalesce(func.sum(GoodsReceiptLine.qty), 0))
                     .join(PurchaseOrder, PurchaseOrder.id == GoodsReceipt.po_id)
                     .outerjoin(Vendor, Vendor.id == PurchaseOrder.vendor_id)
                     .outerjoin(GoodsReceiptLine, GoodsReceiptLine.goods_receipt_id == GoodsReceipt.id)
                     .where(PurchaseOrder.workspace_id == workspace_id)
                     .group_by(GoodsReceipt.id, PurchaseOrder.id, Vendor.name)
                     .order_by(GoodsReceipt.received_date, GoodsReceipt.grn_number)).all()
    return [out.DeliveryRow(id=gr.id, grn_number=gr.grn_number, received_date=gr.received_date, po_id=po.id,
                            po_number=po.po_number, vendor=vendor, lines=n, quantity=qty)
            for gr, po, vendor, n, qty in rows]
