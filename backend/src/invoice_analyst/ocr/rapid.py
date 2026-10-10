"""Local OCR (RapidOCR, bundled PP-OCRv6 models): the words on a page image with boxes and confidence.

Used to find where the model's quoted text sits on photos and scans, which also cross-checks the model's reading
(decision 25). text_lines() backs the optional --ocr-hint A/B mode, which is off by default. CPU only; no network.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image


@lru_cache(maxsize=1)
def _engine():
    from rapidocr import RapidOCR  # slow import (onnxruntime); load only when OCR is used

    return RapidOCR(params={"Global.log_level": "error"})


def _quad_to_box(quad, w: int, h: int) -> list[float]:
    xs, ys = [float(p[0]) for p in quad], [float(p[1]) for p in quad]
    return [max(0.0, min(xs) / w), max(0.0, min(ys) / h), min(1.0, max(xs) / w), min(1.0, max(ys) / h)]


def words(image: Image.Image) -> list[dict]:
    """[{"t", "bbox" (normalised 0-1, origin top-left), "conf", "src": "ocr"}], in reading order."""
    w, h = image.size
    out = _engine()(np.asarray(image.convert("RGB")), return_word_box=True)
    results: list[dict] = []
    line_boxes, line_texts, line_scores = out.boxes, out.txts, out.scores
    word_results = getattr(out, "word_results", None)
    if line_boxes is None:
        return results
    for i, (box, text, score) in enumerate(zip(line_boxes, line_texts, line_scores, strict=False)):
        per_word = word_results[i] if word_results is not None and i < len(word_results) else None
        if per_word:
            for item in per_word:  # (text, score, quad) per word
                wt, ws, wq = item[0], item[1], item[2]
                if wt and wt.strip():
                    results.append({"t": wt, "bbox": _quad_to_box(wq, w, h), "conf": round(float(ws), 3),
                                    "src": "ocr"})
        elif text and text.strip():
            results.append({"t": text, "bbox": _quad_to_box(box, w, h), "conf": round(float(score), 3),
                            "src": "ocr"})
    return results


OCR_VERSION = "rapidocr-3.10-ppocrv6-words-1"  # part of the on-disk cache name; bump if words() changes


def cached_words(image: Image.Image, cache_dir: Path, page_no: int) -> list[dict]:
    """words(image), stored next to the page renders so reruns skip the ~3 s of OCR per page."""
    path = cache_dir / f"ocr-{OCR_VERSION}-p{page_no}.json"
    if path.exists():
        return json.loads(path.read_text())
    result = words(image)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(result))
    tmp.replace(path)
    return result


def text_lines(image: Image.Image) -> list[str]:
    """OCR lines in reading order (for the optional text hint to the vision model)."""
    out = _engine()(np.asarray(image.convert("RGB")))
    return [t for t in (out.txts or []) if t and t.strip()]
