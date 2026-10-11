"""The Evaluation page's data: the entries bench/ logs in data/eval/EVAL_LOG.jsonl (dev reference runs and the
final held-out run). Scores only, computed in bench/; nothing here reads answer keys."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ROOT
from ..db.models import Document, Run
from . import schemas as out
from .deps import db

router = APIRouter()
LOG = ROOT / "data" / "eval" / "EVAL_LOG.jsonl"


@router.get("/api/evaluation", response_model=list[out.EvalEntryOut], name="listEvaluations")
def list_evaluations(s: Session = Depends(db)) -> list[out.EvalEntryOut]:
    if not LOG.exists():
        return []
    rows = []
    for line in LOG.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        run = s.get(Run, int(e["bench_run"]))
        docs = dict(s.execute(select(Document.bench_doc_id, Document.id).where(
            Document.workspace_id == run.workspace_id)).all()) if run else {}
        rows.append(out.EvalEntryOut(**{k: e.get(k) for k in out.EvalEntryOut.model_fields if k != "documents"},
                                     documents={k: v for k, v in docs.items() if k}))
    return rows


RUNS = ROOT / "data" / "runs"


class RunCardOut(out.BaseModel):
    run: str
    corpus: str
    label: str | None
    documents: int
    fully_correct: int


class FieldDiffOut(out.BaseModel):
    field: str
    a_right: int
    a_scored: int
    b_right: int
    b_scored: int


class DocChangeOut(out.BaseModel):
    doc_id: str
    category: str | None
    gained: list[str]  # fields right in b, wrong in a
    lost: list[str]  # right in a, wrong in b
    a_document_id: int | None
    b_document_id: int | None


class CompareOut(out.BaseModel):
    a: RunCardOut
    b: RunCardOut
    fields: list[FieldDiffOut]
    changes: list[DocChangeOut]


def _card(run: str) -> dict:
    path = RUNS / f"{int(run):04d}" / "scorecard.json"
    if not path.exists():
        raise HTTPException(404, f"run {run} has no scorecard (score it first)")
    return json.loads(path.read_text())


def _run_out(card: dict) -> RunCardOut:
    meta_path = RUNS / card["run"] / "run.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    docs = card["documents"]
    return RunCardOut(run=card["run"], corpus=card["corpus"], label=(meta.get("config") or {}).get("label"),
                      documents=len(docs), fully_correct=sum(1 for d in docs.values() if d["fully_correct"]))


@router.get("/api/evaluation/runs", response_model=list[RunCardOut], name="listScoredRuns")
def list_scored_runs() -> list[RunCardOut]:
    return [_run_out(json.loads(p.read_text())) for p in sorted(RUNS.glob("[0-9]*/scorecard.json"))]


def _doc_ids(s: Session, run: str) -> dict[str, int]:
    r = s.get(Run, int(run))
    if r is None:
        return {}
    return {k: v for k, v in s.execute(select(Document.bench_doc_id, Document.id)
                                       .where(Document.workspace_id == r.workspace_id)).all() if k}


@router.get("/api/evaluation/compare", response_model=CompareOut, name="compareRuns")
def compare_runs(a: str, b: str, s: Session = Depends(db)) -> CompareOut:
    """Field by field, and the documents whose fields changed, between two scored runs on the same set."""
    ca, cb = _card(a), _card(b)
    if ca["corpus"] != cb["corpus"]:
        raise HTTPException(409, f"run {a} is on {ca['corpus']} and run {b} on {cb['corpus']}: compare runs on one set")
    da, db_ = ca["documents"], cb["documents"]
    fields = sorted({f for d in (*da.values(), *db_.values()) for f in d["fields"]})
    diffs = []
    for f in fields:
        ra = [d["fields"][f] for d in da.values() if f in d["fields"]]
        rb = [d["fields"][f] for d in db_.values() if f in d["fields"]]
        diffs.append(FieldDiffOut(field=f, a_right=sum(ra), a_scored=len(ra), b_right=sum(rb), b_scored=len(rb)))
    ids_a, ids_b = _doc_ids(s, a), _doc_ids(s, b)
    changes = []
    for doc_id in sorted(set(da) & set(db_)):
        fa, fb = da[doc_id]["fields"], db_[doc_id]["fields"]
        gained = sorted(f for f in fb if fb[f] and f in fa and not fa[f])
        lost = sorted(f for f in fa if fa[f] and f in fb and not fb[f])
        if gained or lost:
            changes.append(DocChangeOut(doc_id=doc_id, category=da[doc_id].get("category"), gained=gained, lost=lost,
                                        a_document_id=ids_a.get(doc_id), b_document_id=ids_b.get(doc_id)))
    return CompareOut(a=_run_out(ca), b=_run_out(cb), fields=diffs, changes=changes)
