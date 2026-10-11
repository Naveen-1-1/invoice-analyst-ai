import { Link, useParams } from '@tanstack/react-router'
import { useGetPurchaseOrder, useGetVendor } from '@/api/generated'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { errorText, formatMoney, formatQty, formatShare } from '@/lib/format'
import { STATE_LABELS } from '@/lib/review'

const share = formatShare
const qty = formatQty
const PAYMENT: Record<string, string> = { planned: 'to pay', waiting: 'payment waiting', paid: 'paid' }

function InvoiceLink({ invoiceId, documentId, state, label }: { invoiceId: number; documentId: number; state: string; label: string }) {
  return ['NEEDS_REVIEW', 'ON_HOLD', 'APPROVED_PARTIAL'].includes(state)
    ? <Link to="/review/$invoiceId" params={{ invoiceId: String(invoiceId) }} className="hover:underline">{label}</Link>
    : <Link to="/documents/$documentId" params={{ documentId: String(documentId) }} className="hover:underline">{label}</Link>
}

export function VendorPage() {
  const { vendorId } = useParams({ from: '/vendor/$vendorId' })
  const vendor = useGetVendor(Number(vendorId))
  if (vendor.isPending) return <Skeleton className="h-96 w-full" />
  if (vendor.isError) return <p className="text-sm text-red-700">Couldn't load the vendor: {errorText(vendor.error)}</p>
  const v = vendor.data
  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm text-muted-foreground">
          <Link to="/vendors/$workspaceId" params={{ workspaceId: String(v.workspace_id) }} className="hover:underline">Vendors</Link> / {v.code}
        </p>
        <h1 className="text-xl font-semibold">{v.name}</h1>
        <p className="text-sm text-muted-foreground">
          {[v.country, v.tax_id && `tax ID ${v.tax_id}`, v.terms_days != null && `${v.terms_days} days`, v.discount,
            v.default_gl && `expense account ${v.default_gl}`, v.origin === 'review' ? 'added in review' : null,
            v.status !== 'active' && v.status].filter(Boolean).join(' · ')}
        </p>
        {v.address && <p className="text-sm text-muted-foreground">{v.address}{v.email && ` · ${v.email}`}</p>}
        {v.aliases.length > 0 && <p className="text-xs text-muted-foreground">Also known as {v.aliases.join(', ')}</p>}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-base">Bank accounts</CardTitle></CardHeader>
          <CardContent>
            {v.accounts.length === 0 ? <p className="text-sm text-muted-foreground">None on file: this vendor can't be paid yet.</p> : (
              <Table>
                <TableHeader><TableRow><TableHead>Account</TableHead><TableHead>Bank</TableHead><TableHead>In use</TableHead><TableHead>From</TableHead></TableRow></TableHeader>
                <TableBody>
                  {v.accounts.map((a, k) => (
                    <TableRow key={k}>
                      <TableCell className="font-mono text-xs">{a.account} <span className="font-sans text-muted-foreground">{a.kind.replace('_', ' ')}</span></TableCell>
                      <TableCell className="whitespace-normal text-xs">{[a.bank_name, a.bic, a.branch].filter(Boolean).join(' · ') || '—'}</TableCell>
                      <TableCell>{a.valid_to ? <span className="text-muted-foreground">replaced {a.valid_to}</span> : 'yes: payments go here'}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{a.source?.startsWith('review') ? a.source.replace('review of invoice', 'confirmed in review of invoice') : 'vendor list'}{a.valid_from ? `, ${a.valid_from}` : ''}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Invoices received ({v.invoices.length})</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <TableHeader><TableRow><TableHead>Invoice</TableHead><TableHead>Date</TableHead><TableHead className="text-right">Total</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
              <TableBody>
                {v.invoices.map((i) => (
                  <TableRow key={i.invoice_id}>
                    <TableCell><InvoiceLink invoiceId={i.invoice_id} documentId={i.document_id} state={i.state} label={i.invoice_number ?? 'no number'} /></TableCell>
                    <TableCell className="tabular-nums">{i.invoice_date ?? '—'}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatMoney(i.total, i.currency)}</TableCell>
                    <TableCell>{STATE_LABELS[i.state] ?? i.state}{i.payment && <span className="text-muted-foreground"> · {PAYMENT[i.payment]}</span>}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Purchase orders ({v.purchase_orders.length})</CardTitle></CardHeader>
          <CardContent>
            {v.purchase_orders.length === 0 ? <p className="text-sm text-muted-foreground">None.</p> : (
              <Table>
                <TableHeader><TableRow><TableHead>PO</TableHead><TableHead className="text-right">Ordered</TableHead><TableHead className="text-right">Received</TableHead><TableHead className="text-right">Invoiced</TableHead></TableRow></TableHeader>
                <TableBody>
                  {v.purchase_orders.map((p) => (
                    <TableRow key={p.id}>
                      <TableCell><Link to="/purchase-order/$poId" params={{ poId: String(p.id) }} className="hover:underline">{p.po_number}</Link></TableCell>
                      <TableCell className="text-right tabular-nums">{formatMoney(p.ordered, p.currency)}</TableCell>
                      <TableCell className="text-right tabular-nums">{share(p.received_share)}</TableCell>
                      <TableCell className="text-right tabular-nums">{share(p.invoiced_share)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Past invoices ({v.history.length})</CardTitle></CardHeader>
          <CardContent>
            <p className="mb-2 text-xs text-muted-foreground">Used by the "unusual amount" check: more than 3× the median of these (with at least 3) needs review.</p>
            <Table>
              <TableHeader><TableRow><TableHead>Invoice</TableHead><TableHead>Date</TableHead><TableHead className="text-right">Amount</TableHead></TableRow></TableHeader>
              <TableBody>
                {v.history.map((h, k) => (
                  <TableRow key={k}>
                    <TableCell>{h.invoice_number ?? '—'}</TableCell>
                    <TableCell className="tabular-nums">{h.invoice_date}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatMoney(h.amount, h.currency)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

export function PurchaseOrderPage() {
  const { poId } = useParams({ from: '/purchase-order/$poId' })
  const po = useGetPurchaseOrder(Number(poId))
  if (po.isPending) return <Skeleton className="h-96 w-full" />
  if (po.isError) return <p className="text-sm text-red-700">Couldn't load the PO: {errorText(po.error)}</p>
  const { po: p, lines, receipts, invoices } = po.data
  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm text-muted-foreground">
          {p.vendor_id != null
            ? <Link to="/vendor/$vendorId" params={{ vendorId: String(p.vendor_id) }} className="hover:underline">{p.vendor}</Link>
            : 'no vendor'} / purchase order
        </p>
        <h1 className="text-xl font-semibold">PO {p.po_number}</h1>
        <p className="text-sm text-muted-foreground">
          {[p.entity && `for ${p.entity}`, p.order_date && `ordered ${p.order_date}`, formatMoney(p.ordered, p.currency), p.status].filter(Boolean).join(' · ')}
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Lines</CardTitle>
          <p className="text-sm text-muted-foreground">
            Received comes from the deliveries; invoiced is what invoices have used up (rejected ones don't count), so
            the same delivery can't be paid twice. Open = received and not yet invoiced.
          </p>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8">#</TableHead>
                <TableHead>Item</TableHead>
                <TableHead className="text-right">Price</TableHead>
                <TableHead className="text-right">Ordered</TableHead>
                <TableHead className="text-right">Received</TableHead>
                <TableHead className="text-right">Invoiced</TableHead>
                <TableHead className="text-right">Open</TableHead>
                <TableHead>Account</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {lines.map((ln) => (
                <TableRow key={ln.line_no}>
                  <TableCell className="text-muted-foreground">{ln.line_no}</TableCell>
                  <TableCell className="max-w-72 whitespace-normal">{ln.description ?? '—'}{ln.item_code && <span className="text-xs text-muted-foreground"> · {ln.item_code}</span>}</TableCell>
                  <TableCell className="text-right tabular-nums">{ln.qty == null ? `${formatMoney(ln.amount)} (service)` : formatMoney(ln.unit_price)}</TableCell>
                  <TableCell className="text-right tabular-nums">{qty(ln.qty)}</TableCell>
                  <TableCell className="text-right tabular-nums">{qty(ln.received)}</TableCell>
                  <TableCell className="text-right tabular-nums">{qty(ln.invoiced)}</TableCell>
                  <TableCell className="text-right tabular-nums">{qty(ln.open)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{ln.gl_account ?? 'vendor default'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-base">Deliveries ({receipts.length})</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm">
            {receipts.length === 0 && <p className="text-muted-foreground">Nothing received yet.</p>}
            {receipts.map((r) => (
              <div key={r.id}>
                <div className="font-medium">{r.grn_number} <span className="font-normal text-muted-foreground">received {r.received_date}</span></div>
                <ul className="text-xs text-muted-foreground">{r.lines.map((l, k) => <li key={k}>line {l.line_no}, {l.description}: {qty(l.qty)}</li>)}</ul>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Invoices against this PO ({invoices.length})</CardTitle></CardHeader>
          <CardContent>
            {invoices.length === 0 ? <p className="text-sm text-muted-foreground">None yet.</p> : (
              <ul className="space-y-1 text-sm">
                {invoices.map((i) => (
                  <li key={i.invoice_id} className="flex justify-between gap-3">
                    <InvoiceLink invoiceId={i.invoice_id} documentId={i.document_id} state={i.state} label={i.invoice_number ?? 'no number'} />
                    <span className="text-muted-foreground">{formatMoney(i.total, i.currency)} · {STATE_LABELS[i.state] ?? i.state}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
