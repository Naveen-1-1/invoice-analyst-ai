"""Finding values on the page, and turning the evidence into confidence tiers."""

from __future__ import annotations

from invoice_analyst.extraction.confidence import assess
from invoice_analyst.extraction.grounding import PageIndex, locate_amount, locate_code, locate_text


def page(*words: tuple[str, float, float], conf: float | None = None) -> PageIndex:
    """Words at (x, y); each 0.05 wide and 0.01 high."""
    return PageIndex(1, [{"t": t, "bbox": [x, y, x + 0.05, y + 0.01], **({"conf": conf} if conf else {})}
                         for t, x, y in words])


def test_amount_split_over_words_and_symbol_ignored() -> None:
    p = page(("Total", 0.70, 0.50), ("N$21", 0.80, 0.50), ("667.28", 0.86, 0.50))
    loc = locate_amount("total", None, 21667.28, [p])
    assert loc.grounding == "exact" and loc.matched == "N$21 667.28"
    assert loc.bbox == [0.80, 0.50, 0.91, 0.51]


def test_same_amount_twice_prefers_the_one_beside_its_label() -> None:
    p = page(("Subtotal", 0.6, 0.40), ("100.00", 0.8, 0.40), ("Total", 0.6, 0.45), ("100.00", 0.8, 0.45))
    assert locate_amount("total", None, 100.0, [p]).bbox[1] == 0.45
    assert locate_amount("subtotal", None, 100.0, [p]).bbox[1] == 0.40


def test_code_one_character_off_is_a_near_miss() -> None:
    p = page(("Invoice", 0.6, 0.1), ("#:", 0.66, 0.1), ("IN0099973", 0.8, 0.1), conf=0.97)
    exact = locate_code("invoice_number", "IN0099973", [p])
    miss = locate_code("invoice_number", "IN009973", [p])
    assert exact.grounding == "exact" and exact.word_conf == 0.97
    assert miss.grounding == "fuzzy" and miss.near_miss == "IN0099973"


def test_value_not_on_the_page() -> None:
    p = page(("Hello", 0.1, 0.1))
    assert locate_amount("total", None, 55.5, [p]).grounding == "page"
    assert locate_text("vendor_name", "Acme Industrial Supply Co", [p]).grounding == "page"


def sig(**kw):
    return {"field": "total", "route": "text_llm", "printed": True, "grounding": "exact", "word_conf": None,
            "near_miss": None, "matched": "1,629.70", "notes": [], "checks_pass": [], "checks_fail": [],
            "sharpness": None, "brightness": None, "extra": [], **kw}


def test_found_in_text_layer_and_adds_up_is_high() -> None:
    tier, reasons = assess(sig(checks_pass=["total"]))
    assert tier == "high" and {r["code"] for r in reasons} == {"found_text_layer", "check.total"}


def test_image_needs_confident_ocr_and_a_readable_photo() -> None:
    assert assess(sig(route="image_llm", word_conf=0.98, sharpness=900, brightness=240))[0] == "high"
    assert assess(sig(route="image_llm", word_conf=0.70, sharpness=900, brightness=240))[0] == "medium"
    assert assess(sig(route="image_llm", word_conf=0.98, sharpness=20, brightness=240))[0] == "medium"
    assert assess(sig(route="image_llm", word_conf=0.70, sharpness=20, brightness=240))[0] == "low"


def test_serious_doubts_make_it_low() -> None:
    assert assess(sig(grounding="fuzzy", near_miss="1,629.78"))[0] == "low"
    assert assess(sig(grounding="page"))[0] == "low"  # a PDF value missing from its own text layer
    assert assess(sig(checks_fail=[{"code": "arith.total_mismatch", "message": "doesn't add up"}]))[0] == "low"


def test_missing_optional_field_can_be_high_but_missing_key_field_cannot() -> None:
    assert assess(sig(field="tax", printed=False, grounding="none", checks_pass=["total"]))[0] == "high"
    assert assess(sig(field="tax", printed=False, grounding="none"))[0] == "medium"
    assert assess(sig(field="invoice_number", printed=False, grounding="none"))[0] == "medium"


def test_ambiguous_date_is_only_a_doubt_when_guessed_from_the_country() -> None:
    settled = [{"code": "invoice_date.ambiguous_resolved", "text": "read from the terms"}]
    guessed = [{"code": "invoice_date.ambiguous_country", "text": "read from the country"}]
    assert assess(sig(field="invoice_date", notes=settled))[0] == "high"
    assert assess(sig(field="invoice_date", notes=guessed))[0] == "medium"
