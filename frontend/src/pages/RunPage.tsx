import { Link, useParams } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { useListRunDocuments } from '@/api/generated'
import { TierBadge } from '@/components/TierBadge'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ROUTE_LABELS, errorText, formatMoney } from '@/lib/format'
import { cn } from '@/lib/utils'

const FILTERS = ['all', 'high', 'medium', 'low'] as const

export function RunPage() {
  const { runId } = useParams({ from: '/runs/$runId' })
  const docs = useListRunDocuments(Number(runId))
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('all')
  const shown = useMemo(
    () => (docs.data ?? []).filter((d) => filter === 'all' || d.tier === filter),
    [docs.data, filter],
  )
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-sm text-muted-foreground">
            <Link to="/" className="hover:underline">Runs</Link> / Run {runId}
          </p>
          <h1 className="text-xl font-semibold">Documents</h1>
        </div>
        <div className="flex gap-1" role="group" aria-label="Filter by confidence">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              aria-pressed={filter === f}
              className={cn('rounded-md border px-2.5 py-1 text-xs', filter === f && 'bg-primary text-primary-foreground')}
            >
              {f === 'all' ? `All (${docs.data?.length ?? 0})` : `${f} (${docs.data?.filter((d) => d.tier === f).length ?? 0})`}
            </button>
          ))}
        </div>
      </div>
      {docs.isPending && <Skeleton className="h-64 w-full" />}
      {docs.isError && <p className="text-sm text-red-700">Couldn't load documents: {errorText(docs.error)}</p>}
      {docs.data && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Document</TableHead>
              <TableHead>Read by</TableHead>
              <TableHead>Vendor</TableHead>
              <TableHead>Invoice number</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead>Confidence</TableHead>
              <TableHead>State</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((d) => (
              <TableRow key={d.id}>
                <TableCell>
                  <Link to="/documents/$documentId" params={{ documentId: String(d.id) }} className="font-medium underline-offset-4 hover:underline">
                    {d.bench_doc_id ?? d.filename}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">{ROUTE_LABELS[d.route ?? ''] ?? d.route}</TableCell>
                <TableCell className="max-w-56 truncate">{d.vendor ?? '—'}</TableCell>
                <TableCell>{d.invoice_number ?? '—'}</TableCell>
                <TableCell className="text-right tabular-nums">{formatMoney(d.total, d.currency)}</TableCell>
                <TableCell><TierBadge tier={d.tier} /></TableCell>
                <TableCell className="text-muted-foreground">{d.state}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
