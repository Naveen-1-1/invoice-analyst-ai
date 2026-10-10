"""The web API on a small document built in the test database."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from invoice_analyst.api import main
from invoice_analyst.config import settings
from invoice_analyst.db.models import Document, FieldValue, Invoice, Page, Run, Workspace


@pytest.fixture
def client(engine: Engine, tmp_path, monkeypatch):
    sessions = sessionmaker(engine, expire_on_commit=False)
    storage = tmp_path / "storage"
    (storage / "pages").mkdir(parents=True)
    image = storage / "pages" / "1.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(settings(), "storage_dir", storage)
    with sessions.begin() as s:
        ws = Workspace(name=f"api-test-{dt.datetime.now().timestamp()}", kind="benchmark", dataset="dev", settings={})
        s.add(ws)
        s.flush()
        run = Run(workspace_id=ws.id, mode="benchmark", config={"label": "t"}, status="finished")
        s.add(run)
        s.flush()
        doc = Document(workspace_id=ws.id, run_id=run.id, sha256="x", filename="inv.pdf", mime="application/pdf",
                       bytes=10, page_count=1, route="text_llm", bench_doc_id="clean-x", state="EXTRACTED",
                       received_at=dt.datetime.now(dt.timezone.utc), storage_path=str(image))
        s.add(doc)
        s.flush()
        s.add(Page(document_id=doc.id, page_no=1, width=595, height=842, unit="pt", image_path=str(image),
                   words=[], quality={}))
        inv = Invoice(workspace_id=ws.id, document_id=doc.id, child_index=0, doc_type="invoice", state="EXTRACTED",
                      invoice_number="INV-1", total=Decimal("115.00"), currency="USD", conf_tier="high")
        s.add(inv)
        s.flush()
        s.add(FieldValue(invoice_id=inv.id, field="total", raw_text="$115.00", value=115.0, page_no=1,
                         bbox=[0.8, 0.5, 0.9, 0.51], evidence_text="$115.00", grounding="exact", source="pdf_text",
                         tier="high", confidence=1.0,
                         reasons=[{"code": "found_text_layer", "text": "Found", "effect": "+"}]))
        ids = {"run": run.id, "doc": doc.id, "workspace": ws.id}
    app = main.app
    app.dependency_overrides[main.db] = lambda: (yield from _session(sessions))
    yield TestClient(app), ids
    app.dependency_overrides.clear()


def _session(sessions):
    with sessions() as s:
        yield s


def test_runs_documents_and_detail(client) -> None:
    http, ids = client
    run = next(r for r in http.get("/api/runs").json() if r["id"] == ids["run"])
    assert run["documents"] == 1 and run["tiers"] == {"high": 1}
    rows = http.get(f"/api/runs/{ids['run']}/documents").json()
    assert rows[0]["bench_doc_id"] == "clean-x" and rows[0]["tier"] == "high"
    detail = http.get(f"/api/documents/{ids['doc']}").json()
    field = detail["invoices"][0]["fields"][0]
    assert field["bbox"] == pytest.approx([0.8, 0.5, 0.9, 0.51]) and field["reasons"][0]["effect"] == "+"
    assert http.get(detail["pages"][0]["image_url"]).status_code == 200


def test_files_outside_storage_are_not_served(client, engine: Engine) -> None:
    http, ids = client
    with sessionmaker(engine).begin() as s:
        s.get(Document, ids["doc"]).storage_path = "/etc/hosts"
    assert http.get(f"/api/documents/{ids['doc']}/file").status_code == 404


def test_board_groups_a_workspace_by_state(client) -> None:
    http, ids = client
    board = http.get(f"/api/workspaces/{ids['workspace']}/board").json()
    card = board["cards"][0]
    assert card["document_id"] == ids["doc"] and card["state"] == "EXTRACTED" and card["tier"] == "high"
    assert board["worker_online"] in (True, False) and board["clock"] is None
