"""The final evaluation on the sealed held-out set (decision 47). `make final` runs it.

  1. checks: the tree is clean and committed; the seal holds (documents, companion data, seed); no final run is
     logged yet (or this one is being resumed at the same commit)
  2. benchmark mode: the backend reads the 181 held-out documents (new model calls, ~8 minutes at our pace)
  3. scores: the official scorer, the extended scorer, and the confidence check (measured, nothing tuned)
  4. inbox mode: the held-out inbox against its vendors, POs and deliveries (readings reused from step 2), then the
     AP evaluation: planted problems, decisions, false automatic approvals, payment timing
  5. one entry in data/eval/EVAL_LOG.jsonl, and a local report in docs/results/heldout-final.md (not tracked)

Progress is kept in data/eval/final-run.json, so an interrupted run (an API outage, say) picks up where it
stopped; it can only resume at the commit it started from. Once a final entry is logged, a later held-out run needs
`--after-final` and is logged as such: it never replaces the final one.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys

from . import evallog, heldout, score, specs

STATE = specs.ROOT / "data" / "eval" / "final-run.json"
REPORT = specs.ROOT / "docs" / "results" / "heldout-final.md"


class FinalError(SystemExit):
    pass


def _clean_commit() -> str:
    sha = evallog.commit()
    if sha.endswith("-dirty"):
        raise FinalError("commit or discard your changes first: the final run evaluates a committed tree")
    return sha


def _backend(*args: str) -> str:
    """Run a backend command, showing its output as it goes; returns the output."""
    env = {**os.environ, "PYTHONPATH": str(specs.ROOT / "backend" / "src")}
    proc = subprocess.Popen([str(specs.ROOT / "backend" / ".venv" / "bin" / "python"), "-m", "invoice_analyst", *args],
                            cwd=specs.ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    for line in proc.stdout:  # type: ignore[union-attr]
        print(line, end="", flush=True)
        lines.append(line)
    if proc.wait() != 0:
        raise FinalError(f"backend command failed: {' '.join(args)}")
    return "".join(lines)


def _save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1) + "\n")


def run(after_final: bool = False) -> dict:
    sha = _clean_commit()
    if problems := heldout.verify():
        raise FinalError("the held-out seal doesn't hold: " + "; ".join(problems))
    if not heldout.companion_sealed():
        raise FinalError("the held-out companion data isn't generated and sealed yet (apbench companion --set heldout)")
    finals = [e for e in evallog.entries() if e["kind"] == "final"]
    state = json.loads(STATE.read_text()) if STATE.exists() else None
    if state and state.get("done"):
        state = None  # the last one finished; this is a new run
    if finals and not after_final and state is None:
        raise FinalError(f"the final run is already logged ({finals[0]['logged_at']}); a later held-out run needs "
                         "--after-final and is logged as such")
    if state is not None and state["commit"] != sha:
        raise FinalError(f"a final run started at commit {state['commit'][:9]} is unfinished; it can only be resumed "
                         "unchanged (check out that commit)")
    if state is None:
        state = {"kind": "after_final" if finals else "final", "commit": sha,
                 "started_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        _save(state)
    print(f"{state['kind'].replace('_', ' ')} run at {sha[:9]} (started {state['started_at']})", flush=True)

    if "bench_run" not in state:
        out = _backend("bench", "run", "--dataset", "heldout", "--final", "--label", state["kind"])
        m = re.search(r"Run (\d+): (\d+) documents, \d+ new extractions, \d+ from cache, (\d+) failed", out)
        if not m:
            raise FinalError("couldn't read the benchmark run's summary")
        if int(m.group(3)):
            raise FinalError(f"{m.group(3)} document(s) failed (e.g. the model API refused); run make final again "
                             "to resume: documents already read come from the cache")
        state["bench_run"] = m.group(1)
        _save(state)
    score.official(state["bench_run"], "heldout", final=True)

    if "inbox_run" not in state:
        out = _backend("inbox", "run", "--set", "heldout", "--label", state["kind"], "--speed", "0", "--worker",
                       "--final")
        m = re.search(r"Inbox run (\d+)", out)
        if not m or "'status': 'finished'" not in out:
            raise FinalError("the held-out inbox run didn't finish; run make final again to resume")
        state["inbox_run"] = m.group(1)
        _save(state)

    entry = evallog.summarize(state["kind"], "heldout", state["bench_run"], state["inbox_run"])
    entry["started_at"] = state["started_at"]
    evallog.append(entry)
    if state["kind"] == "final":
        REPORT.write_text(report(entry))
    state["done"] = True
    _save(state)
    return entry


def _pct(n: int, d: int) -> str:
    return f"{n / d:.1%} ({n} of {d})" if d else "—"


def report(e: dict) -> str:
    o, x, c = e["official"], e["extended"], e["confidence"]
    high = c["accuracy"].get("all", {}).get("high", {"n": 0, "right": 0})
    lines = [f"# Final evaluation on the held-out set ({e['logged_at'][:10]})", "",
             f"Commit {e['commit'][:9]}; benchmark run {e['bench_run']}, inbox run {e['inbox_run']}. Evaluated once "
             "(decision 47), after building and tuning on the dev set only.", "",
             "| measure | held-out |", "|---|---|",
             f"| documents with every key field right (official) | {_pct(o['fully_correct'], o['documents'])} |",
             f"| key fields right (official) | {_pct(o['key_fields']['correct'], o['key_fields']['scored'])} |",
             f"| line items, mean F1 | {o['line_items']['f1_mean']:.3f} |",
             f"| extended fields right | {_pct(x['overall'].get('right', 0), x['overall'].get('right', 0) + x['overall'].get('wrong', 0))} |",
             f"| 'high' confidence right | {_pct(high['right'], high['n'])} |"]
    if "ap" in e:
        a = e["ap"]
        caught = sum(v.get("caught", 0) for v in a["planted"].values())
        planted = sum(v.get("caught", 0) + v.get("missed", 0) for v in a["planted"].values())
        d = a["decisions"]
        t = a["payment_timing"]
        lines += [f"| planted problems caught | {_pct(caught, planted)} |",
                  f"| **false automatic approvals** | **{len(a['false_auto_approvals'])}** |",
                  f"| approved automatically | {_pct(d.get('auto_approved', 0), d.get('bills', 0))} |",
                  f"| payments planned into the expected run | {_pct(t.get('right run', 0), t.get('right run', 0) + t.get('other run', 0))} |"]
    lines += ["", "Details per field, category and check: the Evaluation page in the app, and data/eval/EVAL_LOG.jsonl."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    run("--after-final" in sys.argv)
