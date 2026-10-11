import { Link, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { useGetDocument } from '@/api/generated'
import { CheckList } from '@/components/CheckList'
import { FieldList } from '@/components/FieldList'
import { ModelCalls } from '@/components/ModelCalls'
import { PageViewer } from '@/components/PageViewer'
import { StepTimeline } from '@/components/StepTimeline'
import { TierBadge } from '@/components/TierBadge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ROUTE_LABELS, errorText, eventText, formatDateTime, formatMoney } from '@/lib/format'
import { STATE_LABELS } from '@/lib/review'

export function DocumentPage() {
  const { documentId } = useParams({ from: '/documents/$documentId' })
  const doc = useGetDocument(Number(documentId))
  const [hoveredId, setHoveredId] = useState<number | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [invoiceIdx, setInvoiceIdx] = useState(0)

  if (doc.isPending) return <Skeleton className="h-96 w-full" />
  if (doc.isError) return <p className="text-sm text-red-700">Couldn't load the document: {errorText(doc.error)}</p>
  const d = doc.data
  const invoice = d.invoices[invoiceIdx]
  const fields = invoice?.fields ?? []
  const cacheHit = d.events.some((e) => e.data.cache_hit === true)

  const select = (id: number) => {
    setSelectedId(id)
    document.getElementById(`box-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm text-muted-foreground">
          <Link to="/" className="hover:underline">Runs</Link> / Document {d.id}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold">{d.bench_doc_id ?? d.filename}</h1>
          {invoice?.vendor_id != null && (
            <Link to="/vendor/$vendorId" params={{ vendorId: String(invoice.vendor_id) }} className="text-sm hover:underline">{invoice.vendor_name}</Link>
          )}
          {invoice && <TierBadge tier={invoice.conf_tier} />}
          {invoice && (
            <span className="text-sm text-muted-foreground">
              {STATE_LABELS[invoice.state] ?? invoice.state}
              {['NEEDS_REVIEW', 'ON_HOLD', 'APPROVED_PARTIAL'].includes(invoice.state) && (
                <>
                  {' · '}
                  <Link to="/review/$invoiceId" params={{ invoiceId: String(invoice.id) }} className="text-foreground underline-offset-4 hover:underline">
                    open in review
                  </Link>
                </>
              )}
            </span>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          {ROUTE_LABELS[d.route ?? ''] ?? d.route} · {d.pages.length} page{d.pages.length === 1 ? '' : 's'} · state{' '}
          {d.state} · {d.llm_calls > 0
            ? `${d.llm_calls} model call${d.llm_calls === 1 ? '' : 's'}`
            : cacheHit ? 'answer reused from an earlier identical call' : 'no model calls'} ·{' '}
          <a href={d.file_url} className="underline-offset-4 hover:underline">original file</a>
        </p>
      </div>

      <div className="rounded-md border px-3 py-2.5">
        <StepTimeline events={d.events} invoiceId={invoice?.id} />
      </div>

      {d.invoices.length > 1 && (
        <div className="flex gap-1" role="group" aria-label="Invoices in this file">
          {d.invoices.map((inv, i) => (
            <button key={inv.id} type="button" onClick={() => setInvoiceIdx(i)} aria-pressed={i === invoiceIdx}
                    className={`rounded-md border px-2.5 py-1 text-xs ${i === invoiceIdx ? 'bg-primary text-primary-foreground' : ''}`}>
              Invoice {i + 1}: {inv.invoice_number ?? 'no number'}
            </button>
          ))}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <div className="lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
          <PageViewer pages={d.pages} fields={fields} hoveredId={hoveredId} onHover={setHoveredId} onSelect={select} />
        </div>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">What was read</CardTitle>
            <p className="text-sm text-muted-foreground">
              Hover a value to see where it is on the page; click it for the evidence behind its confidence.
            </p>
          </CardHeader>
          <CardContent>
            <Tabs defaultValue="fields">
              <TabsList>
                <TabsTrigger value="fields">Fields</TabsTrigger>
                <TabsTrigger value="checks">
                  Checks{invoice ? ` (${invoice.checks.filter((c) => c.outcome === 'fail' || c.outcome === 'warn').length})` : ''}
                </TabsTrigger>
                <TabsTrigger value="money">Payment{invoice?.payments.length ? ` (${invoice.payments.length})` : ''}</TabsTrigger>
                <TabsTrigger value="calls">Model calls</TabsTrigger>
                <TabsTrigger value="trail">Audit trail ({d.events.length})</TabsTrigger>
              </TabsList>
              <TabsContent value="fields" className="pt-2">
                {invoice ? (
                  <FieldList fields={fields} currency={invoice.currency} hoveredId={hoveredId} selectedId={selectedId}
                             onHover={setHoveredId} onSelect={select} />
                ) : (
                  <p className="text-sm text-muted-foreground">Nothing was extracted from this document.</p>
                )}
              </TabsContent>
              <TabsContent value="checks" className="pt-2">
                <CheckList checks={invoice?.checks ?? []} />
              </TabsContent>
              <TabsContent value="money" className="space-y-4 pt-2 text-sm">
                {!invoice?.payments.length && <p className="text-muted-foreground">Not approved, so nothing is booked or paid.</p>}
                {invoice?.payments.map((p) => (
                  <div key={p.id} className="rounded-md border p-2.5">
                    <div className="font-medium">
                      {p.status === 'paid' ? `Paid in the run of ${p.run_date}` : p.status === 'planned' ? `Planned for the run of ${p.run_date}` : 'Waiting'}
                      {': '}{formatMoney(p.paid, p.currency)}{Number(p.discount) ? ` (${formatMoney(p.discount)} discount)` : ''}
                      {p.to_account && <span className="font-normal text-muted-foreground"> to account {p.to_account}</span>}
                    </div>
                    <div className="text-muted-foreground">{p.reason}</div>
                  </div>
                ))}
                {invoice && invoice.entries.length > 0 && (
                  <section>
                    <h3 className="mb-1 font-semibold">Ledger entries</h3>
                    <ul className="space-y-2">
                      {invoice.entries.map((e) => (
                        <li key={e.id} className="border-l-2 pl-3">
                          <div className="font-medium">{e.entry_date} · {e.kind.replace('_', ' ')}</div>
                          <ul className="text-xs">
                            {e.lines.map((ln, k) => (
                              <li key={k} className="flex justify-between gap-3">
                                <span className={Number(ln.credit) > 0 ? 'pl-6' : ''}>{ln.account} {ln.account_name}</span>
                                <span className="tabular-nums">{Number(ln.debit) > 0 ? `Dr ${formatMoney(ln.debit, ln.currency)}` : `Cr ${formatMoney(ln.credit, ln.currency)}`}</span>
                              </li>
                            ))}
                          </ul>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
              </TabsContent>
              <TabsContent value="calls" className="pt-2">
                <ModelCalls documentId={d.id} />
              </TabsContent>
              <TabsContent value="trail" className="pt-2">
                <ol className="space-y-2 text-sm">
                  {d.events.map((e) => {
                    const { title, detail } = eventText(e)
                    return (
                      <li key={e.seq} className={`border-l-2 pl-3 ${e.actor === 'user' ? 'border-sky-400' : ''}`}>
                        <div className="font-medium">{title}</div>
                        {detail && <div className="text-muted-foreground">{detail}</div>}
                        <div className="text-xs text-muted-foreground">
                          {formatDateTime(e.occurred_at)} · {e.actor === 'user' ? 'you' : e.actor === 'llm' ? 'the model' : 'the system'}
                        </div>
                      </li>
                    )
                  })}
                </ol>
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
