"""Does the document add up? (The benchmark scores whether inconsistent invoices are flagged.)

This is the first, conservative version: it compares printed lines, subtotal, charges, tax and total, and each
printed VAT rate against its amount. M4 turns these into stored check results with plain-language messages.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..extraction.schemas import InvoiceDoc

ABS_TOL = 0.011  # a cent of rounding either way
REL_TOL = 0.001  # VAT computed per line can differ by a little from rate x subtotal


@dataclass(frozen=True)
class Issue:
    code: str
    message: str


def _close(a: float, b: float, base: float = 0.0) -> bool:
    return abs(a - b) <= max(ABS_TOL, REL_TOL * abs(base))


def arithmetic_issues(inv: InvoiceDoc) -> list[Issue]:
    return arithmetic_report(inv)[1]


def arithmetic_report(inv: InvoiceDoc) -> tuple[list[str], list[Issue]]:
    """(checks that passed, issues). Check names: lines_vs_subtotal, lines_vs_total, total, tax_rate."""
    passed: list[str] = []
    if inv.document_type in ("statement", "other"):
        return passed, []
    # A line whose amount wasn't read can't be summed: comparing the rest with the subtotal would call a
    # perfectly consistent invoice inconsistent. (Confidence, from M3, reports the unread amount instead.)
    complete = all(ln.amount is not None for ln in inv.lines)
    lines = [ln.amount for ln in inv.lines if ln.amount is not None] if complete else []
    lines_sum = round(sum(lines), 2)
    charges = round(sum(-c.amount if c.kind == "discount" else c.amount for c in inv.charges), 2)
    tax_lines = inv.tax_lines
    payable = inv.amount_payable
    if payable is not None and payable.currency and payable.currency.upper() != (inv.currency or "").upper():
        # Dual currency: tax printed only in the payable box belongs to that currency, not to the line totals
        tax_lines = [t for t in tax_lines if t.amount is None or payable.tax_amount is None
                     or abs(t.amount - payable.tax_amount) > ABS_TOL]
    tax = round(sum(t.amount or 0.0 for t in tax_lines), 2)
    subtotal, total = inv.subtotal.value, inv.total.value
    issues: list[Issue] = []

    if inv.prices_include_tax:
        if lines and total is not None:
            if _close(lines_sum + charges, total):
                passed.append("lines_vs_total")
            else:
                issues.append(Issue("arith.lines_vs_total", f"The lines add up to {lines_sum:,.2f}, but the "
                                                            f"VAT-inclusive total is {total:,.2f}."))
        return passed, issues

    if lines and subtotal is not None:
        if _close(lines_sum, subtotal):
            passed.append("lines_vs_subtotal")
        else:
            issues.append(Issue("arith.lines_vs_subtotal", f"The lines add up to {lines_sum:,.2f}, but the printed "
                                                             f"subtotal is {subtotal:,.2f}."))
    base = subtotal if subtotal is not None else (lines_sum if lines else None)
    if base is not None and total is not None:
        if _close(base + charges + tax, total):
            passed.append("total")
        else:
            issues.append(Issue("arith.total_mismatch", f"Subtotal {base:,.2f} + charges {charges:,.2f} + tax "
                                                          f"{tax:,.2f} = {base + charges + tax:,.2f}, but the printed "
                                                          f"total is {total:,.2f}."))
    if base:
        per_line = any(ln.vat_rate_percent is not None for ln in inv.lines)
        several = len({t.rate_percent for t in tax_lines if t.rate_percent}) > 1
        for t in tax_lines:
            if t.rate_percent and t.amount is not None:
                if per_line:  # lines carry their own rate (e.g. VAT on fees, none on disbursements)
                    own = [ln.amount for ln in inv.lines
                           if ln.amount is not None and ln.vat_rate_percent == t.rate_percent]
                    if not own:
                        continue  # no line carries this rate: can't tell what it covers
                    candidates = [t.rate_percent / 100 * sum(own), t.rate_percent / 100 * (sum(own) + charges)]
                elif several:
                    continue  # several rates and no per-line rates: can't tell which lines each covers
                else:
                    candidates = [t.rate_percent / 100 * base, t.rate_percent / 100 * (base + charges)]
                if any(_close(c, t.amount, base) for c in candidates):
                    if "tax_rate" not in passed:
                        passed.append("tax_rate")
                else:
                    issues.append(Issue("arith.tax_rate_mismatch",
                                        f"{t.label or 'Tax'} is printed at {t.rate_percent:g}%, which would be "
                                        f"{candidates[0]:,.2f}, but the amount is {t.amount:,.2f}."))
    return passed, issues
