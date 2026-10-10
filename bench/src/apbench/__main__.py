"""Command line: python -m apbench <command>."""

from __future__ import annotations

from pathlib import Path

import typer

from . import heldout, specs

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


@app.callback()
def main() -> None:
    """Companion data, held-out set and AP evaluation for invoice-analyst-ai."""


if __name__ == "__main__":
    app()
