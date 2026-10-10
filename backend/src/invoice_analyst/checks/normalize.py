"""How names, tax IDs, bank accounts and PO numbers are compared (the same rules for the vendor list and invoices)."""

from __future__ import annotations

import re
import unicodedata

# Legal-form words: ignored when comparing names, but two names with different legal forms are different companies
# ('Karoo Hardware CC' is not 'Karoo Hardware (Pty) Ltd')
LEGAL_FORMS = {"pty", "ltd", "limited", "inc", "incorporated", "llc", "llp", "co", "company", "corp", "corporation",
               "gmbh", "ag", "bv", "nv", "sarl", "sas", "sa", "plc", "cc", "pvt"}
FILLER = {"the", "and", "of"}
# spellings of the same legal form
_FORM_ALIASES = {"limited": "ltd", "incorporated": "inc", "corporation": "corp", "company": "co"}


def norm_name(name: str | None) -> str:
    """'Okavango Hardware (Pty) Ltd' -> 'okavango hardware'."""
    if not name:
        return ""
    return " ".join(w for w in _words(name) if w not in LEGAL_FORMS and w not in FILLER)


def _words(name: str) -> list[str]:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).split()


def legal_form(name: str | None) -> frozenset[str]:
    """{'pty', 'ltd'} for 'Okavango Hardware (Pty) Ltd'; empty when none is printed."""
    return frozenset(_FORM_ALIASES.get(w, w) for w in _words(name or "") if w in LEGAL_FORMS)


def same_legal_form(a: str | None, b: str | None) -> bool:
    """Compatible legal forms: equal, or one name doesn't print one."""
    fa, fb = legal_form(a), legal_form(b)
    return not fa or not fb or fa == fb


def tax_digits(value: str | None) -> str:
    """The digits of a tax ID, ignoring labels and country prefixes ('VAT Reg No: 4362204999' -> '4362204999')."""
    return re.sub(r"\D", "", value or "")


def norm_code(value: str | None) -> str:
    """Tax IDs, PO numbers, invoice numbers: letters and digits only, uppercase ('GB 306 3842 07' -> 'GB306384207')."""
    return re.sub(r"[^0-9A-Za-z]", "", value or "").upper()


def norm_account(iban: str | None = None, account: str | None = None, sort_code: str | None = None) -> str | None:
    """One comparable string per bank account: the IBAN, else sort code + account, else the account digits."""
    if iban and norm_code(iban):
        return norm_code(iban)
    digits = re.sub(r"\D", "", account or "")
    if not digits:
        return None
    return re.sub(r"\D", "", sort_code or "") + digits


def last4(account_norm: str | None) -> str:
    return f"…{account_norm[-4:]}" if account_norm else "none"
