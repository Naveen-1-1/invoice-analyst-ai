import { Link } from '@tanstack/react-router'
import type { EvalEntryOut } from '@/api/generated'
import { useListEvaluations } from '@/api/generated'
import { CalibrationChart, RunComparison } from '@/components/EvalExtras'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { errorText } from '@/lib/format'

// The shapes bench/ logs (data/eval/EVAL_LOG.jsonl); see bench/src/apbench/evallog.py
type Count = { correct?: number; scored?: number; right?: number; wrong?: number }
type Official = {
  documents: number; fully_correct: number; key_fields: Count; fields: Record<string, Count>
  line_items: { f1_mean: number }; categories: Record<string, { docs: number; fully_correct: number }>
  flags: Record<string, { total: number; flagged: number }>; errors: { doc_id: string; fields: string[] }[]
}
type Extended = { documents: number; fields: Record<string, Count>; overall: Count; errors: { doc_id: string; fields: string[] }[] }
type Tier = { n: number; right: number; accuracy: number | null }
type Confidence = { accuracy: Record<string, Record<string, Tier>>; high_share: number | null; wrong_but_high: string[] }
type Ap = {
  planted: Record<string, { caught?: number; missed?: number }>; checks: Record<string, { tp?: number; fp?: number; fn?: number }>
  decisions: Record<string, number>; false_auto_approvals: string[]; payment_timing: Record<string, number>
  matching: Record<string, { right?: number; wrong?: number }>
}

const pct = (n: number | undefined, d: number | undefined) =>
  d ? `${((100 * (n ?? 0)) / d).toFixed(1)}%` : '—'
const frac = (n: number | undefined, d: number | undefined) => (d ? `${n ?? 0} / ${d}` : '—')
const cnt = (c: Count | undefined) => ({ n: c?.correct ?? c?.right ?? 0, d: c?.scored ?? (c ? (c.right ?? 0) + (c.wrong ?? 0) : 0) })

function Cell({ n, d }: { n: number | undefined; d: number | undefined }) {
  return (
    <TableCell className="text-right tabular-nums">
      {pct(n, d)} <span className="text-xs text-muted-foreground">{frac(n, d)}</span>
    </TableCell>
  )
}

type Col = { title: string; e: EvalEntryOut | undefined }

function Summary({ cols }: { cols: Col[] }) {
  const rows: { label: string; get: (e: EvalEntryOut) => [number | undefined, number | undefined] | string; strong?: boolean }[] = [
    { label: 'Documents with every key field right (official)', get: (e) => { const o = e.official as Official; return [o.fully_correct, o.documents] } },
    { label: 'Key fields right (official)', get: (e) => { const k = cnt((e.official as Official).key_fields); return [k.n, k.d] } },
    { label: 'Line items, mean F1', get: (e) => (e.official as Official).line_items.f1_mean.toFixed(3) },
    { label: 'Extended fields right (PO, tax ID, bank, terms, addressee, line qty/price/code)', get: (e) => { const k = cnt((e.extended as Extended).overall); return [k.n, k.d] } },
    { label: "Values rated 'high' confidence that were right", get: (e) => { const h = (e.confidence as Confidence).accuracy.all?.high; return [h?.right, h?.n] } },
    { label: 'Planted problems caught', get: (e) => { const p = Object.values((e.ap as Ap | null)?.planted ?? {}); return [p.reduce((s, x) => s + (x.caught ?? 0), 0), p.reduce((s, x) => s + (x.caught ?? 0) + (x.missed ?? 0), 0)] } },
    { label: 'False automatic approvals', strong: true, get: (e) => String((e.ap as Ap | null)?.false_auto_approvals.length ?? '—') },
    { label: 'Approved automatically (straight through)', get: (e) => { const d = (e.ap as Ap | null)?.decisions; return [d?.auto_approved, d?.bills] } },
    { label: 'Payments planned into the expected run', get: (e) => { const t = (e.ap as Ap | null)?.payment_timing ?? {}; return [t['right run'], (t['right run'] ?? 0) + (t['other run'] ?? 0)] } },
  ]
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Measure</TableHead>
          {cols.map((c) => <TableHead key={c.title} className="text-right">{c.title}</TableHead>)}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.label}>
            <TableCell className={r.strong ? 'font-semibold' : ''}>{r.label}</TableCell>
            {cols.map((c) => {
              if (!c.e) return <TableCell key={c.title} className="text-right text-muted-foreground">not run yet</TableCell>
              const v = r.get(c.e)
              return typeof v === 'string'
                ? <TableCell key={c.title} className={`text-right tabular-nums ${r.strong ? 'font-semibold' : ''}`}>{v}</TableCell>
                : <Cell key={c.title} n={v[0]} d={v[1]} />
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function Breakdown({ cols, title, keys, get }: {
  cols: Col[]; title: string; keys: string[]; get: (e: EvalEntryOut, k: string) => [number | undefined, number | undefined]
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{title}</TableHead>
          {cols.map((c) => <TableHead key={c.title} className="text-right">{c.title}</TableHead>)}
        </TableRow>
      </TableHeader>
      <TableBody>
        {keys.map((k) => (
          <TableRow key={k}>
            <TableCell>{k.replaceAll('_', ' ')}</TableCell>
            {cols.map((c) => {
              if (!c.e) return <TableCell key={c.title} />
              const [n, d] = get(c.e, k)
              return <Cell key={c.title} n={n} d={d} />
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function Errors({ e }: { e: EvalEntryOut }) {
  const link = (docId: string) => e.documents[docId] != null
    ? <Link to="/documents/$documentId" params={{ documentId: String(e.documents[docId]) }} className="hover:underline">{docId}</Link>
    : docId
  const official = (e.official as Official).errors
  const ext = (e.extended as Extended).errors
  const ap = e.ap as Ap | null
  return (
    <div className="grid gap-4 text-sm md:grid-cols-2">
      <section>
        <h4 className="mb-1 font-semibold">Official fields wrong ({official.length} documents)</h4>
        <ul className="space-y-0.5">{official.map((x) => <li key={x.doc_id}>{link(x.doc_id)}: <span className="text-muted-foreground">{x.fields.join(', ')}</span></li>)}</ul>
      </section>
      <section>
        <h4 className="mb-1 font-semibold">Extended fields wrong ({ext.length} documents)</h4>
        <ul className="space-y-0.5">{ext.map((x) => <li key={x.doc_id}>{link(x.doc_id)}: <span className="text-muted-foreground">{x.fields.join(', ')}</span></li>)}</ul>
      </section>
      {ap && ap.false_auto_approvals.length > 0 && (
        <section>
          <h4 className="mb-1 font-semibold text-red-700 dark:text-red-400">False automatic approvals</h4>
          <ul>{ap.false_auto_approvals.map((x) => <li key={x}>{x}</li>)}</ul>
        </section>
      )}
    </div>
  )
}

export function EvaluationPage() {
  const evals = useListEvaluations()
  if (evals.isPending) return <Skeleton className="h-96 w-full" />
  if (evals.isError) return <p className="text-sm text-red-700">Couldn't load the evaluations: {errorText(evals.error)}</p>
  const all = evals.data
  const dev = [...all].reverse().find((e) => e.kind === 'dev')
  const final = all.find((e) => e.kind === 'final')
  const after = all.filter((e) => e.kind === 'after_final')
  const cols: Col[] = [{ title: 'Dev (reference)', e: dev }, { title: 'Held-out (final)', e: final }]
  const fieldKeys = [...new Set(cols.flatMap((c) => Object.keys((c.e?.official as Official | undefined)?.fields ?? {})))]
  const extKeys = [...new Set(cols.flatMap((c) => Object.keys((c.e?.extended as Extended | undefined)?.fields ?? {})))]
  const catKeys = [...new Set(cols.flatMap((c) => Object.keys((c.e?.official as Official | undefined)?.categories ?? {})))]
  const plantKeys = [...new Set(cols.flatMap((c) => Object.keys((c.e?.ap as Ap | null | undefined)?.planted ?? {})))]
  const checkKeys = [...new Set(cols.flatMap((c) => Object.keys((c.e?.ap as Ap | null | undefined)?.checks ?? {})))]
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Evaluation</h1>
        <p className="text-sm text-muted-foreground">
          Built and tuned on the benchmark's 181 dev documents; tested once on the sealed held-out set (same recipe,
          a private seed, never looked at while building). Extraction is scored by the benchmark's official scorer
          and an extended scorer for the AP fields; the AP numbers come from replaying each set as an inbox against
          its vendors, POs and deliveries, with planted problems.
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          {dev && <>Dev: runs {dev.bench_run}/{dev.inbox_run}, logged {dev.logged_at.slice(0, 10)} at {dev.commit.slice(0, 9)}. </>}
          {final ? <>Held-out: runs {final.bench_run}/{final.inbox_run}, {final.logged_at.slice(0, 10)} at {final.commit.slice(0, 9)}.</>
            : <>The held-out set hasn't been evaluated yet (<code>make final</code>, once).</>}
          {after.length > 0 && <> {after.length} later held-out run(s) are logged as "after final" and don't replace it.</>}
        </p>
      </div>
      <Card><CardContent className="pt-6"><Summary cols={cols} /></CardContent></Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-base">Official fields</CardTitle></CardHeader>
          <CardContent><Breakdown cols={cols} title="Field" keys={fieldKeys} get={(e, k) => { const c = cnt((e.official as Official).fields[k]); return [c.n, c.d] }} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Extended fields</CardTitle></CardHeader>
          <CardContent><Breakdown cols={cols} title="Field" keys={extKeys} get={(e, k) => { const c = cnt((e.extended as Extended).fields[k]); return [c.n, c.d] }} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Documents fully right, by category</CardTitle></CardHeader>
          <CardContent><Breakdown cols={cols} title="Category" keys={catKeys} get={(e, k) => { const c = (e.official as Official).categories[k]; return [c?.fully_correct, c?.docs] }} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Confidence: how often each tier was right</CardTitle></CardHeader>
          <CardContent>
            <Breakdown cols={cols} title="Route · tier" keys={['all high', 'all medium', 'all low', 'text_llm high', 'image_llm high']}
                       get={(e, k) => { const [route, tier] = k.split(' '); const t = (e.confidence as Confidence).accuracy[route]?.[tier]; return [t?.right, t?.n] }} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Planted problems caught</CardTitle></CardHeader>
          <CardContent><Breakdown cols={cols} title="Planted" keys={plantKeys} get={(e, k) => { const p = (e.ap as Ap | null)?.planted[k]; return [p?.caught, (p?.caught ?? 0) + (p?.missed ?? 0)] }} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Each check: raised when it should be (recall)</CardTitle></CardHeader>
          <CardContent>
            <Breakdown cols={cols} title="Check" keys={checkKeys} get={(e, k) => { const c = (e.ap as Ap | null)?.checks[k]; return [c?.tp, (c?.tp ?? 0) + (c?.fn ?? 0)] }} />
            <p className="mt-2 text-xs text-muted-foreground">False alarms per check are in each run's ap_eval.json; every one still goes to a person.</p>
          </CardContent>
        </Card>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-base">Calibration: how often each tier was right</CardTitle></CardHeader>
          <CardContent><CalibrationChart cols={cols} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Compare two runs</CardTitle></CardHeader>
          <CardContent><RunComparison /></CardContent>
        </Card>
      </div>
      {cols.filter((c) => c.e).map((c) => (
        <Card key={c.title}>
          <CardHeader><CardTitle className="text-base">What went wrong: {c.title}</CardTitle></CardHeader>
          <CardContent><Errors e={c.e as EvalEntryOut} /></CardContent>
        </Card>
      ))}
    </div>
  )
}
