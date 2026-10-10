import { useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import type { PaymentItemOut, PaymentRunOut } from '@/api/generated'
import { useGetPaymentRun, useListPayments, useRunDuePayments, useRunNextPayment, useSetToday } from '@/api/generated'
import { WorkspacePicker, useWorkspace } from '@/components/WorkspacePicker'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { errorText, formatMoney } from '@/lib/format'
import { cn } from '@/lib/utils'

const totals = (t: Record<string, string>) =>
  Object.entries(t).map(([ccy, v]) => formatMoney(v, ccy)).join(' · ') || '—'

const weekday = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })

function ItemRows({ items }: { items: PaymentItemOut[] }) {
  return (
    <>
      {items.map((i) => (
        <TableRow key={i.id}>
          <TableCell className="max-w-56 whitespace-normal">
            <Link to="/documents/$documentId" params={{ documentId: String(i.document_id) }} className="hover:underline">
              {i.invoice_number ?? 'no number'}
            </Link>
            {i.doc_type === 'credit_note' && <span className="ml-1 text-xs text-muted-foreground">credit note</span>}
            <div className="text-xs text-muted-foreground">{i.entity}</div>
          </TableCell>
          <TableCell className="text-right tabular-nums">{formatMoney(i.amount, i.currency)}</TableCell>
          <TableCell className="text-right tabular-nums">{Number(i.discount) ? formatMoney(i.discount) : '—'}</TableCell>
          <TableCell className="max-w-96 text-xs whitespace-normal text-muted-foreground">{i.reason}</TableCell>
        </TableRow>
      ))}
    </>
  )
}

function RunDetail({ runId }: { runId: number }) {
  const run = useGetPaymentRun(runId)
  if (run.isPending) return <Skeleton className="h-40 w-full" />
  if (run.isError) return <p className="text-sm text-red-700">Couldn't load the run: {errorText(run.error)}</p>
  const r = run.data
  return (
    <div className="space-y-3">
      <p className="text-sm">
        <strong>{weekday(r.run.run_date)}</strong> · {r.run.status === 'executed' ? 'paid' : 'planned'} ·{' '}
        {r.transfers.length} transfer{r.transfers.length === 1 ? '' : 's'} · {totals(r.run.totals)}
        {Object.keys(r.run.discounts).length > 0 && <> · discounts taken {totals(r.run.discounts)}</>}
      </p>
      {r.transfers.map((t, k) => (
        <div key={k} className="rounded-md border">
          <div className="flex flex-wrap items-baseline justify-between gap-2 border-b bg-muted/40 px-3 py-2 text-sm">
            <span className="font-medium">{t.vendor ?? 'Unknown vendor'}</span>
            <span>
              <span className="font-medium tabular-nums">{formatMoney(t.amount, t.currency)}</span>
              <span className="text-muted-foreground"> to account {t.to_account ?? 'none on file'}</span>
            </span>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Invoice</TableHead>
                <TableHead className="text-right">Owed</TableHead>
                <TableHead className="text-right">Discount</TableHead>
                <TableHead>Why this run</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody><ItemRows items={t.items} /></TableBody>
          </Table>
        </div>
      ))}
    </div>
  )
}

function RunList({ runs, today, selected, onSelect }: {
  runs: PaymentRunOut[]; today: string; selected: number | null; onSelect: (id: number) => void
}) {
  return (
    <ol className="divide-y rounded-md border text-sm">
      {runs.map((r) => {
        const due = r.status === 'planned' && r.run_date <= today
        return (
          <li key={r.id}>
            <button
              type="button"
              onClick={() => onSelect(r.id)}
              aria-pressed={selected === r.id}
              className={cn('flex w-full flex-wrap items-baseline justify-between gap-2 px-3 py-2 text-left hover:bg-accent',
                selected === r.id && 'bg-accent')}
            >
              <span>
                <span className="font-medium tabular-nums">{weekday(r.run_date)}</span>{' '}
                <span className={cn('rounded border px-1 text-xs', r.status === 'executed'
                  ? 'border-emerald-300 bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200'
                  : due ? 'border-amber-300 bg-amber-50 text-amber-900 dark:bg-amber-950 dark:text-amber-200' : '')}>
                  {r.status === 'executed' ? 'paid' : due ? 'due' : 'planned'}
                </span>
              </span>
              <span className="text-muted-foreground tabular-nums">
                {r.items} item{r.items === 1 ? '' : 's'} · {totals(r.totals)}
              </span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

export function PaymentsPage() {
  const workspaceId = useWorkspace()
  const qc = useQueryClient()
  const payments = useListPayments(workspaceId ?? 0, { query: { enabled: workspaceId != null, refetchInterval: 10_000 } })
  const [selected, setSelected] = useState<number | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [date, setDate] = useState('')
  const refresh = (text: string) => {
    setMessage({ ok: true, text })
    qc.invalidateQueries({ predicate: (q) => /^\/api\/(workspaces|payment-runs|documents)/.test(String(q.queryKey[0])) })
  }
  const failed = (e: unknown) => setMessage({ ok: false, text: errorText(e) })
  const next = useRunNextPayment({ mutation: { onSuccess: (r) => refresh(r.message), onError: failed } })
  const due = useRunDuePayments({ mutation: { onSuccess: (r) => refresh(r.message), onError: failed } })
  const setToday = useSetToday({ mutation: { onSuccess: (t) => refresh(`Today is now ${t.date}.`), onError: failed } })
  const busy = next.isPending || due.isPending || setToday.isPending

  const p = payments.data
  const runs = p?.runs ?? []
  const planned = runs.filter((r) => r.status === 'planned')
  const current = selected ?? planned[0]?.id ?? runs[runs.length - 1]?.id ?? null
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Payments</h1>
          <p className="text-sm text-muted-foreground">
            Approved invoices are paid in weekly runs on Thursday: the last run before they're due, earlier when the
            vendor gives a discount for paying early, the next run when overdue. Credit notes are subtracted from the
            same vendor's payment.
          </p>
        </div>
        <WorkspacePicker section="/payments" value={workspaceId} />
      </div>

      {payments.isError && <p className="text-sm text-red-700">Couldn't load payments: {errorText(payments.error)}</p>}
      {!p && workspaceId != null && !payments.isError && <Skeleton className="h-64 w-full" />}
      {p && (
        <>
          <Card>
            <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-3 pt-6 text-sm">
              <span>
                Today <strong className="tabular-nums">{weekday(p.today.date)}</strong>
                {p.today.live && <span className="text-muted-foreground"> (the inbox's simulated clock; runs are paid as it passes them)</span>}
              </span>
              {!p.today.live && (
                <>
                  <form
                    className="flex items-center gap-2"
                    onSubmit={(e) => {
                      e.preventDefault()
                      if (date && workspaceId != null) setToday.mutate({ workspaceId, data: { date } })
                    }}
                  >
                    <input type="date" aria-label="Set today" value={date} onChange={(e) => setDate(e.target.value)}
                           className="h-8 rounded-md border bg-background px-2 text-sm" />
                    <Button type="submit" size="sm" variant="outline" disabled={!date || busy}>Set today</Button>
                  </form>
                  <Button size="sm" disabled={busy || planned.length === 0}
                          onClick={() => workspaceId != null && next.mutate({ workspaceId })}>
                    Pay the next run{planned[0] ? ` (${planned[0].run_date})` : ''}
                  </Button>
                  {p.due_now > 0 && (
                    <Button size="sm" variant="outline" disabled={busy}
                            onClick={() => workspaceId != null && due.mutate({ workspaceId })}>
                      Pay all {p.due_now} due by today
                    </Button>
                  )}
                </>
              )}
            </CardContent>
          </Card>
          {message && (
            <div role="status" className={cn('rounded-md border px-3 py-2 text-sm', message.ok
              ? 'border-emerald-300 bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200'
              : 'border-red-300 bg-red-50 text-red-900 dark:bg-red-950 dark:text-red-200')}>{message.text}</div>
          )}

          <div className="grid gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
            <Card>
              <CardHeader><CardTitle className="text-base">Runs ({runs.length})</CardTitle></CardHeader>
              <CardContent>
                {runs.length === 0
                  ? <p className="text-sm text-muted-foreground">Nothing approved for payment yet.</p>
                  : <RunList runs={runs} today={p.today.date} selected={current} onSelect={setSelected} />}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-base">The run</CardTitle></CardHeader>
              <CardContent>{current != null ? <RunDetail runId={current} /> : <p className="text-sm text-muted-foreground">Pick a run.</p>}</CardContent>
            </Card>
          </div>

          {p.waiting.length > 0 && (
            <Card>
              <CardHeader><CardTitle className="text-base">Waiting outside a run ({p.waiting.length})</CardTitle></CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Invoice</TableHead>
                      <TableHead className="text-right">Owed</TableHead>
                      <TableHead className="text-right">Discount</TableHead>
                      <TableHead>Why it waits</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody><ItemRows items={p.waiting} /></TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  )
}
