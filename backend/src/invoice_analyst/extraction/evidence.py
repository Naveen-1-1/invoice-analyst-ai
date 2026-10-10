"""Gather each field's evidence (where it is on the page, notes, checks, page quality) and assess it.

Produces one FieldEvidence per header field and per line amount/description; `confidence.assess` turns the signals
into a tier with reasons. The signals are also exported per run so bench/ can calibrate the knobs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from ..checks.arithmetic import arithmetic_report
from . import confidence, normalize
from .grounding import Located, PageIndex, locate_amount, locate_code, locate_text
from .resolve import Resolved
from .schemas import InvoiceDoc

COUNTRY_CURRENCY = {"US": "USD", "ZA": "ZAR", "NA": "NAD", "ZM": "ZMW", "GB": "GBP", "DE": "EUR", "NL": "EUR",
                    "FR": "EUR"}
# which fields each arithmetic check vouches for
CHECK_FIELDS = {"lines_vs_subtotal": ("subtotal", "line_amount"), "lines_vs_total": ("total", "line_amount"),
                "total": ("subtotal", "tax", "total"), "tax_rate": ("tax",)}
ISSUE_FIELDS = {"arith.lines_vs_subtotal": ("subtotal", "line_amount"), "arith.lines_vs_total": ("total", "line_amount"),
                "arith.total_mismatch": ("subtotal", "tax", "total"), "arith.tax_rate_mismatch": ("tax",)}
KEY_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "due_date", "currency", "subtotal", "tax", "total")


@dataclass
class FieldEvidence:
    field: str
    value: Any
    raw_text: str | None
    located: Located
    signals: dict[str, Any]
    line_no: int | None = None
    tier: str = "medium"
    reasons: list[dict] = field(default_factory=list)


def _symbol_word(symbol: str | None, pages: list[PageIndex]) -> Located | None:
    """The first word that contains the currency symbol, as a location (with its own OCR confidence)."""
    if not symbol:
        return None
    for page in pages:
        for k, word in enumerate(page.words):
            if symbol in word["t"]:
                return Located("exact", page.page_no, page.box(k, k + 1), word["t"], page.conf(k, k + 1))
    return None


def gather(inv: InvoiceDoc, r: Resolved, pages: list[PageIndex], route: str,
           quality: dict[int, dict]) -> list[FieldEvidence]:
    passed, issues = arithmetic_report(inv)
    out: list[FieldEvidence] = []

    def add(name: str, value: Any, raw: str | None, located: Located, *, printed: bool = True,
            line_no: int | None = None, extra: list[dict] | None = None) -> None:
        page_q = quality.get(located.page_no or 1, {})
        prefix = name + "."
        sig = {
            "field": name, "route": route, "printed": printed, "grounding": located.grounding,
            "word_conf": located.word_conf, "near_miss": located.near_miss, "matched": located.matched,
            "notes": [n for n in r.notes if n["code"].startswith(prefix)],
            "checks_pass": [c for c in passed if name in CHECK_FIELDS.get(c, ())],
            "checks_fail": [{"code": i.code, "message": i.message} for i in issues if name in ISSUE_FIELDS.get(i.code, ())],
            "sharpness": page_q.get("sharpness"), "brightness": page_q.get("brightness"), "extra": extra or [],
        }
        out.append(FieldEvidence(name, value, raw, located, sig, line_no))

    # header: who, which, when
    add("vendor_name", r.vendor_name, inv.vendor.name.text,
        locate_text("vendor_name", inv.vendor.name.text, pages, inv.vendor.name.page), printed=bool(r.vendor_name))
    extra = []
    if r.invoice_number and inv.po_number.text and normalize.norm_invoice_number(inv.po_number.text) == r.invoice_number_norm:
        extra.append({"code": "same_as_po", "text": "Same as the PO number printed on the document", "effect": "!"})
    add("invoice_number", r.invoice_number, inv.invoice_number.text,
        locate_code("invoice_number", inv.invoice_number.text, pages, inv.invoice_number.page),
        printed=bool(inv.invoice_number.text), extra=extra)
    add("invoice_date", r.invoice_date, inv.invoice_date.text,
        locate_code("invoice_date", inv.invoice_date.text, pages, inv.invoice_date.page),
        printed=bool(inv.invoice_date.text))
    extra = []
    days = normalize.terms_days(inv.payment_terms.text)
    if days is not None and r.invoice_date and r.due_date:
        gap = (dt.date.fromisoformat(r.due_date) - dt.date.fromisoformat(r.invoice_date)).days
        extra.append({"code": "terms_match", "text": f"Invoice date + {days} days terms = due date", "effect": "+"}
                     if gap == days else
                     {"code": "terms_mismatch", "text": f"The due date is {gap} days after the invoice date, but the "
                                                        f"terms say {days} days", "effect": "-"})
    add("due_date", r.due_date, inv.due_date.text,
        locate_code("due_date", inv.due_date.text, pages, inv.due_date.page), printed=bool(inv.due_date.text),
        extra=extra)
    add("po_number", inv.po_number.text, inv.po_number.text,
        locate_code("po_number", inv.po_number.text, pages, inv.po_number.page), printed=bool(inv.po_number.text))
    add("vendor_tax_id", inv.vendor.tax_id.text, inv.vendor.tax_id.text,
        locate_code("vendor_tax_id", inv.vendor.tax_id.text, pages, inv.vendor.tax_id.page),
        printed=bool(inv.vendor.tax_id.text))

    # currency: the ISO code printed, or the symbol, plus the vendor's country
    code_loc = locate_code("currency", r.currency, pages)
    extra = []
    if code_loc.grounding != "exact":
        symbol_loc = _symbol_word(inv.currency_symbol, pages)
        if symbol_loc is not None:  # only the symbol is printed; the code follows from it
            code_loc = symbol_loc
            extra.append({"code": "symbol_printed", "text": f"The symbol {inv.currency_symbol!r} is printed; the "
                                                            "code follows from it and the vendor's country",
                          "effect": "i"})
    default = COUNTRY_CURRENCY.get(r.vendor_country or "")
    if default and r.currency:
        extra.append({"code": "country_currency", "text": f"{r.currency} is the usual currency in {r.vendor_country}",
                      "effect": "+"} if default == r.currency else
                     {"code": "foreign_currency", "text": f"{r.currency} is not {r.vendor_country}'s usual "
                                                          f"currency ({default})", "effect": "i"})
    add("currency", r.currency, inv.currency_symbol or r.currency, code_loc, printed=bool(r.currency), extra=extra)

    # money: subtotal, tax and total (from the payable box on dual-currency invoices)
    dual = any(n["code"] == "currency.dual" for n in r.notes)
    pay = inv.amount_payable
    if inv.subtotal.text or inv.subtotal.value is not None:
        add("subtotal", r.subtotal, inv.subtotal.text,
            locate_amount("subtotal", inv.subtotal.text, None if dual else r.subtotal, pages, inv.subtotal.page))
    else:
        add("subtotal", r.subtotal, None, Located("none"), printed=r.subtotal is not None,
            extra=[{"code": "derived", "text": "Not printed: worked out from the lines and the tax", "effect": "i"}])
    if dual and pay is not None:
        add("total", r.total, pay.text, locate_amount("total", pay.text, pay.amount, pages))
        add("tax", r.tax, None, locate_amount("tax", None, pay.tax_amount, pages) if pay.tax_amount else Located("none"),
            printed=bool(pay.tax_amount))
    else:
        add("total", r.total, inv.total.text, locate_amount("total", inv.total.text, r.total, pages, inv.total.page),
            printed=r.total is not None)
        amounts = [t.amount for t in inv.tax_lines if t.amount]
        if amounts:
            locs = [locate_amount("tax", None, a, pages) for a in amounts]
            worst = min(locs, key=lambda x: {"exact": 2, "fuzzy": 1}.get(x.grounding, 0))
            add("tax", r.tax, ", ".join(t.label or "" for t in inv.tax_lines), worst)
        else:
            add("tax", r.tax, None, Located("none"), printed=False)

    # lines: amount and description of each
    for n, ln in enumerate(inv.lines, 1):
        extra = []
        if ln.quantity and ln.unit_price and ln.amount is not None and abs(ln.quantity * ln.unit_price - ln.amount) < 0.011:
            extra.append({"code": "qty_x_price", "text": "Quantity x unit price = amount", "effect": "+"})
        add("line_amount", ln.amount, None, locate_amount("line_amount", None, ln.amount, pages, ln.page),
            printed=ln.amount is not None, line_no=n, extra=extra)
        add("line_description", ln.description, ln.description,
            locate_text("line_description", ln.description, pages, ln.page), line_no=n)

    for ev in out:
        ev.tier, ev.reasons = confidence.assess(ev.signals)
    return out


def invoice_tier(evidence: list[FieldEvidence]) -> str | None:
    """The invoice is only as sure as its least-sure key field (due date only if one is printed)."""
    tiers = [e.tier for e in evidence
             if (e.field in KEY_FIELDS and (e.field != "due_date" or e.signals["printed"]))
             or e.field in ("line_amount", "line_description")]
    return confidence.lowest(tiers)
