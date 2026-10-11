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

const STATE_WORDS: Record<string, string> = {
  RECEIVED: 'received', PREPARED: 'prepared', EXTRACTED: 'read', SPLIT: 'read (several invoices)',
  NOT_INVOICE: 'not an invoice', FAILED: 'failed', CHECKED: 'checked', MATCHED: 'compared with the PO and deliveries',
  AUTO_APPROVED: 'approved automatically', NEEDS_REVIEW: 'sent to review', APPROVED: 'approved',
  APPROVED_PARTIAL: 'partly approved', REJECTED: 'rejected', ON_HOLD: 'put on hold',
}
const ACTION_WORDS: Record<string, string> = {
  approve: 'Approved', approve_partial: 'Received part approved', reject: 'Rejected', hold: 'Put on hold',
}

// One audit event in plain words: a title, and an optional detail line.
export function eventText(e: { action: string; data: Record<string, unknown> }): { title: string; detail?: string } {
  const d = e.data as Record<string, string | boolean | string[] | undefined>
  switch (e.action) {
    case 'document.received':
      return { title: 'Received', detail: String(d.filename ?? '') }
    case 'document.state_changed':
    case 'invoice.state_changed':
      return { title: `${e.action.startsWith('document') ? 'Document' : 'Invoice'} ${STATE_WORDS[String(d.to)] ?? d.to}`,
               detail: Array.isArray(d.labels) && d.labels.length ? d.labels.join(', ') : Array.isArray(d.raised) && d.raised.length ? d.raised.join(', ') : undefined }
    case 'field.corrected':
      return { title: `Corrected ${FIELD_LABELS[String(d.field)] ?? d.field}`,
               detail: `${d.from ?? '—'} → ${d.to ?? 'none'}${d.picked_on_page ? ' (picked on the page)' : ''}` }
    case 'invoice.rechecked':
      return { title: 'Checked again', detail: String(d.why ?? '') }
    case 'review.decided':
      return { title: ACTION_WORDS[String(d.action)] ?? String(d.action), detail: String(d.note ?? '') }
    case 'review.released':
      return { title: 'Released back to review', detail: String(d.note ?? '') }
    case 'review.reprocess_requested':
      return { title: 'Asked for a fresh read', detail: String(d.note ?? '') }
    case 'document.reread':
      return { title: 'Read again by the model', detail: d.model ? String(d.model) : undefined }
    case 'payment.planned':
      return { title: d.status === 'planned' ? `Payment planned for ${d.run_date}` : 'Payment waiting',
               detail: `${formatMoney(d.amount, String(d.currency))}${Number(d.discount) ? `, ${formatMoney(d.discount)} discount` : ''}: ${d.reason}` }
    case 'payment_run.executed': {
      const totals = Object.entries((e.data.totals ?? {}) as Record<string, string>).map(([c, v]) => formatMoney(v, c)).join(', ')
      return { title: `Payment run of ${d.run_date} paid`, detail: `${(e.data.transfers as unknown[] | undefined)?.length ?? 0} transfer(s): ${totals || 'nothing'}` }
    }
    case 'invoice.paid':
      return { title: `Paid in the run of ${d.run_date}`, detail: `to account ${d.to}` }
    case 'vendor.added':
      return { title: `Added vendor ${d.name}`, detail: `${d.account ? `account ${d.account}` : 'no bank details'}: ${d.note}` }
    case 'vendor.bank_account_changed':
      return { title: `Bank account changed to ${d.to}`, detail: `was ${Array.isArray(d.from) ? d.from.join(', ') : d.from}: ${d.note}` }
    case 'workspace.today_set':
      return { title: `Today set to ${d.to}`, detail: `was ${d.from}` }
    case 'companion.loaded':
      return { title: 'Vendors, POs and deliveries loaded' }
    case 'run.finished':
      return { title: 'Inbox run finished' }
    default:
      return { title: e.action }
  }
}

export const formatShare = (x: number | null | undefined) => (x == null ? '—' : `${Math.round(x * 100)}%`)
export const formatQty = (v: string | null | undefined) => (v == null ? '—' : String(Number(v)))
