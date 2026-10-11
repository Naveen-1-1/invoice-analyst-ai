import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import type { EvalEntryOut } from '@/api/generated'
import { useCompareRuns, useListScoredRuns } from '@/api/generated'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { errorText } from '@/lib/format'

type Tier = { n: number; right: number }
const TARGET = 0.99

// Decision 51: how often each tier was right, as plain bars with the 99% target marked.
export function CalibrationChart({ cols }: { cols: { title: string; e: EvalEntryOut | undefined }[] }) {
  const rows: [string, string][] = [['all', 'high'], ['all', 'medium'], ['all', 'low'], ['text_llm', 'high'], ['image_llm', 'high']]
  const label: Record<string, string> = { all: 'all', text_llm: 'PDF text', image_llm: 'images' }
  return (
    <div className="space-y-3 text-sm">
      {rows.map(([route, tier]) => (
        <div key={`${route}-${tier}`}>
          <div className="mb-1 font-medium">{tier} <span className="font-normal text-muted-foreground">({label[route]})</span></div>
          {cols.map((c) => {
            const t = (c.e?.confidence as { accuracy?: Record<string, Record<string, Tier>> } | undefined)?.accuracy?.[route]?.[tier]
            const acc = t && t.n ? t.right / t.n : null
            return (
              <div key={c.title} className="flex items-center gap-2">
                <span className="w-36 shrink-0 text-xs text-muted-foreground">{c.title}</span>
                <div className="relative h-4 flex-1 rounded bg-muted" role="img"
                     aria-label={acc == null ? `${c.title}: none` : `${c.title}: ${(acc * 100).toFixed(1)}% of ${t?.n}`}>
                  {acc != null && (
                    <div className={`h-4 rounded ${tier === 'high' ? (acc >= TARGET ? 'bg-emerald-600' : 'bg-red-600') : 'bg-slate-500'}`}
                         style={{ width: `${acc * 100}%` }} />
                  )}
                  {tier === 'high' && <div className="absolute top-[-2px] h-5 border-l-2 border-dashed border-foreground/70" style={{ left: `${TARGET * 100}%` }} title="99% target" />}
                </div>
                <span className="w-32 shrink-0 text-right text-xs tabular-nums">
                  {acc == null ? '—' : `${(acc * 100).toFixed(1)}%`} <span className="text-muted-foreground">{t ? `${t.right}/${t.n}` : ''}</span>
                </span>
              </div>
            )
          })}
        </div>
      ))}
      <p className="text-xs text-muted-foreground">The dashed line is the 99% target for 'high'. Medium and low have no target: they're what goes to a person.</p>
    </div>
  )
}

const runLabel = (r: { run: string; label?: string | null; documents: number; corpus: string }) =>
  `run ${Number(r.run)} · ${r.label ?? ''} · ${r.documents} docs${r.corpus === 'heldout' ? ' · held-out' : ''}`

export function RunComparison() {
  const runs = useListScoredRuns()
  const all = runs.data ?? []
  const full = all.filter((r) => r.documents > 100)
  const [a, setA] = useState<string | null>(null)
  const [b, setB] = useState<string | null>(null)
  const ra = a ?? full.filter((r) => r.corpus === 'dev').at(-2)?.run ?? null
  const rb = b ?? full.filter((r) => r.corpus === 'dev').at(-1)?.run ?? null
  const cmp = useCompareRuns({ a: ra ?? '', b: rb ?? '' }, { query: { enabled: ra != null && rb != null && ra !== rb } })
  const pick = (value: string | null, set: (v: string) => void, name: string) => (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground">{name}</span>
      <select className="rounded-md border bg-background px-2 py-1" value={value ?? ''} onChange={(e) => set(e.target.value)}>
        {all.map((r) => <option key={r.run} value={r.run}>{runLabel(r)}</option>)}
      </select>
    </label>
  )
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap gap-3">{pick(ra, setA, 'From')}{pick(rb, setB, 'To')}</div>
      {ra === rb && <p className="text-muted-foreground">Pick two different runs.</p>}
      {cmp.isError && <p className="text-red-700">{errorText(cmp.error)}</p>}
      {cmp.data && (
        <>
          <p>
            Documents fully right: <strong>{cmp.data.a.fully_correct}</strong> → <strong>{cmp.data.b.fully_correct}</strong> of {cmp.data.b.documents};
            {' '}{cmp.data.changes.length} document{cmp.data.changes.length === 1 ? '' : 's'} changed.
          </p>
          <Table>
            <TableHeader><TableRow><TableHead>Field</TableHead><TableHead className="text-right">From</TableHead><TableHead className="text-right">To</TableHead><TableHead className="text-right">Change</TableHead></TableRow></TableHeader>
            <TableBody>
              {cmp.data.fields.map((f) => {
                const delta = f.b_right - f.a_right
                return (
                  <TableRow key={f.field}>
                    <TableCell>{f.field.replaceAll('_', ' ')}</TableCell>
                    <TableCell className="text-right tabular-nums">{f.a_right} / {f.a_scored}</TableCell>
                    <TableCell className="text-right tabular-nums">{f.b_right} / {f.b_scored}</TableCell>
                    <TableCell className={`text-right tabular-nums ${delta > 0 ? 'text-emerald-700 dark:text-emerald-400' : delta < 0 ? 'text-red-700 dark:text-red-400' : 'text-muted-foreground'}`}>
                      {delta > 0 ? `+${delta}` : delta || '—'}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
          {cmp.data.changes.length > 0 && (
            <ul className="space-y-1">
              {cmp.data.changes.map((c) => (
                <li key={c.doc_id}>
                  <span className="font-medium">{c.doc_id}</span>
                  {c.gained.length > 0 && <span className="text-emerald-700 dark:text-emerald-400"> now right: {c.gained.join(', ')}</span>}
                  {c.lost.length > 0 && <span className="text-red-700 dark:text-red-400"> now wrong: {c.lost.join(', ')}</span>}
                  {' '}
                  {c.a_document_id != null && <Link to="/documents/$documentId" params={{ documentId: String(c.a_document_id) }} className="text-xs hover:underline">before</Link>}
                  {c.b_document_id != null && <> · <Link to="/documents/$documentId" params={{ documentId: String(c.b_document_id) }} className="text-xs hover:underline">after</Link></>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
