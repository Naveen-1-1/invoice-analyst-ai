"""The settings page (decision 49): every threshold, cap, tolerance, rate and model as the system uses it now, read
from the code that uses it, each with the decision that set it. View-only; today's date is set on Payments."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..checks import base as check_base
from ..checks import duplicates, match, patterns
from ..config import ROOT, settings
from ..db.models import Workspace
from ..decision import rules
from ..extraction import confidence, extract, prompts
from ..extraction.schemas import SCHEMA_VERSION
from ..payments import calendar, clock
from .deps import db

router = APIRouter()
INFO_WORDS = {"pattern.round_total": "round total", "doctype.credit_note": "credit note",
              "doctype.not_invoice": "not an invoice (statements, other documents)"}


class SettingOut(BaseModel):
    name: str
    value: str
    explanation: str
    decision: int | None  # docs/decisions.md row


class SectionOut(BaseModel):
    title: str
    items: list[SettingOut]


class WorkspaceTodayOut(BaseModel):
    id: int
    name: str
    kind: str
    today: str
    live: bool


class DecisionOut(BaseModel):
    number: int
    title: str
    text: str


class SettingsOut(BaseModel):
    sections: list[SectionOut]
    workspaces: list[WorkspaceTodayOut]
    decisions: list[DecisionOut]  # the ones referred to above, from docs/decisions.md


def _decisions() -> dict[int, DecisionOut]:
    path = ROOT / "docs" / "decisions.md"
    out = {}
    for line in path.read_text().splitlines() if path.exists() else []:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0].isdigit():
            out[int(cells[0])] = DecisionOut(number=int(cells[0]), title=cells[1], text=" | ".join(cells[2:]))
    return out


def _item(name: str, value: object, explanation: str, decision: int | None = None) -> SettingOut:
    return SettingOut(name=name, value=str(value), explanation=explanation, decision=decision)


@router.get("/api/settings", response_model=SettingsOut, name="getSettings")
def get_settings(s: Session = Depends(db)) -> SettingsOut:
    cfg = settings()
    knobs = (confidence.CALIBRATION or {}).get("knobs") or confidence.DEFAULT_KNOBS
    cal_source = (confidence.CALIBRATION or {}).get("source", {})
    fx = ", ".join(f"{k} {v}" for k, v in rules.FX_TO_USD.items() if k != "USD")
    sections = [
        SectionOut(title="Models", items=[
            _item("Digital PDFs (text layer)", cfg.text_model, "Reads the PDF's own text", None),
            _item("Images, scans, photos, PDFs without text", cfg.image_model, "Reads the page images", None),
            _item("Thinking", f"{extract.TEXT_PARAMS['reasoning_effort']} (text), "
                              f"{extract.IMAGE_PARAMS['reasoning_effort']} (images)",
                  "Medium thinking was no better on the A/B test and slower", 24),
            _item("Answer length limit", f"{extract.TEXT_PARAMS['max_tokens']} tokens (text), "
                                         f"{extract.IMAGE_PARAMS['max_tokens']} (images)",
                  "A cut-off answer counts as a failure, so the limit is generous", None),
            _item("Structured answers", "forced tool call, one retry with the validation error, then lenient",
                  "Every field must be present in the answer", 21),
            _item("OCR text sent with images", "no", "It didn't help on the A/B test", 25),
            _item("Pace", f"{cfg.llm_rpm} requests a minute, {cfg.llm_max_in_flight} at once",
                  "Below the API's 40 a minute; a refusal pauses every call until the cooldown ends", None),
            _item("Prompt", f"{prompts.PROMPT_VERSION} ({prompts.PROMPT_HASH})",
                  "Part of the cache key: a new prompt means new readings", None),
            _item("Answer template", f"version {SCHEMA_VERSION}", "Part of the cache key", None),
        ]),
        SectionOut(title="Confidence", items=[
            _item("OCR word confidence below", knobs["ocr_conf_min"], "A value found only in unsure OCR words", 26),
            _item("Page sharpness below", knobs["sharpness_min"], "The page counts as blurry", 26),
            _item("Page brightness below", knobs["brightness_min"], "The page counts as dark", 26),
            _item("'High' means", "right about 99% of the time (Wilson lower bound >= 97%)",
                  f"Tiers come from evidence, not the model's opinion; calibrated on run {cal_source.get('run', '?')}",
                  7),
            _item("An invoice's tier", "its least sure key field", "Vendor, number, dates, currency, amounts, lines", 7),
        ]),
        SectionOut(title="Checks", items=[
            _item("Block payment", ", ".join(sorted(rules.LABELS.get(c, c) for c in check_base.BLOCK)),
                  "Exact duplicates and bank details that differ: approving needs an override with a note", 28),
            _item("Information only", ", ".join(sorted(INFO_WORDS.get(c, c) for c in check_base.INFO)),
                  "Shown, never a reason to hold", 28),
            _item("Everything else", "needs review", "", 28),
            _item("Possible duplicate", f"same vendor and total within {duplicates.SAME_AMOUNT_DAYS} days, or the "
                                        "same vendor, total and line amounts", "", 31),
            _item("Unusual amount", f"over {patterns.ABOVE_HISTORY_FACTOR:g}x the vendor's median, with at least "
                                    f"{patterns.MIN_HISTORY} past invoices", "", 30),
            _item("Round total", f"a multiple of 100, of at least {patterns.ROUND_TOTAL_MIN:,}", "Information only",
                  28),
            _item("Future date", "dated after it arrived", "Needs review", 28),
            _item("PAID / APPROVED stamp", "a stamp or note with those words on the page", "Needs review", 28),
        ]),
        SectionOut(title="Three-way match", items=[
            _item("Price above the PO allowed", f"{match.PRICE_TOLERANCE_PCT:.0%} or "
                                                f"{match.PRICE_TOLERANCE_ABS} a unit, whichever is more", "", 9),
            _item("Quantity", "never more than received and not yet invoiced", "", 9),
            _item("Charges not on the PO", f"review when over {match.CHARGES_MAX_SHARE:.0%} of the total", "", 9),
            _item("Pairing invoice and PO lines", f"item code, then description (similarity >= "
                                                  f"{match.DESCRIPTION_MATCH_MIN}), then amount", "", 9),
            _item("No PO printed", "review, labelled 'no PO'", "POs exist only where the invoice prints one", 10),
        ]),
        SectionOut(title="Approval", items=[
            _item("Approved automatically when", "nothing to review or blocking, every key value 'high', a PO "
                                                 f"matched, and at most USD {rules.AUTO_APPROVE_CAP_USD:,.0f}",
                  f"Rules {rules.RULES_VERSION}", 11),
            _item("Exchange rates for the cap", f"USD per unit: {fx}", f"{rules.FX_AS_OF}; never used for booking", 34),
            _item("After a correction", "stays with the reviewer", "Never approved automatically once in review", 37),
            _item("Approve the received part", "the delivered share, VAT in proportion; the rest on hold", "", 36),
            _item("Shipping on a partial approval", "held with the rest", "", 38),
            _item("Credit notes", "always reviewed", "They never match a PO", 39),
        ]),
        SectionOut(title="Payments and books", items=[
            _item("Payment runs", "weekly, on Thursday", "The last run before the due date; overdue: the next run",
                  12),
            _item("No due date or terms", f"{calendar.ASSUMED_TERMS_DAYS} days, labelled 'assumed'",
                  "Printed due date first, then printed terms, then the vendor's terms", 12),
            _item("Early-payment discounts", "taken when the vendor offers one and the run can be made", "", 13),
            _item("Books", "one ledger per workspace, each entry tagged with our company",
                  "In the invoice's currency; trial balance per currency", 40),
            _item("Who gets paid", "the bank account on file; one you confirmed in review replaces it", "", 41),
            _item("Booking date", "the day it's approved", "Arrival date for automatic approvals", 42),
        ]),
    ]
    workspaces = []
    for ws in s.scalars(select(Workspace).where(Workspace.kind != "benchmark").order_by(Workspace.id)):
        t = clock.today(s, ws)
        workspaces.append(WorkspaceTodayOut(id=ws.id, name=ws.name, kind=ws.kind, today=t.date.isoformat(),
                                            live=t.live))
    known = _decisions()
    used = sorted({i.decision for sec in sections for i in sec.items if i.decision is not None})
    return SettingsOut(sections=sections, workspaces=workspaces, decisions=[known[n] for n in used if n in known])
