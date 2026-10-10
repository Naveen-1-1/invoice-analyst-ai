"""The backend must never see answers: no answer keys, manifests, AP labels, extended keys, or the held-out seal
and seed. (It may process held-out *documents* at the final evaluation; it may not read what the right answers are.)

Scoring happens in bench/ (apbench). If this test fails, move the code that needs answers to bench/.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
FORBIDDEN = re.compile(
    r"answers/|manifest\.json|metadata\.jsonl|labels/|ap_labels|extended_answers|LOCK\.json|HELDOUT_SEED",
    re.IGNORECASE)


def test_backend_never_references_answers_or_seal() -> None:
    hits = [f"{path.relative_to(SRC)}:{n}: {line.strip()}"
            for path in sorted(SRC.rglob("*.py"))
            for n, line in enumerate(path.read_text().splitlines(), 1)
            if FORBIDDEN.search(line)]
    assert not hits, "backend code references answer keys or held-out data:\n" + "\n".join(hits)
