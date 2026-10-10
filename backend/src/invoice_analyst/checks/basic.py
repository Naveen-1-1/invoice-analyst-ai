"""Document type and arithmetic: is this a bill, and does it add up?"""

from __future__ import annotations

from .arithmetic import arithmetic_report
from .base import Context, Finding


def doctype(ctx: Context) -> list[Finding]:
    t = ctx.inv.document_type
    out = []
    if t in ("statement", "other"):
        out.append(Finding("doctype.not_invoice", "warn",
                           "This is a statement or other document, not a bill: nothing to pay." if t == "statement"
                           else "This doesn't look like an invoice or credit note: nothing to pay.", {"type": t}))
    if ctx.invoices_in_file > 1:
        out.append(Finding("doctype.multiple_invoices", "fail",
                           f"The file holds {ctx.invoices_in_file} separate invoices; each is handled on its own, "
                           "but a person should confirm the split.", {"invoices": ctx.invoices_in_file}))
    if t == "credit_note":
        out.append(Finding("doctype.credit_note", "warn",
                           "Credit note: it reduces what we owe this vendor instead of being paid.",
                           {"refers_to": ctx.inv.credit_note_reference}))
    return out


def arithmetic(ctx: Context) -> list[Finding]:
    passed, issues = arithmetic_report(ctx.inv)
    if issues:
        return [Finding(i.code, "fail", i.message) for i in issues]
    if passed:
        return [Finding("arith.consistent", "pass", "The lines, subtotal, tax and total add up.", {"checks": passed})]
    return [Finding("arith.consistent", "skip", "Not enough figures printed to check the arithmetic.")]
