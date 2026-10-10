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


def official(run: str, corpus: str = "dev") -> Path:
    """Score with scorer.evaluate; writes scores.json and scores.md next to the predictions. Returns the run dir."""
    if corpus not in CORPORA:
        raise SystemExit(f"unknown corpus {corpus!r} (held-out scoring arrives with the final evaluation)")
    out = run_dir(run)
    preds = out / "predictions.json"
    corpus_dir = CORPORA[corpus]
    n_pred = len(json.loads(preds.read_text())["documents"])
    n_corpus = len(json.loads((corpus_dir / "manifest.json").read_text())["documents"])
    cmd = [sys.executable, "-m", "scorer.evaluate", str(preds.resolve()), "--corpus", str(corpus_dir.resolve()),
           "--out", str((out / "scores.json").resolve()), "--markdown", str((out / "scores.md").resolve())]
    if n_pred < n_corpus:  # a subset run (smoke test); failed documents are in the file as empty predictions
        cmd.append("--allow-missing")
    subprocess.run(cmd, cwd=specs.BENCH_DIR, check=True, stdout=subprocess.DEVNULL)
    return out
