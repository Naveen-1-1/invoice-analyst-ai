"""Access the benchmark generator's document specs without modifying the benchmark.

The generator builds every document from a frozen DocSpec. Those specs hold everything printed on the page
(vendor tax ID, bank details, PO number, quantities, ...), which is what companion data and the extended
answer keys are derived from.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BENCH_DIR = ROOT / "third_party" / "invoice-extraction-benchmark"
DEV_CORPUS = BENCH_DIR / "corpus" / "v1"
DEV_SEED = 42


def _import_generator():
    if not (BENCH_DIR / "generator").is_dir():
        raise SystemExit(f"benchmark not found at {BENCH_DIR}; run: make submodule")
    if str(BENCH_DIR) not in sys.path:
        sys.path.insert(0, str(BENCH_DIR))
    import generator.corpus_v1 as corpus_v1  # noqa: PLC0415 — needs the sys.path entry above
    import generator.generate as generate  # noqa: PLC0415

    return corpus_v1, generate


def load(seed: int) -> dict[str, Any]:
    """{doc_id: DocSpec} for the v1 recipe at this seed, in corpus order."""
    corpus_v1, _ = _import_generator()
    return {spec.doc_id: spec for spec in corpus_v1.build_corpus(seed)}


def generator_module():
    """The benchmark's generator.generate module (render_document, manifest_entry, generate)."""
    return _import_generator()[1]


def _plain(obj: Any) -> Any:
    """Normalise tuples to lists etc. the way the manifest's JSON round-trip does."""
    return json.loads(json.dumps(obj))


# Manifest fields that come straight from the spec (the rest describe rendered bytes)
SPEC_FIELDS = ("category", "difficulty", "kind", "description", "severity", "currency", "tags", "base_id")


def verify(seed: int = DEV_SEED, corpus: Path = DEV_CORPUS) -> list[str]:
    """Problems found when comparing specs at `seed` with `corpus/manifest.json` (empty list = match)."""
    manifest = json.loads((corpus / "manifest.json").read_text())
    entries = {e["doc_id"]: e for e in manifest["documents"]}
    specs = load(seed)
    problems: list[str] = []
    if manifest.get("seed") != seed:
        problems.append(f"manifest seed is {manifest.get('seed')}, verifying against seed {seed}")
    for doc_id in sorted(set(entries) - set(specs)):
        problems.append(f"{doc_id}: in manifest but not generated")
    for doc_id in sorted(set(specs) - set(entries)):
        problems.append(f"{doc_id}: generated but not in manifest")
    for doc_id, spec in specs.items():
        entry = entries.get(doc_id)
        if entry is None:
            continue
        if _plain(spec.expected().as_dict()) != entry["expected"]:
            problems.append(f"{doc_id}: expected answer differs")
        for name in SPEC_FIELDS:
            got = _plain(getattr(spec, name))
            if got != entry.get(name):
                problems.append(f"{doc_id}: {name} differs ({got!r} vs {entry.get(name)!r})")
    return problems
