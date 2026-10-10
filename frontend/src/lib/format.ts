// Display helpers: field names, money, dates, routes.

export const FIELD_LABELS: Record<string, string> = {
  vendor_name: 'Vendor',
  invoice_number: 'Invoice number',
  invoice_date: 'Invoice date',
  due_date: 'Due date',
  po_number: 'PO number',
  vendor_tax_id: 'Vendor tax ID',
  currency: 'Currency',
  subtotal: 'Subtotal',
  tax: 'Tax',
  total: 'Total',
  line_amount: 'Line amount',
  line_description: 'Line description',
}

export const HEADER_ORDER = [
  'vendor_name',
  'invoice_number',
  'invoice_date',
  'due_date',
  'po_number',
  'vendor_tax_id',
  'currency',
  'subtotal',
  'tax',
  'total',
]

export const MONEY_FIELDS = new Set(['subtotal', 'tax', 'total', 'line_amount'])

export function formatMoney(value: unknown, currency?: string | null): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = typeof value === 'number' ? value : Number(value)
  if (Number.isNaN(n)) return String(value)
  const plain = n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return currency ? `${currency} ${plain}` : plain
}

export function formatValue(field: string, value: unknown, currency?: string | null): string {
  if (value === null || value === undefined || value === '') return '—'
  if (MONEY_FIELDS.has(field)) return formatMoney(value, field === 'line_amount' ? null : currency)
  return String(value)
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export const ROUTE_LABELS: Record<string, string> = {
  text_llm: 'PDF text layer → GLM-5.3',
  image_llm: 'Page images → Muse Glimmer',
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
