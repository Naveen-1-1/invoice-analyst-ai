"""Generate the held-out corpus once from a private seed, seal it, and check the seal.

The seed is read from HELDOUT_SEED (environment or the project-root .env) and never printed. LOCK.json records
a hash of every generated file plus a slow, salted fingerprint of the seed, so `verify` can confirm both the
files and the seed later without the seed itself being stored anywhere in the repo.

Nothing here prints document contents: the held-out set is not to be looked at while the system is tuned.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

from . import specs

HELDOUT_DIR = specs.ROOT / "data" / "heldout"
CORPUS_DIR = HELDOUT_DIR / "corpus"
LOCK_PATH = HELDOUT_DIR / "LOCK.json"
HAND_FONT = "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf"
COMPANION_PARTS = ("companion", "inbox", "labels")  # generated before the final run (decision 44), then sealed
# scrypt cost: ~50 ms per guess, so trying every 6-digit seed takes over half a day
SCRYPT = {"n": 2**14, "r": 8, "p": 1}


class HeldoutError(SystemExit):
    pass


def _env_value(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name].strip()
    env_file = specs.ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name:
                return value.strip().strip("'\"") or None
    return None


def _seed() -> int:
    raw = _env_value("HELDOUT_SEED")
    if not raw:
        raise HeldoutError("HELDOUT_SEED is not set (environment or project-root .env)")
    try:
        seed = int(raw)
    except ValueError:
        raise HeldoutError("HELDOUT_SEED must be a whole number") from None
    if seed == specs.DEV_SEED:
        raise HeldoutError(f"HELDOUT_SEED must differ from the dev seed ({specs.DEV_SEED})")
    return seed


def _fingerprint(seed: int, salt: bytes) -> str:
    return hashlib.scrypt(f"invoice-analyst-ai heldout seed:{seed}".encode(), salt=salt, **SCRYPT).hex()


def _file_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _bench_commit() -> str:
    out = subprocess.run(["git", "-C", str(specs.BENCH_DIR), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def generate() -> dict[str, Any]:
    """Render the held-out corpus and write LOCK.json. Refuses to touch an existing set."""
    seed = _seed()
    if LOCK_PATH.exists() or CORPUS_DIR.exists():
        raise HeldoutError(f"held-out set already exists at {HELDOUT_DIR}; it is generated once and then sealed")
    if not Path(HAND_FONT).is_file():
        raise HeldoutError(f"handwriting font not found: {HAND_FONT}")
    # Must be set before the generator first resolves its font (it caches the choice)
    os.environ["INVOICE_BENCH_HAND_FONT"] = HAND_FONT
    gen = specs.generator_module()
    try:
        manifest = gen.generate("v1", seed=seed, out=CORPUS_DIR, quiet=True)
    except ValueError as exc:  # the generator's own validation rejected a document at this seed
        raise HeldoutError(f"the generator rejected this seed ({type(exc).__name__}); choose another HELDOUT_SEED "
                           f"and delete {CORPUS_DIR} first") from None
    salt = secrets.token_bytes(16)
    lock = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "set": "v1",
        "document_count": manifest["count"],
        "benchmark_commit": _bench_commit(),
        "generator_version": manifest["generator_version"],
        "hand_font": {**manifest["hand_font"], "path": HAND_FONT},
        "seed_fingerprint": {"kdf": "scrypt", **SCRYPT, "salt": salt.hex(), "hash": _fingerprint(seed, salt)},
        "files": _file_hashes(CORPUS_DIR),
    }
    LOCK_PATH.write_text(json.dumps(lock, indent=1) + "\n")
    return {"documents": manifest["count"], "files": len(lock["files"])}


def companion_seed() -> int:
    """Decision 45: the companion data's seed, derived from HELDOUT_SEED (as private as it); never stored."""
    digest = hashlib.sha256(f"invoice-analyst-ai heldout companion:{_seed()}".encode()).digest()
    return int.from_bytes(digest[:6], "big")


def _companion_hashes() -> dict[str, str]:
    return {f"{part}/{k}": v for part in COMPANION_PARTS if (HELDOUT_DIR / part).exists()
            for k, v in _file_hashes(HELDOUT_DIR / part).items()}


def companion_sealed() -> bool:
    return LOCK_PATH.exists() and "companion" in json.loads(LOCK_PATH.read_text())


def seal_companion() -> int:
    """Add the companion files' hashes to the seal (once)."""
    lock = json.loads(LOCK_PATH.read_text())
    if "companion" in lock:
        raise HeldoutError("the held-out companion data is already sealed; it is generated once")
    lock["companion"] = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                         "files": _companion_hashes()}
    LOCK_PATH.write_text(json.dumps(lock, indent=1) + "\n")
    return len(lock["companion"]["files"])


def verify() -> list[str]:
    """Problems with the sealed set (empty list = intact). Checks every file hash and the seed fingerprint."""
    if not LOCK_PATH.exists():
        return [f"no seal at {LOCK_PATH}"]
    lock = json.loads(LOCK_PATH.read_text())
    problems: list[str] = []
    fp = lock["seed_fingerprint"]
    if _fingerprint(_seed(), bytes.fromhex(fp["salt"])) != fp["hash"]:
        problems.append("HELDOUT_SEED does not match the seed the set was generated with")
    if lock["benchmark_commit"] != _bench_commit():
        problems.append(f"benchmark submodule moved: sealed at {lock['benchmark_commit'][:7]}")
    actual = _file_hashes(CORPUS_DIR) if CORPUS_DIR.exists() else {}
    expected = lock["files"]
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(k for k in set(expected) & set(actual) if expected[k] != actual[k])
    if missing:
        problems.append(f"{len(missing)} sealed file(s) missing")
    if extra:
        problems.append(f"{len(extra)} unexpected file(s) added")
    if changed:
        problems.append(f"{len(changed)} file(s) changed since sealing")
    if "companion" in lock:
        sealed, now = lock["companion"]["files"], _companion_hashes()
        if sealed != now:
            problems.append(f"companion data changed since sealing ({len(set(sealed) ^ set(now))} file(s) added or "
                            f"missing, {sum(1 for k in set(sealed) & set(now) if sealed[k] != now[k])} changed)")
    return problems
