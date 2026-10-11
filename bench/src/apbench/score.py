"""Run the benchmark's official scorer, unchanged, on a run's predictions.json."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import specs

RUNS_DIR = specs.ROOT / "data" / "runs"
CORPORA = {"dev": specs.DEV_CORPUS}


def run_dir(run: str) -> Path:
    path = Path(run)
    if path.is_dir():
        return path
    if run.isdigit():
        return RUNS_DIR / f"{int(run):04d}"
    raise SystemExit(f"no such run: {run}")


def official(run: str, corpus: str = "dev", *, final: bool = False) -> Path:
    """Score with scorer.evaluate; writes scores.json and scores.md next to the predictions. Returns the run dir.
    The held-out answers are used only by the final run (decision 47)."""
    if corpus == "heldout" and final:
        from .heldout import CORPUS_DIR  # noqa: PLC0415
        corpus_dir = CORPUS_DIR
    elif corpus in CORPORA:
        corpus_dir = CORPORA[corpus]
    else:
        raise SystemExit(f"unknown corpus {corpus!r}" + (" (held-out: only the final run, make final)"
                                                         if corpus == "heldout" else ""))
    out = run_dir(run)
    preds = out / "predictions.json"
    n_pred = len(json.loads(preds.read_text())["documents"])
    n_corpus = len(json.loads((corpus_dir / "manifest.json").read_text())["documents"])
    cmd = [sys.executable, "-m", "scorer.evaluate", str(preds.resolve()), "--corpus", str(corpus_dir.resolve()),
           "--out", str((out / "scores.json").resolve()), "--markdown", str((out / "scores.md").resolve())]
    if n_pred < n_corpus:  # a subset run (smoke test); failed documents are in the file as empty predictions
        cmd.append("--allow-missing")
    subprocess.run(cmd, cwd=specs.BENCH_DIR, check=True, stdout=subprocess.DEVNULL)
    write_scorecard(str(out), corpus)
    return out


def write_scorecard(run: str, corpus: str) -> Path:
    """Which fields each document got right, without the expected values: what the app's run comparison reads (the
    backend never sees answers, so it gets this instead of scores.json)."""
    out = run_dir(run)
    scores = json.loads((out / "scores.json").read_text())
    card = {"run": out.name, "corpus": corpus, "documents": {
        d["doc_id"]: {"category": d.get("category"), "fully_correct": d.get("fully_correct"),
                      "fields": {f: r["correct"] for f, r in d["fields"].items()}} for d in scores["documents"]}}
    path = out / "scorecard.json"
    path.write_text(json.dumps(card) + "\n")
    return path
