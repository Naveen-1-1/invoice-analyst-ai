"""Booking, payment runs, payees and the audit check, on hand-built invoices."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from invoice_analyst.audit import chain
from invoice_analyst.audit.verify import verify
from invoice_analyst.checks.engine import check_invoice
from invoice_analyst.db.models import (Approval, CheckResult, Extraction, Invoice, JournalEntry, JournalLine,
                                       PaymentItem, PaymentRun, Run, Vendor, VendorBankAccount, Workspace)
from invoice_analyst.decision import review, rules
from invoice_analyst.ledger import reports
from invoice_analyst.payments import calendar, clock, runs

from .conftest import make_invoice

THU = calendar.RUN_WEEKDAY
DAY = dt.timedelta(days=1)


# ---------------------------------------------------------------- the calendar, every case in a window


def test_plan_holds_for_every_today_due_date_and_discount_deadline() -> None:
    """For each of 14 consecutive 'todays', every due date from 40 days before to 90 after and every discount
    deadline (none, or -10..+40 days): the run is a Thursday no earlier than the first open run; it's the last
    run on or before the discount deadline when that can still be made, else the last on or before the due date,
    else (overdue) the first open run."""
    start = dt.date(2026, 3, 2)
    checked = 0
    for t in range(14):
        today = start + t * DAY
        first = calendar.next_run(today)
        assert first.weekday() == THU and today <= first < today + 7 * DAY
        for d in range(-40, 91):
            due = today + d * DAY
            for k in [None, *range(-10, 41)]:
                by = None if k is None else today + k * DAY
                p = calendar.plan(first, due, by)
                assert p.run_date.weekday() == THU and p.run_date >= first
                if by is not None and calendar.last_run(by) >= first:
                    assert p.take_discount and p.run_date <= by < p.run_date + 7 * DAY
                elif calendar.last_run(due) >= first:
                    assert not p.take_discount and p.run_date <= due < p.run_date + 7 * DAY  # the last run before
                else:
                    assert p.why == "overdue" and p.run_date == first  # overdue or due before the next run
                checked += 1
    assert checked == 14 * 131 * 52


def test_due_dates_and_terms() -> None:
    today, inv_date = dt.date(2026, 3, 2), dt.date(2026, 2, 1)
    assert calendar.due_date(dt.date(2026, 3, 9), inv_date, "60 days", 30, today).source == "printed"
    assert calendar.due_date(None, inv_date, "Net 15", 30, today) == calendar.Due(dt.date(2026, 2, 16), "terms")
    assert calendar.due_date(None, inv_date, "Due on receipt", 30, today) == calendar.Due(inv_date, "terms")
    assert calendar.due_date(None, inv_date, "COD", None, today).date == inv_date
    assert calendar.due_date(None, inv_date, None, 14, today) == calendar.Due(dt.date(2026, 2, 15), "vendor_terms")
    assert calendar.due_date(None, None, None, None, today) == calendar.Due(today + 30 * DAY, "assumed")


# ---------------------------------------------------------------- booking and paying


def approve_auto(s: Session, wid: int, **kw) -> Invoice:
    """A clean invoice through checks and the rules: approved automatically, booked and planned."""
    inv, doc = make_invoice(s, wid, **kw)
    inv.conf_tier = "high"
    check_invoice(s, inv, doc, doc.received_at.date())
    rules.decide(s, inv)
    assert inv.state == "AUTO_APPROVED"
    return inv


def approve_manually(s: Session, wid: int, on: dt.date, *, credit_note: bool = False, amount: str | None = None,
                     **kw) -> tuple[Invoice, PaymentItem]:
    """An invoice (or credit note) of the workspace's vendor, approved by hand and booked on `on`."""
    inv, _ = make_invoice(s, wid, po=None, **kw)
    inv.vendor_id = s.scalars(select(Vendor.id).where(Vendor.workspace_id == wid)).one()
    if credit_note:
        inv.doc_type = "credit_note"
    if amount:
        inv.total = Decimal(amount)
    approval = Approval(invoice_id=inv.id, kind="manual", amount=inv.total, currency="ZAR", reasons=[], rules_ver="t")
    s.add(approval)
    s.flush()
    return inv, runs.approved(s, approval, on)


def lines(s: Session, inv: Invoice, kind: str = "invoice") -> dict[str, tuple[Decimal, Decimal]]:
    out: dict[str, tuple[Decimal, Decimal]] = {}
    for ln in s.scalars(select(JournalLine).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                        .where(JournalEntry.invoice_id == inv.id, JournalEntry.kind == kind)):
        d, c = out.get(ln.account_code, (Decimal(0), Decimal(0)))
        out[ln.account_code] = (d + ln.debit, c + ln.credit)
    return out


def item_of(s: Session, inv: Invoice) -> PaymentItem:
    return s.scalars(select(PaymentItem).where(PaymentItem.invoice_id == inv.id).order_by(PaymentItem.id)).first()


def books_ok(s: Session, wid: int) -> None:
    assert all(reports.balanced(reports.trial_balance(s, wid)).values())
    assert all(r.ok for r in reports.reconcile(s, wid)), reports.reconcile(s, wid)


def test_an_automatic_approval_is_booked_on_arrival_and_planned_before_its_due_date(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv = approve_auto(s, wid)  # 1,500.00 ZAR, invoice 15 Jan, arrived Tue 20 Jan, no terms anywhere
        entry = s.scalars(select(JournalEntry).where(JournalEntry.invoice_id == inv.id)).one()
        assert entry.entry_date == dt.date(2026, 1, 20) and entry.entity_id == inv.entity_id
        assert lines(s, inv) == {"5000": (Decimal("1500.00"), 0), "2000": (0, Decimal("1500.00"))}
        item = item_of(s, inv)
        assert (item.due_date, item.due_source) == (dt.date(2026, 2, 14), "assumed")
        assert s.get(PaymentRun, item.run_id).run_date == dt.date(2026, 2, 12)  # the Thursday before Sat 14 Feb
        assert "last run before then" in item.reason
        books_ok(s, wid)


def test_tax_and_withholding_go_to_their_own_accounts(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv, doc = make_invoice(s, wid, number="W1", po=None)
        ext = s.get(Extraction, inv.extraction_id)
        ext.raw = {**ext.raw, "invoices": [{**ext.raw["invoices"][0], "withholding_amount": 225.0}]}
        inv.tax, inv.total = Decimal("225.00"), Decimal("1725.00")
        approval = Approval(invoice_id=inv.id, kind="manual", amount=inv.total, currency="ZAR", reasons=[],
                            rules_ver="t")
        s.add(approval)
        s.flush()
        item = runs.approved(s, approval, dt.date(2026, 1, 21))
        assert lines(s, inv) == {"5000": (Decimal("1500.00"), 0), "1300": (Decimal("225.00"), 0),
                                 "2000": (0, Decimal("1500.00")), "2100": (0, Decimal("225.00"))}
        assert item.amount == Decimal("1500.00")  # what is paid: the total less withholding
        books_ok(s, wid)


def test_partial_approval_books_its_share_and_the_rest_books_what_is_left(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv, doc = make_invoice(s, wid, number="P1", qty=15)  # 15 boxes invoiced, 10 received
        inv.conf_tier = "high"
        check_invoice(s, inv, doc, doc.received_at.date())
        rules.decide(s, inv)
        inv.tax, inv.total = Decimal("300.00"), Decimal("2300.00")
        review.approve_partial(s, inv, "pay for the 10 boxes")
        assert lines(s, inv)["1300"] == (Decimal("225.00"), 0) and lines(s, inv)["2000"] == (0, Decimal("1725.00"))
        review.approve(s, inv, "the rest arrived")
        assert lines(s, inv)["1300"] == (Decimal("300.00"), 0) and lines(s, inv)["2000"] == (0, Decimal("2300.00"))
        assert len(s.scalars(select(PaymentItem).where(PaymentItem.invoice_id == inv.id)).all()) == 2
        books_ok(s, wid)


def test_a_run_pays_each_vendor_once_with_credit_notes_subtracted(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv = approve_auto(s, wid, number="R1")
        credit, cn_item = approve_manually(s, wid, dt.date(2026, 1, 21), credit_note=True, amount="200.00", number="CN1")
        assert lines(s, credit, "credit_note") == {"2000": (Decimal("200.00"), 0), "5000": (0, Decimal("200.00"))}
        assert cn_item.amount == Decimal("-200.00") and cn_item.run_id == item_of(s, inv).run_id  # netted there
        run = s.get(PaymentRun, cn_item.run_id)
        with pytest.raises(runs.PaymentError, match="today"):
            runs.execute(s, run, dt.date(2026, 2, 11))
        summary = runs.execute(s, run, dt.date(2026, 2, 12))
        assert [(t["amount"], t["to"]) for t in summary["transfers"]] == [("1300.00", "…7890")]
        assert item_of(s, inv).status == "paid" and cn_item.status == "paid"
        assert lines(s, inv, "payment") == {"2000": (Decimal("1500.00"), 0), "1000": (0, Decimal("1500.00"))}
        assert sum(r.balance for r in reports.trial_balance(s, wid) if r.account == "2000") == 0
        assert reports.payables(s, wid, dt.date(2026, 2, 12)) == []
        books_ok(s, wid)
        with pytest.raises(runs.PaymentError, match="already"):
            runs.execute(s, run, dt.date(2026, 2, 12))


def test_a_credit_note_waits_until_there_is_an_invoice_to_net_against(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        _, item = approve_manually(s, wid, dt.date(2026, 1, 21), credit_note=True, amount="200.00", number="CN2")
        assert item.status == "waiting" and item.run_id is None
        assert reports.payables(s, wid, dt.date(2026, 1, 21))[0].amount == Decimal("-200.00")
        approve_auto(s, wid, number="R2")
        assert item.status == "planned"  # attached to that invoice's run
        books_ok(s, wid)


def test_overdue_invoices_go_in_the_next_run_and_discounts_are_taken(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        vendor = s.scalars(select(Vendor).where(Vendor.workspace_id == wid)).one()
        vendor.terms_days = 7  # invoice 15 Jan + 7 = due 22 Jan; booked Tue 3 Feb (late): next run Thu 5 Feb
        _, item = approve_manually(s, wid, dt.date(2026, 2, 3), number="O1")
        assert s.get(PaymentRun, item.run_id).run_date == dt.date(2026, 2, 5) and "already passed" in item.reason
        vendor.terms_days, vendor.discount_pct, vendor.discount_days = 30, Decimal("2.00"), 10
        inv2, item = approve_manually(s, wid, dt.date(2026, 1, 20), number="D1", date="2026-01-19")  # 2% by 29 Jan
        assert s.get(PaymentRun, item.run_id).run_date == dt.date(2026, 1, 29) and item.discount == Decimal("30.00")
        runs.execute(s, s.get(PaymentRun, item.run_id), dt.date(2026, 1, 29))
        assert lines(s, inv2, "payment") == {"2000": (Decimal("1500.00"), 0), "1000": (0, Decimal("1470.00")),
                                             "4900": (0, Decimal("30.00"))}
        books_ok(s, wid)


# ---------------------------------------------------------------- payees (decision 41)


def test_overriding_a_bank_change_pays_the_new_account(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv, doc = make_invoice(s, wid, number="B1", bank="9998887776")
        inv.conf_tier = "high"
        check_invoice(s, inv, doc, doc.received_at.date())
        rules.decide(s, inv)
        assert "bank details changed" in inv.review_labels
        review.approve(s, inv, "Confirmed by phone with the number on file", override=True)
        accounts = s.scalars(select(VendorBankAccount).where(VendorBankAccount.vendor_id == inv.vendor_id)
                             .order_by(VendorBankAccount.id)).all()
        assert [(a.account_norm, a.valid_to is None) for a in accounts] == [("1234567890", False), ("9998887776", True)]
        item = item_of(s, inv)
        runs.execute(s, s.get(PaymentRun, item.run_id), s.get(PaymentRun, item.run_id).run_date)
        assert s.get(VendorBankAccount, item.bank_account_id).account_norm == "9998887776"
        later, ldoc = make_invoice(s, wid, number="B2", bank="1234567890")  # the old account again: changed
        check_invoice(s, later, ldoc, ldoc.received_at.date())
        assert any(c.code == "bank.changed" and c.outcome == "fail" for c in
                   s.scalars(select(CheckResult).where(CheckResult.invoice_id == later.id)))


def test_approving_an_unknown_vendor_adds_it(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        inv, doc = make_invoice(s, wid, number="U1", vendor="Brand New Traders CC", tax_id="4999999999",
                                bank="5550001111", po=None)
        check_invoice(s, inv, doc, doc.received_at.date())
        rules.decide(s, inv)
        assert "unknown vendor" in inv.review_labels and inv.vendor_id is None
        approval = review.approve(s, inv, "New supplier, onboarded by procurement")
        vendor = s.get(Vendor, inv.vendor_id)
        assert (vendor.name, vendor.origin, vendor.code) == ("Brand New Traders CC", "review", "R0001")
        assert runs.open_account(s, vendor.id).account_norm == "5550001111"
        assert any(r["kind"] == "payee" for r in approval.reasons) and item_of(s, inv).status == "planned"


# ---------------------------------------------------------------- today


def test_today_is_settable_but_not_back_before_a_paid_run(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        w = s.get(Workspace, wid)
        inv = approve_auto(s, wid, number="T1")
        assert clock.today(s, w) == clock.Today(dt.date(2026, 1, 20), False)  # the last arrival
        result = runs.next_run(s, w)
        assert result["run_date"] == "2026-02-12" and w.as_of_date == dt.date(2026, 2, 12)
        with pytest.raises(runs.PaymentError, match="can't go back"):
            runs.set_today(s, w, dt.date(2026, 2, 1))
        runs.set_today(s, w, dt.date(2026, 3, 1))
        assert item_of(s, inv).status == "paid"
        w.settings = {"clock": {"start_real": dt.datetime.now(dt.timezone.utc).isoformat(), "start_sim": "2026-04-01",
                                "seconds_per_day": 2}}
        s.add(Run(workspace_id=wid, mode="inbox", status="running", config={}))
        s.flush()
        assert clock.today(s, w) == clock.Today(dt.date(2026, 4, 1), True)
        with pytest.raises(runs.PaymentError, match="simulated clock"):
            runs.set_today(s, w, dt.date(2026, 5, 1))


# ---------------------------------------------------------------- the audit chain


def test_the_audit_check_finds_an_edited_or_deleted_event(ws) -> None:
    sessions, wid = ws
    with sessions.begin() as s:
        for k in range(5):
            chain.record(s, wid, "system", "test.event", "workspace", wid, {"k": k, "amount": "1.10"})
    with sessions() as s:
        good = verify(s, wid)
        assert good.ok and good.events >= 5
        last = good.events
    with sessions.begin() as s:  # tamper the way someone with database access could: switch the guard off
        s.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only"))
        s.execute(text("UPDATE audit_events SET data = '{\"k\": 99, \"amount\": \"1.10\"}' "
                       "WHERE workspace_id = :w AND seq = :q"), {"w": wid, "q": last - 2})
        s.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only"))
    with sessions() as s:
        bad = verify(s, wid)
        assert not bad.ok and bad.broken_at == last - 2 and "changed" in bad.problem
    with sessions.begin() as s:
        s.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only"))
        s.execute(text("DELETE FROM audit_events WHERE workspace_id = :w AND seq <= :q"), {"w": wid, "q": last - 2})
        s.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only"))
    with sessions() as s:
        assert verify(s, wid).broken_at == last - 1  # the first remaining event is missing its predecessor


def test_money_api(ws) -> None:
    from fastapi.testclient import TestClient

    from invoice_analyst.api import main

    sessions, wid = ws
    with sessions.begin() as s:
        inv = approve_auto(s, wid, number="A1")
        doc_id = inv.document_id

    def session():
        with sessions() as s:
            yield s

    main.app.dependency_overrides[main.db] = session
    try:
        api = TestClient(main.app)
        pay = api.get(f"/api/workspaces/{wid}/payments").json()
        assert pay["today"] == {"date": "2026-01-20", "live": False} and pay["due_now"] == 0
        run = pay["runs"][0]
        assert (run["run_date"], run["status"], run["totals"]) == ("2026-02-12", "planned", {"ZAR": "1500.0000"})
        detail = api.get(f"/api/payment-runs/{run['id']}").json()
        assert detail["transfers"][0]["to_account"] == "…7890" and "last run before" in detail["transfers"][0]["items"][0]["reason"]
        assert api.post(f"/api/payment-runs/{run['id']}/execute").status_code == 409  # not until 12 Feb
        done = api.post(f"/api/workspaces/{wid}/payments/next").json()
        assert done["executed"] == ["2026-02-12"] and done["today"]["date"] == "2026-02-12"
        tb = api.get(f"/api/workspaces/{wid}/trial-balance").json()
        assert tb["balanced"] == {"ZAR": True} and all(r["ok"] for r in tb["reconciled"])
        assert {r["account"]: r["balance"] for r in tb["rows"]} == {"1000": "-1500.0000", "2000": "0.0000",
                                                                    "5000": "1500.0000"}
        assert api.get(f"/api/workspaces/{wid}/payables").json()["items"] == []
        journal = api.get(f"/api/workspaces/{wid}/journal").json()
        assert journal["total"] == 2 and [e["kind"] for e in journal["entries"]] == ["payment", "invoice"]
        csv = api.get(f"/api/workspaces/{wid}/export/journal.csv")
        assert csv.status_code == 200 and csv.text.count("\n") == 5  # header + 4 lines
        document = api.get(f"/api/documents/{doc_id}").json()["invoices"][0]
        assert [p["status"] for p in document["payments"]] == ["paid"] and len(document["entries"]) == 2
        assert api.get(f"/api/workspaces/{wid}/audit/verify").json()["ok"] is True
        assert "payment_run.executed" in api.get(f"/api/workspaces/{wid}/audit").json()["actions"]
        assert api.put(f"/api/workspaces/{wid}/today", json={"date": "2026-01-01"}).status_code == 409
    finally:
        main.app.dependency_overrides.clear()


def test_a_live_inbox_pays_its_runs_as_the_clock_passes_them(ws, monkeypatch) -> None:
    from invoice_analyst.worker import sequencer

    sessions, wid = ws
    monkeypatch.setattr(sequencer, "session_factory", lambda: sessions)
    with sessions.begin() as s:
        inv = approve_auto(s, wid, number="L1", feed_seq=1)  # planned for Thu 12 Feb 2026
        run_id = item_of(s, inv).run_id
        # the simulated clock started at 1 Feb 2026, one day per second, 30 s ago: it is now about 3 Mar
        start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)
        s.get(Workspace, wid).settings = {"clock": {"start_real": start.isoformat(), "start_sim": "2026-02-01",
                                                    "seconds_per_day": 1}}
        s.add(Run(workspace_id=wid, mode="inbox", status="running", config={"documents": 2}))  # 1 of 2 arrived
    sequencer.advance(wid)
    with sessions() as s:
        assert s.get(PaymentRun, run_id).status == "executed" and item_of(s, s.get(Invoice, inv.id)).status == "paid"
        assert s.get(Workspace, wid).as_of_date is None  # still live: the run hasn't finished


def test_when_a_live_inbox_finishes_today_stops_where_the_clock_was(ws, monkeypatch) -> None:
    from invoice_analyst.worker import sequencer

    sessions, wid = ws
    monkeypatch.setattr(sequencer, "session_factory", lambda: sessions)
    monkeypatch.setattr(sequencer, "write_results", lambda run_id: None)  # don't touch data/runs from a test
    with sessions.begin() as s:
        make_invoice(s, wid, number="F1", feed_seq=1)
        start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=10)
        s.get(Workspace, wid).settings = {"clock": {"start_real": start.isoformat(), "start_sim": "2026-02-01",
                                                    "seconds_per_day": 1}}
        s.add(Run(workspace_id=wid, mode="inbox", status="running", config={"documents": 1}))
    sequencer.advance(wid)
    with sessions() as s:
        w = s.get(Workspace, wid)
        assert dt.date(2026, 2, 10) <= w.as_of_date <= dt.date(2026, 2, 13)  # about 10 simulated days in
        assert clock.today(s, w).live is False
