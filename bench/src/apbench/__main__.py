"""Command line: python -m apbench <command>."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from . import compare, heldout, score, specs

app = typer.Typer(add_completion=False, no_args_is_help=True)
heldout_app = typer.Typer(add_completion=False, no_args_is_help=True,
                          help="The sealed held-out set (generated once from HELDOUT_SEED).")
app.add_typer(heldout_app, name="heldout")


@heldout_app.command("generate")
def heldout_generate() -> None:
    """Render the held-out corpus from HELDOUT_SEED and seal it with LOCK.json."""
    summary = heldout.generate()
    typer.echo(f"Generated and sealed {summary['documents']} documents ({summary['files']} files) "
               f"in {heldout.CORPUS_DIR}; seal: {heldout.LOCK_PATH}")


@heldout_app.command("verify")
def heldout_verify() -> None:
    """Check every sealed file and the seed fingerprint, without showing any contents."""
    problems = heldout.verify()
    if problems:
        for p in problems:
            typer.echo(p)
        raise typer.Exit(1)
    typer.echo("OK: held-out set intact and HELDOUT_SEED matches the seal")


@app.command("verify-specs")
def verify_specs(seed: int = specs.DEV_SEED, corpus: Path = specs.DEV_CORPUS) -> None:
    """Check that the generator's specs at SEED reproduce CORPUS/manifest.json exactly."""
    problems = specs.verify(seed, corpus)
    count = len(specs.load(seed))
    if problems:
        for p in problems:
            typer.echo(p)
        typer.echo(f"FAILED: {len(problems)} problem(s) across {count} specs")
        raise typer.Exit(1)
    typer.echo(f"OK: {count}/{count} specs match {corpus / 'manifest.json'}")


@app.command("score")
def score_run(run: str = typer.Argument(..., help="Run number (e.g. 3) or run directory"),
              corpus: str = typer.Option("dev", help="Which answer keys to score against")) -> None:
    """Score a run's predictions.json with the benchmark's official scorer (writes scores.json / scores.md)."""
    out = score.official(run, corpus)
    typer.echo((out / "scores.md").read_text())


@app.command("compare")
def compare_runs(a: str = typer.Argument(..., help="Baseline run number or directory"),
                 b: str = typer.Argument(..., help="Run to compare against it")) -> None:
    """Side-by-side scores of two runs on the documents both contain, and which documents changed."""
    typer.echo(compare.compare(a, b))


@app.command("hardest")
def hardest(n: int = typer.Option(20, help="How many documents"),
            categories: str | None = typer.Option(None, help="Only these categories, comma-separated"),
            out: Path = typer.Option(specs.ROOT / "data" / "runs" / "hardest.txt", help="Where to write the ids")) -> None:
    """Write the ids of the N hardest dev documents (difficulty 'hard', spread across categories) for A/B runs.
    Only ids leave bench/; the backend never sees the answer keys."""
    entries = json.loads((specs.DEV_CORPUS / "manifest.json").read_text())["documents"]
    wanted = {c.strip() for c in categories.split(",")} if categories else None
    hard = [e for e in entries if e["difficulty"] == "hard" and (wanted is None or e["category"] in wanted)]
    by_cat: dict[str, list[str]] = {}
    for e in sorted(hard, key=lambda e: e["doc_id"]):
        by_cat.setdefault(e["category"], []).append(e["doc_id"])
    picked: list[str] = []
    while len(picked) < min(n, len(hard)):  # round-robin across categories
        for ids in by_cat.values():
            if ids and len(picked) < n:
                picked.append(ids.pop(0))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(picked) + "\n")
    typer.echo(f"{len(picked)} ids -> {out}")


@app.callback()
def main() -> None:
    """Companion data, held-out set and AP evaluation for invoice-analyst-ai."""


if __name__ == "__main__":
    app()
