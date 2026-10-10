"""Evidence-based confidence (decision 7): each field's evidence -> a tier and plain-language reasons.

Pure Python on purpose: bench/ imports this module to calibrate the knobs against the answer keys, which the
backend never sees. The backend gathers the evidence (signals) in `evidence.py`.

Every reason has an effect: '+' supports the value, '-' casts some doubt, '!' is a serious doubt, 'i' is info.
  high    found on the page (in the text layer, or by confident OCR) and no doubts at all
  low     any serious doubt, or two or more doubts
  medium  everything else
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

KNOBS: dict[str, float] = {
    "ocr_conf_min": 0.90,  # OCR confidence below this = 'OCR was unsure'
    "sharpness_min": 100.0,  # page sharpness (Laplacian variance) below this = 'blurry'
    "brightness_min": 80.0,  # mean grey level below this = 'dark'
}

FIELD_NAMES = {
    "vendor_name": "vendor name", "invoice_number": "invoice number", "invoice_date": "invoice date",
    "due_date": "due date", "po_number": "PO number", "currency": "currency", "subtotal": "subtotal", "tax": "tax",
    "total": "total", "vendor_tax_id": "vendor tax ID", "line_amount": "line amount",
    "line_description": "line description",
}
TIER_ORDER = {"low": 0, "medium": 1, "high": 2}
# Often legitimately absent: not printing them is no doubt (tax on zero-rated invoices, POs, due dates, tax IDs)
OPTIONAL = {"tax", "po_number", "due_date", "vendor_tax_id"}
NOTE_EFFECTS = {"ambiguous_country": "-", "ambiguous_resolved": "i", "disagrees": "!", "unparsed": "!",
                "no_digits": "!", "dual": "i"}


def _r(code: str, text: str, effect: str) -> dict[str, str]:
    return {"code": code, "text": text, "effect": effect}


def assess(sig: dict[str, Any], knobs: dict[str, float] | None = None) -> tuple[str, list[dict[str, str]]]:
    """(tier, reasons) for one field from its signals:
    route ('text_llm'/'image_llm'), printed (bool), grounding, word_conf, near_miss, matched, notes [{code,text}],
    checks_pass [str], checks_fail [{code,message}], sharpness, brightness, extra [{code,text,effect}]."""
    k = {**KNOBS, **(knobs or {})}
    reasons: list[dict[str, str]] = []
    image = sig.get("route") == "image_llm"

    if not sig.get("printed", True):
        if sig.get("field") in OPTIONAL:
            reasons.append(_r("not_printed", "Not printed on the document (often the case for this field)", "i"))
        else:
            reasons.append(_r("not_printed", "Not printed on the document (left empty)", "-"))
    else:
        g = sig.get("grounding")
        if g == "exact":
            if not image:
                reasons.append(_r("found_text_layer", "Found in the PDF's text layer", "+"))
            elif (sig.get("word_conf") or 0) >= k["ocr_conf_min"]:
                reasons.append(_r("found_ocr", f"Found on the page by OCR (confidence {sig['word_conf']:.2f})", "+"))
            else:
                reasons.append(_r("found_ocr_unsure", f"Found on the page, but OCR was unsure "
                                                      f"(confidence {sig.get('word_conf') or 0:.2f})", "-"))
        elif g == "fuzzy" and sig.get("near_miss"):
            reasons.append(_r("page_reads_differently", f"The page reads {sig['near_miss']!r}, one character "
                                                        "different: either the model or the OCR misread it", "!"))
        elif g == "fuzzy":
            reasons.append(_r("close_match", f"Only a close match on the page ({sig.get('matched')!r})", "-"))
        elif g == "page":
            if image:
                reasons.append(_r("not_confirmed_ocr", "Couldn't confirm it on the page (OCR didn't find it)", "-"))
            else:
                reasons.append(_r("not_in_text_layer", "Not found in the PDF's text layer", "!"))

    for note in sig.get("notes") or []:
        code = note["code"].split(".", 1)[-1]
        effect = NOTE_EFFECTS.get(code, "-")
        reasons.append(_r(note["code"], note["text"], effect))
    for check in sig.get("checks_pass") or []:
        reasons.append(_r(f"check.{check}", "Agrees with the other figures on the invoice", "+"))
    for check in sig.get("checks_fail") or []:
        reasons.append(_r(check["code"], check["message"], "!"))
    reasons.extend(sig.get("extra") or [])

    if image and sig.get("printed", True):
        if sig.get("sharpness") is not None and sig["sharpness"] < k["sharpness_min"]:
            reasons.append(_r("page_blurry", f"The photo is blurry (sharpness {sig['sharpness']:.0f})", "-"))
        if sig.get("brightness") is not None and sig["brightness"] < k["brightness_min"]:
            reasons.append(_r("page_dark", f"The photo is dark (brightness {sig['brightness']:.0f})", "-"))

    effects = [r["effect"] for r in reasons]
    found = any(r["code"] in ("found_text_layer", "found_ocr") for r in reasons)
    if not sig.get("printed", True) and sig.get("field") in OPTIONAL:
        found = bool(sig.get("checks_pass"))  # e.g. no tax printed, and the totals add up without it
    if "!" in effects or effects.count("-") >= 2:
        tier = "low"
    elif found and "-" not in effects:
        tier = "high"
    else:
        tier = "medium"
    return tier, reasons


def lowest(tiers: list[str]) -> str | None:
    return min(tiers, key=TIER_ORDER.__getitem__) if tiers else None


# Written by `apbench calibrate` from a scored dev run: the tuned knobs, and how often each tier was right there.
CALIBRATION_PATH = Path(__file__).with_name("calibration.json")
CALIBRATION: dict[str, Any] = json.loads(CALIBRATION_PATH.read_text()) if CALIBRATION_PATH.exists() else {}
DEFAULT_KNOBS = dict(KNOBS)
KNOBS.update(CALIBRATION.get("knobs", {}))


def calibrated_accuracy(route: str, tier: str) -> float | None:
    """Share of dev fields with this route and tier that were right (None before calibration)."""
    entry = CALIBRATION.get("accuracy", {}).get(route, {}).get(tier)
    return entry["accuracy"] if entry else None
