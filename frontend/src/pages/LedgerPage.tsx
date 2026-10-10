import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useGetPayables, useGetTrialBalance, useListEntities, useListJournal } from '@/api/generated'
import { WorkspacePicker, useWorkspace } from '@/components/WorkspacePicker'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { errorText, formatMoney } from '@/lib/format'
import { cn } from '@/lib/utils'

const KIND: Record<string, string> = { invoice: 'invoice', credit_note: 'credit note', payment: 'payment', reversal: 'reversal' }
const OK = 'border-emerald-300 bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200'
const BAD = 'border-red-300 bg-red-50 text-red-900 dark:bg-red-950 dark:text-red-200'
const amount = (v: string) => (Number(v) ? formatMoney(v) : '')

function Journal({ workspaceId, entityId }: { workspaceId: number; entityId: number | undefined }) {
  const [page, setPage] = useState(0)
  const journal = useListJournal(workspaceId, { entity_id: entityId, limit: 50, offset: page * 50 })
  if (journal.isPending) return <Skeleton className="h-64 w-full" />
  if (journal.isError) return <p className="text-sm text-red-700">{errorText(journal.error)}</p>
  const j = journal.data
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">{j.total} entries, newest first. Entries are never changed: a mistake is put right with a new entry.</p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-28">Date</TableHead>
            <TableHead>Entry</TableHead>
            <TableHead>Account</TableHead>
            <TableHead className="text-right">Debit</TableHead>
            <TableHead className="text-right">Credit</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {j.entries.map((e) => e.lines.map((ln, k) => (
            <TableRow key={`${e.id}-${k}`} className={cn(k === 0 && 'border-t-2')}>
              <TableCell className="align-top tabular-nums">{k === 0 ? e.entry_date : ''}</TableCell>
              <TableCell className="max-w-72 align-top whitespace-normal">
                {k === 0 && (
                  <>
                    <span className="mr-1 rounded bg-muted px-1 text-xs">{KIND[e.kind] ?? e.kind}</span>
                    {e.document_id != null
                      ? <Link to="/documents/$documentId" params={{ documentId: String(e.document_id) }} className="hover:underline">{e.memo}</Link>
                      : e.memo}
                    {e.entity && <div className="text-xs text-muted-foreground">{e.entity}</div>}
                  </>
                )}
              </TableCell>
              <TableCell className={cn('whitespace-normal', Number(ln.credit) > 0 && 'pl-8')}>
                {ln.account} {ln.account_name}{ln.vendor && <span className="text-xs text-muted-foreground"> · {ln.vendor}</span>}
              </TableCell>
              <TableCell className="text-right tabular-nums">{amount(ln.debit)}{Number(ln.debit) ? ` ${ln.currency}` : ''}</TableCell>
              <TableCell className="text-right tabular-nums">{amount(ln.credit)}{Number(ln.credit) ? ` ${ln.currency}` : ''}</TableCell>
            </TableRow>
          )))}
        </TableBody>
      </Table>
      {j.total > 50 && (
        <div className="flex items-center gap-2 text-sm">
          <button type="button" className="rounded border px-2 py-0.5 disabled:opacity-40" disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</button>
          <span className="text-muted-foreground">{page * 50 + 1}–{Math.min((page + 1) * 50, j.total)} of {j.total}</span>
          <button type="button" className="rounded border px-2 py-0.5 disabled:opacity-40" disabled={(page + 1) * 50 >= j.total} onClick={() => setPage(page + 1)}>Older</button>
        </div>
      )}
    </div>
  )
}

function TrialBalance({ workspaceId, entityId }: { workspaceId: number; entityId: number | undefined }) {
  const tb = useGetTrialBalance(workspaceId, { entity_id: entityId })
  if (tb.isPending) return <Skeleton className="h-64 w-full" />
  if (tb.isError) return <p className="text-sm text-red-700">{errorText(tb.error)}</p>
  const t = tb.data
  const currencies = [...new Set(t.rows.map((r) => r.currency))]
  if (!currencies.length) return <p className="text-sm text-muted-foreground">Nothing booked yet.</p>
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2 text-sm">
        {Object.entries(t.balanced).map(([ccy, ok]) => (
          <Badge key={ccy} variant="outline" className={ok ? OK : BAD}>{ccy}: debits {ok ? '=' : '≠'} credits</Badge>
        ))}
        {t.reconciled.map((r) => (
          <Badge key={r.currency} variant="outline" className={r.ok ? OK : BAD}
                 title={`Accounts payable ${r.ledger}; unpaid items ${r.open_items}`}>
            {r.currency}: payables {r.ok ? '=' : '≠'} unpaid invoices
          </Badge>
        ))}
      </div>
      {currencies.map((ccy) => {
        const rows = t.rows.filter((r) => r.currency === ccy)
        return (
          <section key={ccy}>
            <h3 className="mb-1 text-sm font-semibold">{ccy}</h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Account</TableHead>
                  <TableHead className="w-36 text-right">Debits</TableHead>
                  <TableHead className="w-36 text-right">Credits</TableHead>
                  <TableHead className="w-40 text-right">Balance</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.account}>
                    <TableCell>{r.account} {r.name} <span className="text-xs text-muted-foreground">{r.type}</span></TableCell>
                    <TableCell className="text-right tabular-nums">{formatMoney(r.debit)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatMoney(r.credit)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {Number(r.balance) === 0 ? '0.00' : `${formatMoney(Math.abs(Number(r.balance)))} ${Number(r.balance) > 0 ? 'Dr' : 'Cr'}`}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
        )
      })}
    </div>
  )
}

function Payables({ workspaceId, entityId }: { workspaceId: number; entityId: number | undefined }) {
  const ap = useGetPayables(workspaceId, { entity_id: entityId })
  if (ap.isPending) return <Skeleton className="h-64 w-full" />
  if (ap.isError) return <p className="text-sm text-red-700">{errorText(ap.error)}</p>
  const a = ap.data
  if (!a.items.length) return <p className="text-sm text-muted-foreground">Nothing owed: everything booked has been paid (as of {a.today.date}).</p>
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">Booked and not yet paid, by how far past its due date it is on {a.today.date}.</p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Currency</TableHead>
            {a.buckets.map((b) => <TableHead key={b} className="text-right">{b}</TableHead>)}
          </TableRow>
        </TableHeader>
        <TableBody>
          {Object.entries(a.totals).map(([ccy, row]) => (
            <TableRow key={ccy}>
              <TableCell className="font-medium">{ccy}</TableCell>
              {a.buckets.map((b) => <TableCell key={b} className="text-right tabular-nums">{Number(row[b]) ? formatMoney(row[b]) : '—'}</TableCell>)}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Vendor · invoice</TableHead>
            <TableHead className="text-right">Owed</TableHead>
            <TableHead>Due</TableHead>
            <TableHead>Age</TableHead>
            <TableHead>Payment</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {a.items.map((i) => (
            <TableRow key={i.item_id}>
              <TableCell className="max-w-72 whitespace-normal">
                <Link to="/documents/$documentId" params={{ documentId: String(i.document_id) }} className="hover:underline">{i.vendor ?? 'Unknown vendor'}</Link>
                <div className="text-xs text-muted-foreground">{i.invoice_number ?? 'no number'}{i.entity ? ` · ${i.entity}` : ''}</div>
              </TableCell>
              <TableCell className="text-right tabular-nums">{formatMoney(i.amount, i.currency)}</TableCell>
              <TableCell className="tabular-nums">{i.due_date ?? '—'}</TableCell>
              <TableCell>{i.bucket}</TableCell>
              <TableCell>{i.status === 'planned' ? `run of ${i.run_date}` : 'waiting (see Payments)'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

export function LedgerPage() {
  const workspaceId = useWorkspace()
  const entities = useListEntities(workspaceId ?? 0, { query: { enabled: workspaceId != null } })
  const [entityId, setEntityId] = useState<number | undefined>(undefined)
  const csv = (name: string) => `/api/workspaces/${workspaceId}/export/${name}.csv${entityId != null ? `?entity_id=${entityId}` : ''}`
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Ledger</h1>
          <p className="text-sm text-muted-foreground">
            Double-entry books, in each invoice's own currency. Approving books the cost, VAT and what we owe; paying
            clears what we owe against the bank.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Company</span>
            <select className="max-w-64 rounded-md border bg-background px-2 py-1 text-sm" value={entityId ?? ''}
                    onChange={(e) => setEntityId(e.target.value ? Number(e.target.value) : undefined)}>
              <option value="">All our companies</option>
              {(entities.data ?? []).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </select>
          </label>
          <WorkspacePicker section="/ledger" value={workspaceId} />
        </div>
      </div>
      {workspaceId != null && (
        <Card>
          <CardContent className="pt-6">
            <Tabs defaultValue="trial">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <TabsList>
                  <TabsTrigger value="trial">Trial balance</TabsTrigger>
                  <TabsTrigger value="payables">What we owe</TabsTrigger>
                  <TabsTrigger value="journal">Journal</TabsTrigger>
                </TabsList>
                <div className="flex gap-3 text-sm">
                  <span className="text-muted-foreground">CSV:</span>
                  <a href={csv('trial-balance')} className="underline-offset-4 hover:underline">trial balance</a>
                  <a href={csv('payables')} className="underline-offset-4 hover:underline">what we owe</a>
                  <a href={csv('journal')} className="underline-offset-4 hover:underline">journal</a>
                </div>
              </div>
              <TabsContent value="trial" className="pt-3"><TrialBalance workspaceId={workspaceId} entityId={entityId} /></TabsContent>
              <TabsContent value="payables" className="pt-3"><Payables workspaceId={workspaceId} entityId={entityId} /></TabsContent>
              <TabsContent value="journal" className="pt-3"><Journal workspaceId={workspaceId} entityId={entityId} /></TabsContent>
            </Tabs>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
