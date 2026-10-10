import { useState } from 'react'
import type { FieldOut, Reason } from '@/api/generated'
import { TierBadge } from '@/components/TierBadge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { FIELD_LABELS, HEADER_ORDER, formatValue } from '@/lib/format'
import { cn } from '@/lib/utils'

const EFFECT: Record<string, { mark: string; className: string; label: string }> = {
  '+': { mark: '✓', className: 'text-emerald-700 dark:text-emerald-400', label: 'supports' },
  '-': { mark: '?', className: 'text-amber-700 dark:text-amber-400', label: 'some doubt' },
  '!': { mark: '!', className: 'text-red-700 dark:text-red-400', label: 'serious doubt' },
  i: { mark: 'i', className: 'text-muted-foreground', label: 'note' },
}

function Reasons({ reasons }: { reasons: Reason[] }) {
  return (
    <ul className="mt-1.5 space-y-1 text-xs">
      {reasons.map((r, i) => {
        const e = EFFECT[r.effect] ?? EFFECT.i
        return (
          <li key={i} className="flex gap-2">
            <span className={cn('w-3 shrink-0 text-center font-bold', e.className)} aria-label={e.label}>
              {e.mark}
            </span>
            <span>{r.text}</span>
          </li>
        )
      })}
    </ul>
  )
}

type Props = {
  fields: FieldOut[]
  currency?: string | null
  hoveredId: number | null
  selectedId: number | null
  onHover: (id: number | null) => void
  onSelect: (id: number) => void
}

// Every extracted value with its confidence and the evidence behind it. Hovering a row highlights its box on the
// page; clicking scrolls the page to it and shows the reasons.
export function FieldList({ fields, currency, hoveredId, selectedId, onHover, onSelect }: Props) {
  const [open, setOpen] = useState<Set<number>>(new Set())
  const header = HEADER_ORDER.map((name) => fields.find((f) => f.field === name && f.line_no == null)).filter(
    (f): f is FieldOut => Boolean(f),
  )
  const lineNos = [...new Set(fields.filter((f) => f.line_no != null).map((f) => f.line_no as number))].sort((a, b) => a - b)
  const lineField = (n: number, name: string) => fields.find((f) => f.line_no === n && f.field === name)

  const rowProps = (f: FieldOut) => ({
    onMouseEnter: () => onHover(f.id),
    onMouseLeave: () => onHover(null),
    onClick: () => {
      onSelect(f.id)
      setOpen((prev) => {
        const next = new Set(prev)
        if (next.has(f.id)) next.delete(f.id)
        else next.add(f.id)
        return next
      })
    },
    className: cn(
      'cursor-pointer',
      (f.id === hoveredId || f.id === selectedId) && 'bg-accent',
    ),
  })

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-2 text-sm font-semibold">Header fields</h3>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-36">Field</TableHead>
              <TableHead>Value</TableHead>
              <TableHead className="w-32 text-right">Confidence</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {header.map((f) => (
              <TableRow key={f.id} {...rowProps(f)}>
                <TableCell className="align-top text-muted-foreground">{FIELD_LABELS[f.field] ?? f.field}</TableCell>
                <TableCell className="align-top whitespace-normal">
                  <div className="font-medium">{formatValue(f.field, f.value, currency)}</div>
                  {f.raw_text && f.raw_text !== String(f.value) && (
                    <div className="text-xs text-muted-foreground">printed as “{f.raw_text}”</div>
                  )}
                  {open.has(f.id) && <Reasons reasons={f.reasons} />}
                </TableCell>
                <TableCell className="text-right align-top">
                  <TierBadge tier={f.tier} confidence={f.confidence} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>

      {lineNos.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">Line items ({lineNos.length})</h3>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8">#</TableHead>
                <TableHead>Description</TableHead>
                <TableHead className="text-right">Amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {lineNos.map((n) => {
                const desc = lineField(n, 'line_description')
                const amount = lineField(n, 'line_amount')
                return (
                  <TableRow key={n}>
                    <TableCell className="align-top text-muted-foreground">{n}</TableCell>
                    {desc ? (
                      <TableCell {...rowProps(desc)} className={cn(rowProps(desc).className, 'align-top whitespace-normal')}>
                        <div className="flex items-start justify-between gap-2">
                          <span>{String(desc.value ?? '—')}</span>
                          <TierBadge tier={desc.tier} />
                        </div>
                        {open.has(desc.id) && <Reasons reasons={desc.reasons} />}
                      </TableCell>
                    ) : (
                      <TableCell />
                    )}
                    {amount ? (
                      <TableCell {...rowProps(amount)} className={cn(rowProps(amount).className, 'text-right align-top')}>
                        <div className="flex items-start justify-end gap-2">
                          <span className="tabular-nums">{formatValue('line_amount', amount.value)}</span>
                          <TierBadge tier={amount.tier} />
                        </div>
                        {open.has(amount.id) && <Reasons reasons={amount.reasons} />}
                      </TableCell>
                    ) : (
                      <TableCell />
                    )}
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </section>
      )}
    </div>
  )
}
