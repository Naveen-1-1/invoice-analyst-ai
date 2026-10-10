"""Command line: python -m invoice_analyst <command>  (the Makefile calls this `ia`)."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import typer

from .config import settings

app = typer.Typer(add_completion=False, no_args_is_help=True)
bench_app = typer.Typer(add_completion=False, no_args_is_help=True,
                        help="Benchmark mode: each document on its own, scored by the official scorer.")
app.add_typer(bench_app, name="bench")
inbox_app = typer.Typer(add_completion=False, no_args_is_help=True,
                        help="Inbox mode: documents arrive in order into one workspace with its vendors and POs.")
app.add_typer(inbox_app, name="inbox")


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


@inbox_app.command("run")
def inbox_run(
    set_name: str = typer.Option("dev", "--set", help="Which companion set (vendors, POs, inbox stream)"),
    label: str | None = typer.Option(None, help="Short name for this run"),
    speed: float = typer.Option(2.0, help="Real seconds per simulated day; 0 = everything arrives at once"),
    fresh: bool = typer.Option(False, help="Ignore answers cached before this run (a resumed run still reuses its own)"),
    limit: int | None = typer.Option(None, help="Only the first N arrivals"),
    worker: bool = typer.Option(False, "--worker", help="Also process the run in this process, until it finishes"),
) -> None:
    """Start an inbox run: schedule every arrival; the worker (make worker, or --worker) reads and checks them."""
    from .inbox.feed import feed, status

    fed = asyncio.run(feed(set_name, label=label, seconds_per_day=speed, fresh=fresh, limit=limit))
    typer.echo(f"Inbox run {fed.run_id} (workspace {fed.workspace_id}): {fed.documents} arrivals from {fed.first_date} "
               f"to {fed.last_date}, over about {fed.seconds / 60:.0f} min")
    if worker:
        asyncio.run(_work_until_finished(fed.run_id))
        typer.echo(f"Finished: {status(fed.run_id)}")
    else:
        typer.echo("Start the worker (make worker) to process it; watch it on the board.")


@inbox_app.command("status")
def inbox_status(run: int = typer.Argument(..., help="Inbox run number")) -> None:
    """How far an inbox run has got."""
    from .inbox.feed import status

    typer.echo(status(run))


async def _work_until_finished(run_id: int) -> None:
    from .db.engine import session_factory
    from .db.models import Run
    from .worker.app import QUEUES, app as queue
    from .worker.run import retry_stalled

    async with queue.open_async():
        await retry_stalled()
        worker = asyncio.create_task(queue.run_worker_async(queues=list(QUEUES), concurrency=8,
                                                            install_signal_handlers=False))
        while True:
            await asyncio.sleep(2)
            with session_factory()() as s:
                if s.get(Run, run_id).status != "running":
                    break
            if worker.done():
                worker.result()  # surfaces a crashed worker
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


@app.command("worker")
def run_worker(concurrency: int = typer.Option(8, help="Jobs at once (model calls are capped separately)")) -> None:
    """Process the job queue: arrivals, reading and checks. Run exactly one (it holds the shared rate limiter)."""
    from .worker.run import main as worker_main

    asyncio.run(worker_main(concurrency))


if __name__ == "__main__":
    app()
