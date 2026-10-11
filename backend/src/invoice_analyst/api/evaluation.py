"""The Evaluation page's data: the entries bench/ logs in data/eval/EVAL_LOG.jsonl (dev reference runs and the
final held-out run). Scores only, computed in bench/; nothing here reads answer keys."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
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
