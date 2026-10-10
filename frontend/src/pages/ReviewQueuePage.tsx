import { Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'
import { useListReviewQueue, useListWorkspaces } from '@/api/generated'
import { TierBadge } from '@/components/TierBadge'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { errorText, formatMoney } from '@/lib/format'
import { STATE_LABELS, isTyping } from '@/lib/review'
import { cn } from '@/lib/utils'

// Everything waiting for a person: payment-blocked first, then the soonest due. j/k move, Enter opens.
export function ReviewQueuePage() {
  const navigate = useNavigate()
  const workspaces = useListWorkspaces()
  const [workspaceId, setWorkspaceId] = useState<number | null>(null)
  const queue = useListReviewQueue(workspaceId != null ? { workspace_id: workspaceId } : undefined, {
    query: { refetchInterval: 15_000 },
  })
  const [cursor, setCursor] = useState(0)
  const items = useMemo(() => queue.data ?? [], [queue.data])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey || !items.length) return
      if (e.key === 'j') setCursor((c) => Math.min(c + 1, items.length - 1))
      else if (e.key === 'k') setCursor((c) => Math.max(c - 1, 0))
      else if (e.key === 'Enter' && items[cursor])
        navigate({ to: '/review/$invoiceId', params: { invoiceId: String(items[cursor].invoice_id) } })
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [items, cursor, navigate])

  useEffect(() => {
    document.getElementById(`queue-row-${cursor}`)?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  const blocked = items.filter((i) => i.blocks > 0).length
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Review queue</h1>
          <p className="text-sm text-muted-foreground">
            Invoices the rules wouldn't approve on their own. Payment-blocked ones come first, then the soonest due.
            <span className="ml-1 hidden sm:inline">Keys: <kbd>j</kbd>/<kbd>k</kbd> move, <kbd>Enter</kbd> opens.</span>
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Workspace</span>
          <select
            className="rounded-md border bg-background px-2 py-1 text-sm"
            value={workspaceId ?? ''}
            onChange={(e) => {
              setWorkspaceId(e.target.value ? Number(e.target.value) : null)
              setCursor(0)
            }}
          >
            <option value="">All</option>
            {(workspaces.data ?? []).map((w) => (
              <option key={w.id} value={w.id}>{w.name}</option>
            ))}
          </select>
        </label>
      </div>

      {queue.isError && <p className="text-sm text-red-700">Couldn't load the queue: {errorText(queue.error)}</p>}
      {queue.isPending && <Skeleton className="h-64 w-full" />}
      {queue.data && (
        <>
          <p className="text-sm">
            <strong className="tabular-nums">{items.length}</strong> waiting
            {blocked > 0 && <span className="text-red-700 dark:text-red-400"> · {blocked} with payment blocked</span>}
          </p>
          {items.length === 0 ? (
            <p className="rounded-md border p-6 text-center text-sm text-muted-foreground">Nothing to review.</p>
          ) : (
            <div className="overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10">#</TableHead>
                    <TableHead>Vendor · invoice</TableHead>
                    <TableHead className="text-right">Total</TableHead>
                    <TableHead>Due</TableHead>
                    <TableHead>Why it's here</TableHead>
                    <TableHead>Read</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((q, i) => (
                    <TableRow
                      key={q.task_id}
                      id={`queue-row-${i}`}
                      className={cn('cursor-pointer', i === cursor && 'bg-accent')}
                      onMouseEnter={() => setCursor(i)}
                      onClick={() => navigate({ to: '/review/$invoiceId', params: { invoiceId: String(q.invoice_id) } })}
                    >
                      <TableCell className="text-muted-foreground tabular-nums">{q.seq ?? '—'}</TableCell>
                      <TableCell className="max-w-72 whitespace-normal">
                        <Link
                          to="/review/$invoiceId"
                          params={{ invoiceId: String(q.invoice_id) }}
                          className="font-medium hover:underline"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {q.vendor_name ?? q.filename}
                        </Link>
                        <div className="text-xs text-muted-foreground">
                          {q.invoice_number ?? 'no number'}
                          {q.doc_type === 'credit_note' && ' · credit note'}
                          {workspaceId == null && ` · ${q.workspace}`}
                        </div>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{formatMoney(q.total, q.currency)}</TableCell>
                      <TableCell className="tabular-nums">{q.due_date ?? '—'}</TableCell>
                      <TableCell className="whitespace-normal">
                        <div className="flex flex-wrap gap-1 text-xs">
                          {q.blocks > 0 && (
                            <span className="rounded border border-red-300 bg-red-100 px-1 text-red-900 dark:bg-red-950 dark:text-red-200">
                              payment blocked
                            </span>
                          )}
                          {q.state !== 'NEEDS_REVIEW' && (
                            <span className="rounded border px-1">{STATE_LABELS[q.state] ?? q.state}</span>
                          )}
                          {q.labels.map((l) => (
                            <span key={l} className="rounded bg-muted px-1 text-muted-foreground">{l}</span>
                          ))}
                        </div>
                      </TableCell>
                      <TableCell><TierBadge tier={q.conf_tier} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
