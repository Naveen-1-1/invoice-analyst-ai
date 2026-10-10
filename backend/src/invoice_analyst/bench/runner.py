"""Benchmark mode: every document processed on its own in a fresh workspace, written as predictions.json for the
official scorer (which runs in bench/, never here)."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import rfc8785

from ..config import ROOT, Settings
from ..db.engine import session_factory
from ..db.models import Run, Workspace
from ..extraction import prompts
from ..extraction.extract import IMAGE_PARAMS, TEXT_PARAMS, Outcome, extract_document
from ..llm.client import CooldownError, LlmClient
from .predictions import flag_reasons, to_prediction

DATASETS = {"dev": ROOT / "third_party" / "invoice-extraction-benchmark" / "corpus" / "v1" / "documents"}
RUNS_DIR = ROOT / "data" / "runs"
DOC_CONCURRENCY = 8  # documents prepared/in progress at once; the LLM client caps calls in flight separately
MAX_COOLDOWN_WAITS = 3


@dataclass
class RunSummary:
    run_id: int
    out_dir: Path
    documents: int
    failed: list[str]
    cache_hits: int
    new_calls: int
    predictions_sha256: str


def select_documents(dataset: str, *, smoke: bool = False, only: set[str] | None = None,
                     limit: int | None = None) -> list[Path]:
    if dataset not in DATASETS:
        raise SystemExit(f"unknown dataset {dataset!r}; benchmark mode runs on: {', '.join(DATASETS)}")
    docs = sorted(p for p in DATASETS[dataset].iterdir() if p.is_file() and not p.name.startswith("."))
    if only:
        docs = [p for p in docs if p.stem in only]
        missing = only - {p.stem for p in docs}
        if missing:
            raise SystemExit(f"not in {dataset}: {', '.join(sorted(missing))}")
    if smoke:  # two documents from every category (the doc id starts with its category)
        per: dict[str, list[Path]] = {}
        for p in docs:
            per.setdefault(p.stem.split("-", 1)[0], []).append(p)
        docs = [p for group in per.values() for p in group[:2]]
    return docs[:limit] if limit else docs


def _git_sha() -> str:
    out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True).stdout.strip()
    return out.stdout.strip() + ("-dirty" if dirty else "")


async def _one(path: Path, *, ws_id: int, run_id: int, settings: Settings, llm: LlmClient, use_cache: bool,
               overrides: dict | None, ocr_hint: bool, gate: asyncio.Semaphore) -> tuple[Path, Outcome]:
    async with gate:
        for _ in range(MAX_COOLDOWN_WAITS + 1):
            try:
                return path, await extract_document(path=path, workspace_id=ws_id, run_id=run_id,
                                                    settings=settings, sessions=session_factory(), llm=llm,
                                                    bench_doc_id=path.stem, use_cache=use_cache, overrides=overrides,
                                                    ocr_hint=ocr_hint)
            except CooldownError as exc:
                wait = max(1.0, (exc.until - dt.datetime.now(dt.timezone.utc)).total_seconds())
                print(f"  {path.stem}: LLM paused until {exc.until:%H:%M:%S} UTC, waiting {wait:.0f}s", flush=True)
                await asyncio.sleep(wait)
            except Exception as exc:  # not an extraction failure (those are recorded inside): report, keep going
                return path, Outcome(-1, "?", "?", None, error=f"{type(exc).__name__}: {exc}"[:2000])
        return path, Outcome(-1, "?", "?", None, error=f"still rate-limited after {MAX_COOLDOWN_WAITS} cooldowns")


async def run(settings: Settings, dataset: str, docs: list[Path], *, use_cache: bool = True,
              overrides: dict | None = None, label: str | None = None, ocr_hint: bool = False) -> RunSummary:
    sessions = session_factory()
    stamp = dt.datetime.now(dt.timezone.utc)
    config = {"dataset": dataset, "documents": [p.stem for p in docs], "label": label, "use_cache": use_cache,
              "models": {"text": settings.text_model, "image": settings.image_model},
              "params": {"text": {**TEXT_PARAMS, **(overrides or {})}, "image": {**IMAGE_PARAMS, **(overrides or {})}},
              "prompt_hash": prompts.PROMPT_HASH, "llm_rpm": settings.llm_rpm, "ocr_hint": ocr_hint}
    with sessions.begin() as s:
        ws = Workspace(name=f"benchmark-{dataset}-{stamp:%Y%m%dT%H%M%S}-{label or 'run'}", kind="benchmark",
                       dataset=dataset, settings={})
        s.add(ws)
        s.flush()
        run_row = Run(workspace_id=ws.id, mode="benchmark", git_sha=_git_sha(), config=config, status="running")
        s.add(run_row)
        s.flush()
        ws_id, run_id = ws.id, run_row.id
    out_dir = RUNS_DIR / f"{run_id:04d}"
    out_dir.mkdir(parents=True, exist_ok=True)

    llm = LlmClient(settings, sessions)
    gate = asyncio.Semaphore(DOC_CONCURRENCY)
    tasks = [asyncio.create_task(_one(p, ws_id=ws_id, run_id=run_id, settings=settings, llm=llm,
                                      use_cache=use_cache, overrides=overrides, ocr_hint=ocr_hint, gate=gate))
             for p in docs]
    outcomes: dict[str, Outcome] = {}
    done = 0
    try:
        for fut in asyncio.as_completed(tasks):
            path, outcome = await fut
            outcomes[path.stem] = outcome
            done += 1
            status = "error: " + outcome.error[:80] if outcome.error else ("cache" if outcome.cache_hit else "new")
            if "partial" in outcome.notes:
                status += " (partial: still incomplete after the retry)"
            flags = ",".join(flag_reasons(outcome.extraction)) if outcome.extraction else ""
            print(f"[{done:3d}/{len(docs)}] {path.stem:<44} {outcome.route:<9} {outcome.seconds:6.1f}s "
                  f"{status}{'  flag: ' + flags if flags else ''}", flush=True)
    finally:
        await llm.aclose()

    # A failed document is written as an empty prediction so the scorer counts it as wrong, not as skipped
    predictions = {doc_id: to_prediction(o.extraction) if o.extraction else {}
                   for doc_id, o in sorted(outcomes.items())}
    pred_sha = hashlib.sha256(rfc8785.dumps(predictions)).hexdigest()
    (out_dir / "predictions.json").write_text(json.dumps({
        "system": f"invoice-analyst-ai {config['models']['text']} + {config['models']['image']}",
        "run_date": stamp.date().isoformat(),
        "notes": f"run {run_id}, prompt {prompts.PROMPT_HASH}, git {_git_sha()}",
        "documents": predictions}, indent=1, ensure_ascii=False) + "\n")
    failed = sorted(k for k, o in outcomes.items() if o.error)
    hits = sum(o.cache_hit for o in outcomes.values())
    counts = Counter(o.route for o in outcomes.values())
    summary = {"run_id": run_id, "workspace_id": ws_id, "documents": len(docs), "routes": counts,
               "failed": failed, "cache_hits": hits, "new_extractions": len(outcomes) - hits - len(failed),
               "predictions_sha256": pred_sha, "seconds": round((dt.datetime.now(dt.timezone.utc) - stamp)
                                                                  .total_seconds(), 1)}
    (out_dir / "run.json").write_text(json.dumps({**summary, "config": config}, indent=1) + "\n")
    with sessions.begin() as s:
        r = s.get(Run, run_id)
        r.status = "failed" if failed and len(failed) == len(docs) else "finished"
        r.finished_at = dt.datetime.now(dt.timezone.utc)
    return RunSummary(run_id, out_dir, len(docs), failed, hits, len(outcomes) - hits - len(failed), pred_sha)
