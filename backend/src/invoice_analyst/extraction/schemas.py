"""The common invoice template. The models fill it in through a forced tool call (decision 21).

Header fields carry both the text exactly as printed and the model's normalised value, so deterministic code can
re-check the value (and, from M3, find the text on the page). The template reports what is *printed*; benchmark
conventions (net subtotal on VAT-inclusive invoices, the amount-payable box on dual-currency invoices, ...) are
applied afterwards in `predictions.py`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

SCHEMA_VERSION = "2"


def _all_keys_required(schema: dict[str, Any]) -> None:
    # The model is shown every key as required (values may still be null): left optional, it skips whole
    # sections. Parsing stays lenient through the defaults, so a stored answer always loads.
    if "properties" in schema:
        schema["required"] = list(schema["properties"])


class Template(BaseModel):
    model_config = ConfigDict(json_schema_extra=_all_keys_required)


STRICT = {"strict": True}  # validation context for live calls: incomplete answers get one retry


class PrintedText(Template):
    text: str | None = Field(None, description="Exactly as printed, including any label-free punctuation")
    page: int | None = Field(None, description="1-based page number where it is printed")


class PrintedDate(PrintedText):
    value: str | None = Field(None, description="The same date as ISO YYYY-MM-DD")


class PrintedAmount(PrintedText):
    value: float | None = Field(None, description="The same amount as a plain number with '.' decimals, no symbols")


class BankDetails(Template):
    text: str | None = Field(None, description="The banking-details text exactly as printed")
    bank_name: str | None = None
    iban: str | None = None
    account_number: str | None = None
    sort_code: str | None = None
    branch_code: str | None = None
    swift_bic: str | None = None


class Party(Template):
    name: PrintedText = Field(default_factory=PrintedText)
    address: str | None = None
    country: str | None = Field(None, description="ISO 3166 alpha-2 country code, from the address or tax label")
    tax_id: PrintedText = Field(default_factory=PrintedText,
                                description="VAT / tax registration number (EIN, VAT No, TPIN, USt-IdNr, ...)")


class Vendor(Party):
    email: str | None = None
    phone: str | None = None
    bank: BankDetails | None = None


class TaxLine(Template):
    label: str | None = Field(None, description="As printed, e.g. 'VAT 15%'")
    rate_percent: float | None = None
    amount: float | None = None


class Charge(Template):
    """A totals-block line that is not an item: shipping, freight, fees, discounts."""
    description: str
    amount: float = Field(description="Positive number; use kind to say it is a discount")
    kind: Literal["shipping", "fee", "discount", "other"] = "other"


class LineItem(Template):
    description: str
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = Field(None, description="The line total as printed")
    item_code: str | None = None
    vat_rate_percent: float | None = None
    page: int | None = None


class AmountPayable(Template):
    """A separate amount-payable box in another currency (dual-currency invoices)."""
    currency: str = Field(description="ISO 4217 code of the payable amount")
    amount: float
    tax_amount: float | None = Field(None, description="Tax shown in the payable currency, if any")
    text: str | None = None


class InvoiceDoc(Template):
    document_type: Literal["invoice", "credit_note", "statement", "other"]
    vendor: Vendor = Field(default_factory=Vendor, description="Who issued the document (the seller)")
    bill_to: Party = Field(default_factory=Party, description="Who it is addressed to (the buyer)")
    invoice_number: PrintedText = Field(default_factory=PrintedText)
    invoice_date: PrintedDate = Field(default_factory=PrintedDate)
    due_date: PrintedDate = Field(default_factory=PrintedDate)
    po_number: PrintedText = Field(default_factory=PrintedText)
    payment_terms: PrintedText = Field(default_factory=PrintedText)
    currency: str | None = Field(None, description="ISO 4217 code of the line items and totals")
    currency_symbol: str | None = Field(None, description="The symbol or code printed with amounts, e.g. 'N$', '$'")
    prices_include_tax: bool = Field(False, description="True if line amounts already include VAT/tax")
    lines: list[LineItem] = Field(default_factory=list)
    charges: list[Charge] = Field(default_factory=list)
    subtotal: PrintedAmount = Field(default_factory=PrintedAmount,
                                    description="Net amount before tax and before charges, as printed")
    tax_lines: list[TaxLine] = Field(default_factory=list)
    total: PrintedAmount = Field(default_factory=PrintedAmount,
                                 description="Invoice total including tax, before any withholding deduction")
    withholding_amount: float | None = None
    amount_payable: AmountPayable | None = None
    credit_note_reference: str | None = Field(None, description="Invoice number a credit note refers to")
    references: list[str] = Field(default_factory=list,
                                  description="Shipment/transport references: container, trip, load, waybill, "
                                              "truck registration, file reference numbers")
    annotations: list[str] = Field(default_factory=list,
                                   description="Stamps and handwritten notes, e.g. 'PAID', 'Approved JB'")
    first_page: int | None = None
    last_page: int | None = None


class FileExtraction(Template):
    """Everything in one uploaded file: usually one invoice; several if the file holds more than one."""
    invoices: list[InvoiceDoc] = Field(min_length=1)

    @model_validator(mode="after")
    def _complete(self, info: ValidationInfo) -> FileExtraction:
        """Live calls only (context STRICT): ask again if a bill came back without its total or line amounts."""
        if not (info.context or {}).get("strict"):
            return self
        problems = []
        for n, inv in enumerate(self.invoices, 1):
            if inv.document_type not in ("invoice", "credit_note"):
                continue
            where = f"invoice {n}" if len(self.invoices) > 1 else "the document"
            if inv.total.value is None:
                problems.append(f"{where}: total is missing; read it from the totals block")
            if not inv.lines:
                problems.append(f"{where}: no line items were recorded; read every row of the item table")
            missing = [ln.description for ln in inv.lines if ln.amount is None]
            if missing:
                problems.append(f"{where}: these lines have no amount (use the line-total column): "
                                + "; ".join(repr(d[:40]) for d in missing[:5]))
        if problems:
            raise ValueError("Incomplete record. " + " ".join(problems))
        return self
