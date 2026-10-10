import type { MatchOut } from '@/api/generated'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatMoney } from '@/lib/format'
import { cn } from '@/lib/utils'

const OUTCOME: Record<string, { label: string; className: string }> = {
  match: { label: 'matches', className: 'text-emerald-700 dark:text-emerald-400' },
  two_way: { label: 'amount matches', className: 'text-emerald-700 dark:text-emerald-400' },
  price_variance: { label: 'price over PO', className: 'text-amber-700 dark:text-amber-400' },
  qty_over_received: { label: 'more than received', className: 'text-amber-700 dark:text-amber-400' },
  unmatched: { label: 'not on the PO', className: 'text-amber-700 dark:text-amber-400' },
}

const qty = (v: string | null | undefined) => (v == null ? '—' : String(Number(v)))

// Each invoice line against its PO line and what was received (the three-way match).
export function MatchTable({ matches, poNumber }: { matches: MatchOut[]; poNumber: string | null }) {
  if (!poNumber) return <p className="text-sm text-muted-foreground">No purchase order matched.</p>
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8">#</TableHead>
            <TableHead>Line</TableHead>
            <TableHead className="text-right">Invoiced</TableHead>
            <TableHead className="text-right" title="Received on the PO and not yet on another invoice">Received</TableHead>
            <TableHead className="text-right">Price · PO</TableHead>
            <TableHead>Result</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {matches.map((m) => {
            const o = OUTCOME[m.outcome] ?? { label: m.outcome, className: '' }
            return (
              <TableRow key={m.line_no}>
                <TableCell className="text-muted-foreground">{m.line_no}</TableCell>
                <TableCell className="max-w-56 whitespace-normal">{m.description ?? '—'}</TableCell>
                <TableCell className="text-right tabular-nums">{qty(m.qty_invoiced)}</TableCell>
                <TableCell className="text-right tabular-nums">{qty(m.qty_received_avail)}</TableCell>
                <TableCell className="text-right tabular-nums whitespace-nowrap">
                  {formatMoney(m.price_inv)} · {formatMoney(m.price_po)}
                  {m.variance_pct != null && Number(m.variance_pct) !== 0 && (
                    <span className="text-xs text-muted-foreground"> ({Number(m.variance_pct) > 0 ? '+' : ''}{Number(m.variance_pct).toFixed(1)}%)</span>
                  )}
                </TableCell>
                <TableCell className={cn('whitespace-nowrap', o.className)}>{o.label}</TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
