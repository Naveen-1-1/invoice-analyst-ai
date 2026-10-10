"""Compare two scored runs on the documents they share (for A/B tests of prompts and settings)."""

from __future__ import annotations

import json

from .score import run_dir

KEY_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "due_date", "currency", "subtotal", "tax", "total",
              "document_type")


def _load(run: str) -> dict[str, dict]:
    path = run_dir(run) / "scores.json"
    if not path.exists():
        raise SystemExit(f"{path} not found; score the run first (apbench score {run})")
    return {d["doc_id"]: d for d in json.loads(path.read_text())["documents"]}


def _summary(docs: list[dict]) -> dict[str, float]:
    key_right = sum(1 for d in docs for f in KEY_FIELDS if f in d["fields"] and d["fields"][f]["correct"])
    key_scored = sum(1 for d in docs for f in KEY_FIELDS if f in d["fields"])
    f1s = [d["line_f1"] for d in docs if d["line_f1"] is not None]
    flags = [d["fields"]["flag"]["correct"] for d in docs if "flag" in d["fields"]]
    return {"fully correct": sum(d["fully_correct"] for d in docs), "documents": len(docs),
            "key fields %": round(100 * key_right / key_scored, 1) if key_scored else 0.0,
            "line F1": round(sum(f1s) / len(f1s), 3) if f1s else 0.0,
            "flag right": sum(flags), "flag scored": len(flags)}


def compare(a: str, b: str) -> str:
    ra, rb = _load(a), _load(b)
    shared = sorted(set(ra) & set(rb))
    sa, sb = _summary([ra[i] for i in shared]), _summary([rb[i] for i in shared])
    lines = [f"Run {a} vs run {b} on {len(shared)} shared documents", "",
             f"| measure | run {a} | run {b} |", "|---|---|---|"]
    lines += [f"| {k} | {sa[k]} | {sb[k]} |" for k in sa if k != "documents"]
    changed = []
    for doc_id in shared:
        fa, fb = ra[doc_id]["fields"], rb[doc_id]["fields"]
        fixed = [f for f in fb if fb[f]["correct"] and not fa.get(f, {}).get("correct", False)]
        broke = [f for f in fb if not fb[f]["correct"] and fa.get(f, {}).get("correct", False)]
        if fixed or broke:
            changed.append(f"- {doc_id}: " + "; ".join(x for x in (
                f"fixed {', '.join(fixed)}" if fixed else "", f"broke {', '.join(broke)}" if broke else "") if x))
    lines += ["", f"Documents that changed: {len(changed)}", *changed]
    return "\n".join(lines)
