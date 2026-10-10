"""Deciding after the checks (auto-approve or review) and the reviewer's actions, on hand-built invoices."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from invoice_analyst.checks.engine import check_invoice
from invoice_analyst.db.models import Approval, AuditEvent, FieldValue, Invoice, ReviewTask
from invoice_analyst.decision import review, rules
from invoice_analyst.extraction import corrections

from .conftest import make_invoice


def arrive(s: Session, wid: int, *, tier: str = "high", **kw) -> Invoice:
    """An invoice through checks and the decision, as the sequencer does it."""
    inv, doc = make_invoice(s, wid, **kw)
    inv.conf_tier = tier
    check_invoice(s, inv, doc, doc.received_at.date())
    rules.decide(s, inv)
    return inv


def task(s: Session, inv: Invoice) -> ReviewTask:
    t = review.open_task(s, inv)
    assert t is not None
    return t


def test_clean_invoice_is_approved_automatically_with_its_reasons(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv = arrive(s, wid)
        assert inv.state == "AUTO_APPROVED"
        approval = s.scalars(select(Approval).where(Approval.invoice_id == inv.id)).one()
        assert approval.kind == "auto" and approval.amount == Decimal("1500.00") and approval.rules_ver
        assert [r["code"] for r in approval.reasons] == ["checks", "confidence", "po", "cap"]
        assert review.open_task(s, inv) is None


def test_each_reason_for_review_gets_a_label(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        assert "no PO" in arrive(s, wid, number="R1", po=None).review_labels
        assert "medium confidence" in arrive(s, wid, number="R2", tier="medium", qty=1).review_labels
        inv = arrive(s, wid, number="R3", qty=0, service=0)  # nothing on it to pay against the PO, but clean
        inv.total, inv.state = Decimal("200000"), "MATCHED"  # ZAR 200,000 is about USD 11,000
        assert rules.decide(s, inv) == "NEEDS_REVIEW" and "over USD 10,000" in inv.review_labels
        inv = arrive(s, wid, number="R4", qty=0, service=0)
        inv.currency, inv.state = "XYZ", "MATCHED"
        rules.decide(s, inv)
        assert "currency not in FX table" in inv.review_labels
    assert rules.usd(Decimal("100"), "GBP") == Decimal("130.00") and rules.usd(Decimal("1"), "???") is None


def test_payment_blocks_need_an_override_and_every_action_a_note(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        arrive(s, wid, number="D1", po=None)
        dup = arrive(s, wid, number="D1", po=None)  # same vendor + number: blocks payment
        assert "duplicate: same invoice number" in dup.review_labels
        with pytest.raises(review.ReviewError, match="note"):
            review.approve(s, dup, "  ")
        with pytest.raises(review.ReviewError, match="override"):
            review.approve(s, dup, "vendor confirmed it is a new invoice")
        approval = review.approve(s, dup, "vendor confirmed it is a new invoice", override=True)
        assert dup.state == "APPROVED" and approval.kind == "manual"
        assert any(r["kind"] == "override" and r["code"] == "dup.vendor_number" for r in approval.reasons)
        assert review.open_task(s, dup) is None
        with pytest.raises(review.ReviewError, match="nothing to decide"):
            review.reject(s, dup, "too late")


def test_a_correction_checks_the_invoice_again_and_keeps_the_original(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv = arrive(s, wid, number="C1", po="PO-999")
        assert "PO not found" in inv.review_labels
        row = review.correct(s, inv, "po_number", "PO-100")
        assert inv.state == "NEEDS_REVIEW"  # clean now, but a person is reviewing it: the decision stays theirs
        assert inv.review_labels == [] and inv.po_id is not None
        assert [r["code"] for r in task(s, inv).reasons] == ["checks", "confidence", "po", "cap"]
        assert row.source == "human" and row.tier == "high" and row.is_current
        events = [e.action for e in s.scalars(select(AuditEvent).where(AuditEvent.subject_id == inv.id,
                                                                       AuditEvent.subject_type == "invoice"))]
        assert "field.corrected" in events and "invoice.rechecked" in events
        with pytest.raises(review.ReviewError, match="already"):
            review.correct(s, inv, "po_number", "PO-100")
        review.correct(s, inv, "invoice_date", "2026-01-16")
        assert str(inv.invoice_date) == "2026-01-16"
        dates = s.scalars(select(FieldValue).where(FieldValue.invoice_id == inv.id,
                                                   FieldValue.field == "invoice_date")).all()
        assert [fv.is_current for fv in dates] == [True]  # no model reading in this hand-built invoice
        review.approve(s, inv, "PO number was misread")
        assert inv.state == "APPROVED"


def test_corrected_values_are_normalised_or_refused() -> None:
    assert corrections.normalize("invoice_date", "13/02/2026") == "2026-02-13"
    assert corrections.normalize("total", "R 1 234,50") == "1234.50"
    assert corrections.normalize("currency", "zar") == "ZAR"
    assert corrections.normalize("po_number", "") is None
    for field, text in (("total", "abc"), ("currency", "rand"), ("invoice_number", "KH"), ("vendor_name", "")):
        with pytest.raises(corrections.CorrectionError):
            corrections.normalize(field, text)


def test_partial_approval_pays_what_was_received_with_tax_in_proportion(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv = arrive(s, wid, number="P1", qty=15)  # 15 boxes invoiced, 10 received; service 500 matched by amount
        assert "more than received" in inv.review_labels
        inv.tax, inv.total = Decimal("300.00"), Decimal("2300.00")  # 15% VAT on 2,000
        split = review.partial_split(s, inv)
        assert (split.approved_net, split.approved_tax, split.approved, split.held) == (
            Decimal("1500.00"), Decimal("225.00"), Decimal("1725.00"), Decimal("575.00"))
        review.approve_partial(s, inv, "pay for the 10 boxes delivered")
        assert inv.state == "APPROVED_PARTIAL" and task(s, inv).labels == ["rest on hold"]
        assert review.allowed_actions(s, inv) == ["approve", "reject"]
        review.approve(s, inv, "the other 5 boxes arrived")
        assert inv.state == "APPROVED" and review.approved_so_far(s, inv) == Decimal("2300.00")


def test_partial_approval_only_when_something_is_waiting_for_delivery(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv = arrive(s, wid, number="P2", tier="medium")
        assert review.partial_split(s, inv) is None and "approve_partial" not in review.allowed_actions(s, inv)
        with pytest.raises(review.ReviewError, match="waiting for delivery"):
            review.approve_partial(s, inv, "no")


def test_rejecting_frees_the_delivery_for_a_later_invoice(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        first = arrive(s, wid, number="J1", tier="medium")  # uses all 10 received boxes
        review.reject(s, first, "wrong vendor entity")
        assert first.state == "REJECTED"
        second = arrive(s, wid, number="J2", price=101.0)  # a different invoice (not a resubmission) for the boxes
        assert second.state == "AUTO_APPROVED"  # the 10 boxes count as not yet invoiced again


def test_hold_and_release(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv = arrive(s, wid, number="H1", po=None)
        review.hold(s, inv, "asked the vendor for the PO")
        assert inv.state == "ON_HOLD" and "release" in review.allowed_actions(s, inv)
        with pytest.raises(review.ReviewError):
            review.hold(s, inv, "again")
        review.release(s, inv, "vendor replied")
        assert inv.state == "NEEDS_REVIEW"


def test_statements_get_no_decision_and_credit_notes_are_labelled(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv, _ = make_invoice(s, wid, number="S1")
        inv.doc_type, inv.conf_tier = "statement", "high"
        assert rules.decide(s, inv) == "EXTRACTED"  # not a bill: no decision at all
        assert review.open_task(s, inv) is None
        cn = arrive(s, wid, number="CN1", po=None)
        cn.doc_type, cn.state = "credit_note", "MATCHED"
        rules.decide(s, cn)
        assert cn.review_labels == ["no PO", "credit note"]


def test_review_api_queue_detail_correct_and_decide(ws) -> None:
    from fastapi.testclient import TestClient

    from invoice_analyst.api import main

    sessions, wid = ws
    with sessions.begin() as s:
        blocked_first = arrive(s, wid, number="Q1", po=None)
        dup = arrive(s, wid, number="Q1", po=None)
        wrong_po = arrive(s, wid, number="Q2", po="PO-999", qty=1)
        ids = (blocked_first.id, dup.id, wrong_po.id)

    def session():
        with sessions() as s:
            yield s

    main.app.dependency_overrides[main.db] = session
    try:
        api = TestClient(main.app)
        queue = api.get("/api/review", params={"workspace_id": wid}).json()
        assert [q["invoice_id"] for q in queue][0] == ids[1]  # the payment-blocked duplicate comes first
        assert {q["invoice_id"] for q in queue} == set(ids)

        detail = api.get(f"/api/invoices/{ids[2]}/review").json()
        assert "PO not found" in detail["labels"] and "approve" in detail["actions"]
        assert detail["cap"]["usd_estimate"] == "33.00"

        fixed = api.post(f"/api/invoices/{ids[2]}/corrections", json={"field": "po_number", "text": "PO-100"}).json()
        assert fixed["labels"] == [] and fixed["po_number"] == "PO-100" and fixed["state"] == "NEEDS_REVIEW"
        bad = api.post(f"/api/invoices/{ids[2]}/corrections", json={"field": "total", "text": "lots"})
        assert bad.status_code == 409 and "amount" in bad.json()["detail"]

        refused = api.post(f"/api/invoices/{ids[1]}/decision", json={"action": "approve", "note": "ok"})
        assert refused.status_code == 409 and "override" in refused.json()["detail"]
        done = api.post(f"/api/invoices/{ids[1]}/decision",
                        json={"action": "approve", "note": "vendor confirmed", "override": True}).json()
        assert done["state"] == "APPROVED" and done["task"] is None and done["approvals"][0]["kind"] == "manual"
        assert ids[1] not in {q["invoice_id"] for q in api.get("/api/review", params={"workspace_id": wid}).json()}
    finally:
        main.app.dependency_overrides.clear()


def test_a_fresh_read_refreshes_the_invoice_but_keeps_corrections(ws) -> None:
    from invoice_analyst.db.models import Extraction, InvoiceLine
    from invoice_analyst.extraction import extract
    from invoice_analyst.extraction.schemas import FileExtraction

    sessions, wid = ws
    with sessions.begin() as s:
        inv = arrive(s, wid, number="X1", po=None)
        review.correct(s, inv, "invoice_number", "X1-FIXED")
        raw = s.get(Extraction, inv.extraction_id).raw
        ids = (inv.id, inv.document_id, inv.extraction_id)
    raw["invoices"][0]["lines"] = raw["invoices"][0]["lines"][:1]  # the new reading sees only the boxes line
    raw["invoices"][0]["total"] = {"text": "1000.00", "value": 1000.0}
    message = extract._refresh(sessions, ids[1], {ids[0]}, "text_llm", "m", {}, FileExtraction.model_validate(raw))
    assert "Read again" in message
    with sessions.begin() as s:
        inv = s.get(Invoice, ids[0])
        assert inv.extraction_id != ids[2] and inv.total == Decimal("1000.00")
        assert len(s.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)).all()) == 1
        numbers = s.scalars(select(FieldValue).where(FieldValue.invoice_id == inv.id,
                                                     FieldValue.field == "invoice_number")).all()
        current = [fv for fv in numbers if fv.is_current]
        assert [(fv.source, fv.value) for fv in current] == [("human", "X1-FIXED")]
        assert any(fv.source != "human" and fv.superseded_by == current[0].id for fv in numbers)
        review.refresh(s, inv, "read again by the model")
        assert inv.invoice_number == "X1-FIXED"  # the correction wins over the new reading
