"""The view-only screens' data: vendors, POs and deliveries, settings, model calls."""

from __future__ import annotations

from fastapi.testclient import TestClient

from invoice_analyst.api import main
from invoice_analyst.checks.engine import check_invoice
from invoice_analyst.decision import review, rules

from .conftest import make_invoice


def test_vendor_po_and_settings_screens(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv, doc = make_invoice(s, wid, number="S1", qty=6)  # 6 of the 10 boxes received
        inv.conf_tier = "high"
        check_invoice(s, inv, doc, doc.received_at.date())
        rules.decide(s, inv)
        rejected, rdoc = make_invoice(s, wid, number="S2", qty=4, price=101.0)
        check_invoice(s, rejected, rdoc, rdoc.received_at.date())
        rules.decide(s, rejected)
        review.reject(s, rejected, "not ours")  # its 4 boxes no longer count as invoiced
        doc_id = doc.id

    def session():
        with sessions() as s:
            yield s

    main.app.dependency_overrides[main.db] = session
    try:
        api = TestClient(main.app)
        vendors = api.get(f"/api/workspaces/{wid}/vendors").json()
        assert [(v["code"], v["account"], v["invoices"], v["purchase_orders"]) for v in vendors] == [("V0001", "…7890", 2, 1)]
        vendor = api.get(f"/api/vendors/{vendors[0]['id']}").json()
        assert [a["account"] for a in vendor["accounts"]] == ["…7890"]
        assert {i["invoice_number"]: i["state"] for i in vendor["invoices"]} == {"S1": "AUTO_APPROVED", "S2": "REJECTED"}
        po = api.get(f"/api/workspaces/{wid}/purchase-orders").json()[0]
        assert (po["po_number"], po["received_share"], po["invoiced_share"]) == ("PO-100", 1.0, 0.6)
        detail = api.get(f"/api/purchase-orders/{po['id']}").json()
        boxes, service = detail["lines"]
        assert (boxes["qty"], boxes["received"], boxes["invoiced"], boxes["open"]) == ("10.000000", "10.000000",
                                                                                      "6.000000", "4.000000")
        assert service["qty"] is None and service["open"] is None  # a service is matched on amount
        assert [r["grn_number"] for r in detail["receipts"]] == ["GRN-1"]
        assert {i["invoice_number"] for i in detail["invoices"]} == {"S1", "S2"}
        assert [d["grn_number"] for d in api.get(f"/api/workspaces/{wid}/deliveries").json()] == ["GRN-1"]

        settings = api.get("/api/settings").json()
        titles = [sec["title"] for sec in settings["sections"]]
        assert titles == ["Models", "Confidence", "Checks", "Three-way match", "Approval", "Payments and books"]
        cap = next(i for sec in settings["sections"] for i in sec["items"] if i["name"] == "Approved automatically when")
        assert "USD 10,000" in cap["value"] and cap["decision"] == 11
        assert {d["number"] for d in settings["decisions"]} >= {11, 26, 28, 34}
        assert any(w["id"] == wid for w in settings["workspaces"])

        assert api.get(f"/api/documents/{doc_id}/model-calls").json() == []  # a hand-built document: no calls
        events = api.get(f"/api/documents/{doc_id}").json()["events"]
        assert all("subject_type" in e for e in events)
    finally:
        main.app.dependency_overrides.clear()
