"""Command line: python -m invoice_analyst <command>  (the Makefile calls this `ia`)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from .config import settings

app = typer.Typer(add_completion=False, no_args_is_help=True)
bench_app = typer.Typer(add_completion=False, no_args_is_help=True,
                        help="Benchmark mode: each document on its own, scored by the official scorer.")
app.add_typer(bench_app, name="bench")


def _ids(only: str | None) -> set[str] | None:
    if not only:
        return None
    if only.startswith("@"):
        return {ln.strip() for ln in Path(only[1:]).read_text().splitlines() if ln.strip()}
    return {x.strip() for x in only.split(",") if x.strip()}


@bench_app.command("run")
def bench_run(
    dataset: str = typer.Option("dev", help="Which corpus to read"),
    smoke: bool = typer.Option(False, help="Two documents per category (20 in all)"),
    only: str | None = typer.Option(None, help="Comma-separated doc ids, or @file with one id per line"),
    limit: int | None = typer.Option(None, help="At most this many documents"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Call the models even if an answer is cached"),
    reasoning: str | None = typer.Option(None, help="Override reasoning_effort for both models (A/B tests)"),
    label: str | None = typer.Option(None, help="Short name for this run"),
    ocr_hint: bool = typer.Option(False, "--ocr-hint", help="Also give the vision model the local OCR text (A/B)"),
) -> None:
    """Extract every selected document and write data/runs/<run>/predictions.json."""
    # imported here so `--help` doesn't load the database and model clients
    from .bench.runner import run, select_documents

    docs = select_documents(dataset, smoke=smoke, only=_ids(only), limit=limit)
    overrides = {"reasoning_effort": reasoning} if reasoning else None
    summary = asyncio.run(run(settings(), dataset, docs, use_cache=not no_cache, overrides=overrides, label=label,
                              ocr_hint=ocr_hint))
    typer.echo(f"\nRun {summary.run_id}: {summary.documents} documents, {summary.new_calls} new extractions, "
               f"{summary.cache_hits} from cache, {len(summary.failed)} failed")
    if summary.failed:
        typer.echo("Failed: " + ", ".join(summary.failed))
    typer.echo(f"Predictions: {summary.out_dir / 'predictions.json'} (sha256 {summary.predictions_sha256[:12]})")


if __name__ == "__main__":
    app()
