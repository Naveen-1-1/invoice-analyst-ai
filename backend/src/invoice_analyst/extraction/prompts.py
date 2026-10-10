"""Extraction prompts. PROMPT_HASH (prompt text + template schema) is part of every LLM cache key, so changing
either one makes the next run call the model again instead of reusing old answers."""

from __future__ import annotations

import hashlib
import json

from .schemas import SCHEMA_VERSION, FileExtraction

SYSTEM = """You read supplier documents for an accounts-payable team and record them by calling the provided tool.

Report what is printed. Do not correct the document: if the lines, subtotal, tax or total do not add up, report the
printed numbers anyway (the team checks the arithmetic separately). Fill in every field of the template; use null or
an empty list only when nothing is printed for it.

Document type
- invoice: a bill to pay. credit_note: titled credit note / credit memo / nota de credito, or a negative total.
- statement: a statement of account listing several invoices and a balance; it is not a bill. other: anything else.
- If one file contains several separate invoices (different invoice numbers), return one entry per invoice, in order,
  with first_page and last_page.

Numbers and the invoice number
- invoice_number is the document's own number. Never use a PO, order, delivery note, customer reference or supplier
  reference number instead. If no invoice number is printed, leave it empty. For a credit note use the credit note's
  number; put the invoice it refers to in credit_note_reference.
- Amounts are plain positive numbers with '.' as the decimal point. Read the document's own number format
  (1.234,56 and 1 234,56 both mean 1234.56). Amounts in brackets, (1,234.56), are negative: report the magnitude.
  Report credit-note amounts as positive numbers.

Currency
- currency is the ISO 4217 code of the line items and totals. When the symbol is ambiguous, use the vendor's country:
  '$' or 'N$' on a Namibian invoice is NAD, 'R' is ZAR, 'K' in Zambia is ZMW, '£' is GBP, '€' is EUR.
- Some invoices list lines in one currency and show a separate amount-payable box in another currency (with an
  exchange rate). Keep currency/subtotal/total for the line currency and put the payable box in amount_payable.

Lines, charges and totals
- lines are the rows of the item table only. Do not include subtotal, tax or total rows, page subtotals, or
  'carried forward' / 'brought forward' rows.
- Every line needs its amount: the value in the line-total (Amount) column. Some rows show a base value and a
  percentage instead of quantity x unit price (a fuel surcharge, a levy); the amount is still the line-total column.
  Also record quantity, unit price, item code and VAT rate when printed.
- Shipping, freight, handling, fees and discounts printed in the totals area are charges, not lines.
- subtotal is the net amount before tax and before charges (often 'Subtotal', 'Total Exclusive', 'Total excl VAT').
  A 'Sub Total' printed after the VAT is not the subtotal. If only VAT-inclusive amounts are printed, leave subtotal
  empty and set prices_include_tax.
- total is the invoice total including tax and before any withholding deduction; put withholding in
  withholding_amount (a 'Net Amount Due' after withholding is not the total).
- tax_lines lists every printed tax amount (VAT, sales tax), even 0.00.

Dates
- Give invoice_date and due_date exactly as printed in text and as ISO in value. If a numeric date could be read
  either way (03/04/2026), decide from the vendor's country and from other dates on the document.
- If no due date is printed, leave it empty (do not compute one from the terms).

Stamps and handwriting
- Stamps (PAID, APPROVED, RECEIVED) and handwritten notes go in annotations. They never replace printed values: a
  handwritten amount next to the total is not the total.

references: shipment and transport numbers (container, trip, load, waybill, truck registration, file reference).
For every field, page is the 1-based page where you read it."""

USER_TEXT = "Record this document. Its text layer follows, page by page.\n\n{text}"
USER_IMAGE = "Record this document. {pages}"
# Versioned separately (extract.OCR_HINT_VERSION in the cache key), so using it never changes PROMPT_HASH
OCR_HINT = ("\n\nText found on the page by OCR, in reading order. It may contain errors; the image is "
            "authoritative.\n{ocr}")

PROMPT_VERSION = "extract-2"
PROMPT_HASH = hashlib.sha256(json.dumps({
    "version": PROMPT_VERSION, "system": SYSTEM, "user_text": USER_TEXT, "user_image": USER_IMAGE,
    "schema_version": SCHEMA_VERSION, "schema": FileExtraction.model_json_schema(),
}, sort_keys=True).encode()).hexdigest()[:16]
