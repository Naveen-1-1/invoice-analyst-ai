"""Command line: python -m apbench <command>."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from . import ap_eval, calibrate, companion, compare, evallog, extended, final, heldout, score, specs

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


@app.command("calibrate")
def calibrate_run(run: str = typer.Argument(..., help="A scored dev run (number or directory)"),
                  write: bool = typer.Option(True, help="Write backend/.../calibration.json"),
                  use_defaults: bool = typer.Option(False, help="Keep the default knobs; only measure accuracy")) -> None:
    """Tune the confidence knobs on a scored dev run and report how often each tier is right."""
    typer.echo(calibrate.calibrate(run, write, use_defaults))


@app.command("companion")
def companion_data(set_name: str = typer.Option("dev", "--set", help="Which corpus")) -> None:
    """Generate the vendor list, POs, deliveries, vendor history, inbox stream and planted problems (with labels)."""
    summary = companion.build(set_name)
    typer.echo(json.dumps(summary, indent=1))


@app.command("ap-eval")
def ap_evaluate(run: str = typer.Argument(..., help="An inbox run (number or directory)"),
                set_name: str = typer.Option("dev", "--set", help="Which labels")) -> None:
    """Score an inbox run's checks against the planted and natural problems in the labels."""
    if set_name == "heldout":
        raise typer.BadParameter("the held-out labels are used only by the final run (make final)")
    typer.echo(ap_eval.evaluate(run, set_name)[0])


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



@app.command("extended-keys")
def extended_keys(set_name: str = typer.Option("dev", "--set", help="dev (the held-out keys come with its companion)")) -> None:
    """Write the extended answer keys (PO, tax ID, bank, terms, addressee, line qty/price/code) from the specs."""
    if set_name != "dev":
        raise typer.BadParameter("only dev here; the held-out keys are written and sealed with its companion data")
    path = extended.write_keys("dev", specs.load(specs.DEV_SEED))
    docs = json.loads(path.read_text())["documents"]
    counts = {f: sum(1 for d in docs.values() if f in d) for f in ("po_number", "vendor_tax_id", "bank_account",
                                                                     "terms_days", "bill_to")}
    counts.update({f"line_{k}": sum(1 for d in docs.values() for ln in d["lines"] if ln[k] is not None)
                   for k in ("qty", "unit_price", "code")})
    typer.echo(f"{len(docs)} documents keyed in {path}: {counts}")


@app.command("ext-score")
def ext_score(run: str = typer.Argument(..., help="Run number or directory"),
              set_name: str = typer.Option("dev", "--set", help="Which extended answer keys")) -> None:
    """Score a run's extended.json against the extended answer keys (writes ext_scores.json / ext_scores.md)."""
    if set_name == "heldout":
        raise typer.BadParameter("held-out scoring happens only in the final run (make final)")
    text, _ = extended.score_run(run, set_name)
    typer.echo(text)


@app.command("eval-log")
def eval_log(bench_run: str = typer.Option(..., help="A full dev benchmark run (scored)"),
             inbox_run: str = typer.Option(..., help="A full dev inbox run")) -> None:
    """Log a dev reference entry (official, extended, confidence and AP results) for the Evaluation page."""
    entry = evallog.summarize("dev", "dev", bench_run, inbox_run)
    path = evallog.append(entry)
    o = entry["official"]
    typer.echo(f"logged dev runs {entry['bench_run']}/{entry['inbox_run']} in {path.relative_to(specs.ROOT)}: "
               f"{o['fully_correct']}/{o['documents']} fully right")


@app.command("final")
def final_run(after_final: bool = typer.Option(False, "--after-final",
                                               help="A held-out run after the final one (logged as such)")) -> None:
    """The final evaluation on the sealed held-out set (decision 47): once, on a clean committed tree."""
    entry = final.run(after_final)
    typer.echo("\n" + final.report(entry))


@app.command("scorecards")
def scorecards() -> None:
    """Write scorecard.json for every scored run that lacks one (for the app's run comparison)."""
    n = 0
    for d in sorted(score.RUNS_DIR.glob("[0-9]*")):
        if (d / "scores.json").exists() and not (d / "scorecard.json").exists():
            meta = json.loads((d / "run.json").read_text()).get("config", {}) if (d / "run.json").exists() else {}
            score.write_scorecard(str(d), meta.get("dataset", "dev"))
            n += 1
    typer.echo(f"wrote {n} scorecard(s)")


if __name__ == "__main__":
    app()
