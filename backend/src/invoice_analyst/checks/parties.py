"""Who sent it and who it's for: vendor list, vendor tax ID, bank account on file, and the addressee."""

from __future__ import annotations

from rapidfuzz import fuzz
from sqlalchemy import select

from ..db.models import Entity, Vendor, VendorBankAccount
from .base import Context, Finding
from .normalize import last4, norm_account, norm_name, same_legal_form, tax_digits

NAME_MATCH_MIN = 90  # rapidfuzz token_sort_ratio on names without legal-form words


def _best(name: str, candidates: list, key, full_name) -> tuple[object | None, float]:
    """The candidate whose normalised name is closest to `name`, if close enough and of a compatible legal form."""
    target = norm_name(name)
    best, score = None, 0.0
    if not target:
        return None, 0.0
    for c in candidates:
        if not same_legal_form(name, full_name(c)):
            continue
        sc = fuzz.token_sort_ratio(target, key(c))
        if sc > score:
            best, score = c, sc
    return (best, score) if score >= NAME_MATCH_MIN else (None, score)


def vendor(ctx: Context) -> list[Finding]:
    name, tax_id = ctx.inv.vendor.name.text, ctx.inv.vendor.tax_id.text
    vendors = ctx.s.scalars(select(Vendor).where(Vendor.workspace_id == ctx.workspace_id)).all()
    tax = tax_digits(tax_id)
    by_tax = next((v for v in vendors if len(tax) >= 5 and tax_digits(v.tax_id) == tax), None)
    if by_tax is not None:
        ctx.vendor, how, score = by_tax, "tax ID", 100.0
    else:
        found, score = _best(name or "", vendors, lambda v: v.name_norm, lambda v: v.name)
        ctx.vendor, how = found, "name"  # type: ignore[assignment]
    if ctx.vendor is None:
        return [Finding("vendor.unknown", "fail", f"{name or 'The vendor'} is not in the vendor list. Add the vendor "
                                                  "(after checking who they are) or reject the invoice.",
                        {"printed_name": name, "printed_tax_id": tax_id, "closest_score": round(score, 1)})]
    v = ctx.vendor
    out = [Finding("vendor.matched", "pass", f"Vendor {v.code} {v.name} (matched by {how}).",
                   {"vendor_code": v.code, "by": how, "score": round(score, 1)})]
    if v.status == "blocked":
        out.append(Finding("vendor.blocked", "fail", f"{v.name} is blocked in the vendor list.", {"vendor_code": v.code}))
    digits_printed, digits_master = tax_digits(tax_id), tax_digits(v.tax_id)
    if how == "name" and digits_printed and digits_master and digits_printed != digits_master:
        out.append(Finding("vendor.tax_id_mismatch", "fail",
                           f"The tax ID printed ({tax_id}) differs from the one on file for {v.name} ({v.tax_id}).",
                           {"printed": tax_id, "on_file": v.tax_id}))
    return out


def bank(ctx: Context) -> list[Finding]:
    if ctx.vendor is None:
        return [Finding("bank.changed", "skip", "Vendor unknown, so there is no bank account on file to compare.")]
    printed = ctx.inv.vendor.bank
    acct = norm_account(printed.iban, printed.account_number, printed.sort_code) if printed else None
    on_file = ctx.s.scalars(select(VendorBankAccount).where(VendorBankAccount.vendor_id == ctx.vendor.id)).all()
    if acct is None:
        return [Finding("bank.on_file", "pass", "No bank details printed; payment goes to the account on file "
                                                f"({last4(on_file[0].account_norm) if on_file else 'none on file'}).")]
    if any(a.account_norm == acct for a in on_file):
        return [Finding("bank.on_file", "pass", f"The bank account printed ({last4(acct)}) is the one on file.")]
    return [Finding("bank.changed", "fail",
                    f"The bank account printed ({last4(acct)}) is not the one on file "
                    f"({', '.join(last4(a.account_norm) for a in on_file) or 'none'}). Confirm the change with the "
                    "vendor by phone, using a number from the vendor list, before paying.",
                    {"printed": last4(acct), "on_file": [last4(a.account_norm) for a in on_file]})]


def addressee(ctx: Context) -> list[Finding]:
    name = ctx.inv.bill_to.name.text
    entities = ctx.s.scalars(select(Entity).where(Entity.workspace_id == ctx.workspace_id)).all()
    found, score = _best(name or "", entities, lambda e: norm_name(e.name), lambda e: e.name)
    if found is None:
        return [Finding("pattern.wrong_addressee", "fail",
                        f"The invoice is addressed to {name or 'nobody we can read'}, which is not one of our "
                        "companies.", {"bill_to": name, "closest_score": round(score, 1)})]
    ctx.entity = found  # type: ignore[assignment]
    return [Finding("pattern.addressee_ok", "pass", f"Addressed to {found.name}, one of our companies.")]  # type: ignore[union-attr]
