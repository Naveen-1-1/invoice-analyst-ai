"""Inbox mode (batch version): documents arrive one after another into one workspace with its companion data.

Extraction runs in parallel (answers come from the cache when seen before); the checks then run strictly in
arrival order, because duplicate and three-way-match checks depend on what arrived earlier. M5 moves this onto the
job queue with live updates; the order rule stays the same.

Writes data/runs/<run>/ap_results.json for bench/'s AP evaluation (no labels involved here).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from ..bench.runner import DOC_CONCURRENCY, RUNS_DIR, _git_sha, extract_waiting
from ..companion import loader
from ..config import ROOT, Settings
from ..db.engine import session_factory
from ..checks.engine import check_invoice
from ..db.models import Document, Entity, Invoice, PurchaseOrder, Run, Vendor, Workspace
from ..extraction import prompts
from ..llm.client import LlmClient


@dataclass
class InboxSummary:
    run_id: int
    out_dir: Path
    documents: int
    failed: list[str]
    new_calls: int
    raised: dict[str, int]


async def run(settings: Settings, set_name: str, *, label: str | None = None, use_cache: bool = True,
              limit: int | None = None) -> InboxSummary:
    sessions = session_factory()
    stream = loader.stream(set_name)[:limit] if limit else loader.stream(set_name)
    stamp = dt.datetime.now(dt.timezone.utc)
    with sessions.begin() as s:
        ws = Workspace(name=f"inbox-{set_name}-{stamp:%Y%m%dT%H%M%S}-{label or 'run'}", kind="inbox",
                       dataset=set_name, settings={})
        s.add(ws)
        s.flush()
        run_row = Run(workspace_id=ws.id, mode="inbox", git_sha=_git_sha(), status="running",
                      config={"set": set_name, "label": label, "documents": len(stream),
                              "prompt_hash": prompts.PROMPT_HASH, "use_cache": use_cache})
        s.add(run_row)
        s.flush()
        ws_id, run_id = ws.id, run_row.id
        loaded = loader.load(s, ws_id, set_name)
    out_dir = RUNS_DIR / f"{run_id:04d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Inbox run {run_id}: {len(stream)} documents; loaded {loaded}", flush=True)

    # 1. read every document (in parallel)
    llm = LlmClient(settings, sessions)
    gate = asyncio.Semaphore(DOC_CONCURRENCY)

    def received(item: dict) -> dt.datetime:
        return dt.datetime.combine(dt.date.fromisoformat(item["received_date"]), dt.time(9), dt.timezone.utc)

    tasks = [asyncio.create_task(extract_waiting(ROOT / item["path"], ws_id=ws_id, run_id=run_id, settings=settings,
                                                 llm=llm, use_cache=use_cache, gate=gate, ref=item["ref"],
                                                 feed_seq=item["seq"], received_at=received(item)))
             for item in stream]
    outcomes = {}
    try:
        for fut in asyncio.as_completed(tasks):
            path, outcome = await fut
            outcomes[path] = outcome
            if outcome.error:
                print(f"  extraction failed: {path.name}: {outcome.error[:100]}", flush=True)
    finally:
        await llm.aclose()
    new_calls = sum(1 for o in outcomes.values() if not o.cache_hit and not o.error)
    print(f"Read {len(outcomes)} documents ({new_calls} new model calls); checking in arrival order", flush=True)

    # 2. check, strictly in arrival order (each document committed before the next is checked)
    results = []
    raised: dict[str, int] = {}
    for item in stream:
        with sessions.begin() as s:
            doc = s.scalars(select(Document).where(Document.workspace_id == ws_id,
                                                   Document.feed_seq == item["seq"])).one()
            invoices = s.scalars(select(Invoice).where(Invoice.document_id == doc.id)
                                 .order_by(Invoice.child_index)).all()
            entry = {"seq": item["seq"], "ref": item["ref"], "document_state": doc.state, "invoices": []}
            for inv in invoices:
                findings = check_invoice(s, inv, doc, received(item).date())
                vendor = s.get(Vendor, inv.vendor_id) if inv.vendor_id else None
                entity = s.get(Entity, inv.entity_id) if inv.entity_id else None
                po = s.get(PurchaseOrder, inv.po_id) if inv.po_id else None
                entry["invoices"].append({
                    "child_index": inv.child_index, "doc_type": inv.doc_type, "conf_tier": inv.conf_tier,
                    "vendor_code": vendor.code if vendor else None, "entity": entity.name if entity else None,
                    "po_number": po.po_number if po else None, "invoice_number": inv.invoice_number,
                    "total": float(inv.total) if inv.total is not None else None,
                    "checks": [{"code": f.code, "outcome": f.outcome, "severity": f.severity} for f in findings]})
                for f in findings:
                    if f.outcome in ("fail", "warn"):
                        raised[f.code] = raised.get(f.code, 0) + 1
            results.append(entry)
    (out_dir / "ap_results.json").write_text(json.dumps({"run": run_id, "set": set_name, "items": results},
                                                        indent=1) + "\n")
    failed = sorted(p.name for p, o in outcomes.items() if o.error)
    with sessions.begin() as s:
        r = s.get(Run, run_id)
        r.status, r.finished_at = "finished", dt.datetime.now(dt.timezone.utc)
        r.scores = {"raised": raised}
    return InboxSummary(run_id, out_dir, len(stream), failed, new_calls, dict(sorted(raised.items())))
