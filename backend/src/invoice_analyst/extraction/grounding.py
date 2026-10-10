"""Find where an extracted value is printed: match it against the page's words (PDF text layer or OCR).

How a value is matched depends on its kind:
  amount  the digits must match exactly (symbols, spaces and separators ignored): 'N$21 667.28' ~ 21667.28
  code    letters and digits must match exactly (invoice/PO numbers, tax IDs, references, dates)
  text    close fuzzy match of the words (vendor names, line descriptions)
For amounts and codes, a page reading one character off is reported as a near miss: either the model or the OCR
misread it, and confidence goes down (decision 25: OCR is a cross-check, never an input to the model).
When a value occurs more than once, the occurrence closest to the field's label (e.g. 'Total') wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

MAX_WINDOW = 4  # a value may be split over up to this many words ('21' '667.28', 'IN 0099 973')

LABELS = {
    "vendor_name": (), "invoice_number": ("invoice", "inv", "no", "number", "#"),
    "invoice_date": ("date", "dated", "issued"), "due_date": ("due",), "po_number": ("po", "order"),
    "subtotal": ("subtotal", "sub", "exclusive", "excl", "net"), "tax": ("vat", "tax", "gst", "mwst", "btw", "tva"),
    "total": ("total", "due", "payable", "balance"), "vendor_tax_id": ("vat", "tax", "ein", "tpin", "ust", "btw"),
}


@dataclass
class Located:
    grounding: str  # exact / fuzzy / page / none
    page_no: int | None = None
    bbox: list[float] | None = None  # normalised [x0, y0, x1, y1] on the page image
    matched: str | None = None  # the page's own words where it was found
    word_conf: float | None = None  # mean OCR confidence of those words (None for a PDF text layer)
    near_miss: str | None = None  # the page reads this instead (one character different)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _alnum(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.casefold())


def amount_keys(text: str | None, value: float | None) -> set[str]:
    keys = set()
    if text and _digits(text):
        keys.add(_digits(text))
    if value is not None:
        keys.add(_digits(f"{abs(value):.2f}"))
        if float(value).is_integer():
            keys.add(_digits(f"{abs(value):.0f}"))
    return {k.lstrip("0") or "0" for k in keys}


class PageIndex:
    """One page's words with precomputed normalisations for fast window matching."""

    def __init__(self, page_no: int, words: list[dict]):
        self.page_no = page_no
        self.words = words
        self.digits = [_digits(w["t"]) for w in words]
        self.alnum = [_alnum(w["t"]) for w in words]
        self.lower = [w["t"].casefold() for w in words]

    def box(self, i: int, j: int) -> list[float]:
        boxes = [self.words[k]["bbox"] for k in range(i, j)]
        return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]

    def conf(self, i: int, j: int) -> float | None:
        confs = [self.words[k].get("conf") for k in range(i, j)]
        return None if any(c is None for c in confs) else round(sum(confs) / len(confs), 3)

    def label_distance(self, field: str, box: list[float]) -> float:
        """How far the nearest label word sits to the left on the same row or just above (smaller is closer)."""
        labels = LABELS.get(field) or ()
        if not labels:
            return 0.0
        cy, x0 = (box[1] + box[3]) / 2, box[0]
        best = 9.0
        for k, word in enumerate(self.lower):
            if not any(word.strip(":#.") == lab or word.startswith(lab) for lab in labels):
                continue
            b = self.words[k]["bbox"]
            ly = (b[1] + b[3]) / 2
            if b[0] <= x0 + 0.01 and b[1] <= box[3]:  # left of it, or above it
                best = min(best, abs(cy - ly) * 4 + max(0.0, x0 - b[2]))
        return best


def _pick(field: str, hits: list[tuple[PageIndex, int, int]], kind: str) -> Located:
    page, i, j = min(hits, key=lambda h: (h[0].label_distance(field, h[0].box(h[1], h[2])), h[2] - h[1]))
    matched = " ".join(page.words[k]["t"] for k in range(i, j))
    return Located(kind, page.page_no, page.box(i, j), matched, page.conf(i, j))


def _ordered(pages: list[PageIndex], page_hint: int | None) -> list[PageIndex]:
    """The page the model named first, then the others."""
    return sorted(pages, key=lambda p: (p.page_no != page_hint, p.page_no))


def locate_amount(field: str, text: str | None, value: float | None, pages: list[PageIndex],
                  page_hint: int | None = None) -> Located:
    keys = amount_keys(text, value)
    if not keys:
        return Located("none")
    near: list[tuple[PageIndex, int, int]] = []
    for page in _ordered(pages, page_hint):
        hits = []
        n = len(page.words)
        for i in range(n):
            if not page.digits[i]:
                continue
            for j in range(i + 1, min(n, i + MAX_WINDOW) + 1):
                if not page.digits[j - 1]:
                    break  # a value doesn't continue past a word without digits
                joined = "".join(page.digits[i:j]).lstrip("0") or "0"
                if joined in keys:
                    hits.append((page, i, j))
                elif j - i == 1 and len(joined) >= 3 and any(
                        abs(len(joined) - len(k)) <= 1 and Levenshtein.distance(joined, k) == 1 for k in keys):
                    near.append((page, i, j))
        if hits:
            return _pick(field, hits, "exact")
    if near:
        found = _pick(field, near, "fuzzy")
        found.near_miss = found.matched
        return found
    return Located("page", page_hint)


def locate_code(field: str, text: str | None, pages: list[PageIndex], page_hint: int | None = None) -> Located:
    target = _alnum(text or "")
    if len(target) < 2:
        return Located("none")
    near: list[tuple[PageIndex, int, int]] = []
    for page in _ordered(pages, page_hint):
        hits = []
        n = len(page.words)
        for i in range(n):
            if not page.alnum[i]:
                continue
            for j in range(i + 1, min(n, i + MAX_WINDOW) + 1):
                joined = "".join(page.alnum[i:j])
                if len(joined) > len(target) + 1:
                    break  # longer windows only grow
                if joined == target:
                    hits.append((page, i, j))
                elif len(target) >= 5 and abs(len(joined) - len(target)) <= 1 \
                        and Levenshtein.distance(joined, target) == 1:
                    near.append((page, i, j))
        if hits:
            return _pick(field, hits, "exact")
    if near:
        found = _pick(field, near, "fuzzy")
        found.near_miss = found.matched
        return found
    return Located("page", page_hint)


def locate_text(field: str, text: str | None, pages: list[PageIndex], page_hint: int | None = None,
                min_ratio: float = 85.0) -> Located:
    target = " ".join((text or "").casefold().split())
    if len(target) < 2:
        return Located("none")
    n = len(target.split())
    best: tuple[float, PageIndex, int, int] | None = None
    for page in _ordered(pages, page_hint):
        for size in (n, n - 1, n + 1):
            if size < 1:
                continue
            for i in range(0, max(0, len(page.words) - size) + 1):
                window = " ".join(page.lower[i:i + size])
                if abs(len(window) - len(target)) > max(4, len(target) // 3):
                    continue
                score = fuzz.ratio(window, target)
                if best is None or score > best[0]:
                    best = (score, page, i, i + size)
        if best and best[0] >= 99:
            break
    if best is None or best[0] < min_ratio:
        return Located("page", page_hint)
    score, page, i, j = best
    return Located("exact" if score >= 99 else "fuzzy", page.page_no, page.box(i, j),
                   " ".join(page.words[k]["t"] for k in range(i, j)), page.conf(i, j))
