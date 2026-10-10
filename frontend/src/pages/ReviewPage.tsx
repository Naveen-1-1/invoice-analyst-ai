import { useQueries, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { PageOut, ReviewDetail, WordOut } from '@/api/generated'
import {
  getGetDocumentQueryKey,
  getGetInvoiceReviewQueryKey,
  getGetPageWordsQueryOptions,
  useCorrectField,
  useDecideInvoice,
  useGetDocument,
  useGetInvoiceReview,
  useListReviewQueue,
  useReprocessInvoice,
} from '@/api/generated'
import { CheckList } from '@/components/CheckList'
import { CorrectableFields } from '@/components/CorrectableFields'
import { MatchTable } from '@/components/MatchTable'
import { PageViewer } from '@/components/PageViewer'
import { TierBadge } from '@/components/TierBadge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { errorText, formatDateTime, formatMoney } from '@/lib/format'
import { ACTIONS, type Action, DONE_WORDS, STATE_LABELS, STATE_STYLES, isTyping } from '@/lib/review'
import { cn } from '@/lib/utils'

const REASON_STYLE: Record<string, string> = {
  block: 'border-red-300 bg-red-50 dark:bg-red-950/40',
  review: 'border-amber-300 bg-amber-50 dark:bg-amber-950/40',
  ok: 'border-emerald-300 bg-emerald-50 dark:bg-emerald-950/40',
}
const REASON_WORD: Record<string, string> = { block: 'blocks payment', review: 'needs a look', ok: 'fine' }

type Picked = { page_no: number; bbox: number[]; text: string }

function union(a: number[], b: number[]): number[] {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]
}

// keyed by invoice, so moving to the next one starts with a clean note, no field open, and no old message
export function ReviewPage() {
  const { invoiceId } = useParams({ from: '/review/$invoiceId' })
  return <ReviewScreen key={invoiceId} id={Number(invoiceId)} />
}

function ReviewScreen({ id }: { id: number }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  // while the model reads it again, look every 3 s (decided from what is cached now, so it starts right after queuing)
  const cached = qc.getQueryData<ReviewDetail>(getGetInvoiceReviewQueryKey(id))
  const watching = ['queued', 'running'].includes(cached?.reread?.status ?? '')
  const review = useGetInvoiceReview(id, { query: { refetchInterval: watching ? 3000 : false } })
  const r = review.data
  const doc = useGetDocument(r?.document_id ?? 0, { query: { enabled: r != null } })
  const finished = r?.reread?.finished_at
  const docId = r?.document_id
  useEffect(() => {
    if (finished && docId != null) qc.invalidateQueries({ queryKey: getGetDocumentQueryKey(docId) }) // the new reading
  }, [finished, docId, qc])
  const queue = useListReviewQueue(r ? { workspace_id: r.workspace_id } : undefined, { query: { enabled: r != null } })

  const [hoveredId, setHoveredId] = useState<number | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [picked, setPicked] = useState<Picked | null>(null)
  const [note, setNote] = useState('')
  const [override, setOverride] = useState(false)
  const [pending, setPending] = useState<Action | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const noteRef = useRef<HTMLTextAreaElement>(null)

  // previous/next in the queue; after a decision this invoice leaves the queue, so remember where it was
  const items = useMemo(() => queue.data ?? [], [queue.data])
  const here = items.findIndex((q) => q.invoice_id === id)
  const [lastHere, setLastHere] = useState(0)
  if (here >= 0 && here !== lastHere) setLastHere(here)
  const next = here >= 0 ? items[here + 1] : items[lastHere]
  const prev = here >= 0 ? items[here - 1] : items[lastHere - 1]
  const go = (invoice?: { invoice_id: number }) => {
    if (invoice) navigate({ to: '/review/$invoiceId', params: { invoiceId: String(invoice.invoice_id) } })
  }

  const invoice = doc.data?.invoices.find((i) => i.id === id)
  const pages = useMemo(() => doc.data?.pages ?? [], [doc.data])
  const words = useQueries({
    queries: pages.map((p) => ({ ...getGetPageWordsQueryOptions(p.id), enabled: editing != null, staleTime: Infinity })),
  })
  const wordsByPage: Record<number, WordOut[]> = {}
  pages.forEach((p, i) => {
    wordsByPage[p.page_no] = words[i]?.data ?? []
  })

  const updated = (data: ReviewDetail, text: string) => {
    qc.setQueryData(getGetInvoiceReviewQueryKey(id), data)
    qc.invalidateQueries({ queryKey: getGetDocumentQueryKey(data.document_id) })
    qc.invalidateQueries({ predicate: (q) => /^\/api\/(review|workspaces)/.test(String(q.queryKey[0])) })
    setMessage({ ok: true, text })
  }
  const failed = (e: unknown) => setMessage({ ok: false, text: errorText(e) })
  const correct = useCorrectField({
    mutation: {
      onSuccess: (data) => {
        setEditing(null)
        setPicked(null)
        updated(data, 'Saved. The checks ran again on the corrected value.')
      },
      onError: failed,
    },
  })
  const decide = useDecideInvoice({ mutation: { onError: failed } })
  const reprocess = useReprocessInvoice({ mutation: { onError: failed } })
  const busy = correct.isPending || decide.isPending || reprocess.isPending

  const run = (action: Action) => {
    if (!r || busy) return
    if (!note.trim()) {
      setPending(action)
      setMessage({ ok: false, text: 'Add a note saying why (it goes into the audit trail), then press ⌘/Ctrl+Enter.' })
      noteRef.current?.focus()
      return
    }
    const done = (data: ReviewDetail) => {
      setNote('')
      setOverride(false)
      setPending(null)
      updated(data, `${DONE_WORDS[action]}.${action === 'reprocess' ? ' The checks run again when it is done.' : next ? ' Press j for the next one.' : ''}`)
    }
    if (action === 'reprocess') reprocess.mutate({ invoiceId: id, data: { note } }, { onSuccess: done })
    else decide.mutate({ invoiceId: id, data: { action, note, override } }, { onSuccess: done })
  }

  const allowed = new Set(r?.actions ?? [])
  const buttons = ACTIONS.filter((a) => allowed.has(a.action))

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'j') go(next)
      else if (e.key === 'k') go(prev)
      else {
        const hit = buttons.find((b) => b.key === e.key)
        if (!hit) return
        run(hit.action)
      }
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (review.isPending) return <Skeleton className="h-96 w-full" />
  if (review.isError) return <p className="text-sm text-red-700">Couldn't load the review: {errorText(review.error)}</p>
  if (!r) return null

  const pickWord = (page: PageOut, word: WordOut, extend: boolean) => {
    if (extend && picked && picked.page_no === page.page_no && draft === picked.text) {
      const text = `${picked.text} ${word.t}`
      setPicked({ page_no: page.page_no, bbox: union(picked.bbox, word.bbox), text })
      setDraft(text)
    } else {
      setPicked({ page_no: page.page_no, bbox: word.bbox, text: word.t })
      setDraft(word.t)
    }
  }
  const save = () => {
    if (!editing) return
    const fromPage = picked && picked.text === draft
    correct.mutate({
      invoiceId: id,
      data: { field: editing, text: draft, page_no: fromPage ? picked.page_no : null, bbox: fromPage ? picked.bbox : null },
    })
  }

  const reasons = r.task?.reasons ?? []
  const decidedNow = !r.task
  const rereading = ['queued', 'running'].includes(r.reread?.status ?? '')
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm text-muted-foreground">
            <Link to="/review" className="hover:underline">Review queue</Link>
            {here >= 0 && ` / ${here + 1} of ${items.length}`}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold">{invoice?.vendor_name ?? doc.data?.filename ?? `Invoice ${id}`}</h1>
            <Badge variant="outline" className={cn('font-medium', STATE_STYLES[r.state])}>{STATE_LABELS[r.state] ?? r.state}</Badge>
            {invoice && <TierBadge tier={invoice.conf_tier} />}
          </div>
          <p className="text-sm text-muted-foreground">
            {r.doc_type === 'credit_note' ? 'Credit note' : 'Invoice'} {invoice?.invoice_number ?? '(no number)'} ·{' '}
            <span className="font-medium text-foreground tabular-nums">{formatMoney(invoice?.total, invoice?.currency)}</span>
            {r.cap.usd_estimate != null && invoice?.currency !== 'USD' && (
              <span title={`Rates of ${r.cap.rates_as_of}; used only for the automatic-approval limit`}> (≈ USD {Number(r.cap.usd_estimate).toLocaleString(undefined, { maximumFractionDigits: 0 })})</span>
            )}
            {invoice?.due_date && ` · due ${invoice.due_date}`} ·{' '}
            <Link to="/documents/$documentId" params={{ documentId: String(r.document_id) }} className="hover:underline">
              document and audit trail
            </Link>
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={!prev} aria-keyshortcuts="k" onClick={() => go(prev)}>← Previous <kbd aria-hidden className="ml-1 text-muted-foreground">k</kbd></Button>
          <Button variant="outline" size="sm" disabled={!next} aria-keyshortcuts="j" onClick={() => go(next)}>Next <kbd aria-hidden className="ml-1 text-muted-foreground">j</kbd> →</Button>
        </div>
      </div>

      {message && (
        <div role="status" className={cn('rounded-md border px-3 py-2 text-sm', message.ok
          ? 'border-emerald-300 bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200'
          : 'border-red-300 bg-red-50 text-red-900 dark:bg-red-950 dark:text-red-200')}>
          {message.text}
        </div>
      )}
      {rereading && (
        <div role="status" className="rounded-md border border-sky-300 bg-sky-50 px-3 py-2 text-sm text-sky-900 dark:bg-sky-950 dark:text-sky-200">
          The model is reading this document again{r.reread?.explanation ? ` (${r.reread.explanation})` : ''}. This page updates when it's done.
        </div>
      )}
      {r.reread && !rereading && r.reread.explanation && (
        <p className="text-sm text-muted-foreground">Last re-read: {r.reread.explanation}</p>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
        <div className="lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
          {doc.data ? (
            <PageViewer
              pages={pages}
              fields={invoice?.fields ?? []}
              hoveredId={hoveredId}
              onHover={setHoveredId}
              onSelect={setHoveredId}
              words={editing ? wordsByPage : undefined}
              picked={picked}
              onPickWord={editing ? pickWord : undefined}
            />
          ) : (
            <Skeleton className="aspect-[1/1.4] w-full" />
          )}
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{decidedNow ? 'Decision' : 'Why it needs you'}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {decidedNow && <p>This invoice is {STATE_LABELS[r.state] ?? r.state}; there's nothing left to decide.</p>}
              {reasons.length === 0 && !decidedNow && <p className="text-muted-foreground">No reasons recorded.</p>}
              {reasons.map((x, i) => (
                <div key={i} className={cn('rounded-md border px-2.5 py-1.5', REASON_STYLE[x.kind] ?? 'border-border')}>
                  <span className="mr-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{REASON_WORD[x.kind] ?? x.kind}</span>
                  {x.text}
                </div>
              ))}
              {!decidedNow && reasons.length > 0 && reasons.every((x) => x.kind === 'ok') && (
                <p className="text-muted-foreground">Everything passes now; approve it when you're satisfied.</p>
              )}
            </CardContent>
          </Card>

          {!decidedNow && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Decide</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                {r.partial && allowed.has('approve_partial') && (
                  <div className="rounded-md border p-2.5">
                    <div className="font-medium">Received part: {formatMoney(r.partial.approved, r.partial.currency)} of {formatMoney(r.partial.total, r.partial.currency)}</div>
                    <div className="text-muted-foreground">
                      tax {formatMoney(r.partial.approved_tax)} in proportion · {formatMoney(r.partial.held, r.partial.currency)} would stay on hold
                    </div>
                    <ul className="mt-1 text-xs text-muted-foreground">
                      {r.partial.lines.filter((l) => Number(l.approved) < Number(l.amount)).map((l) => (
                        <li key={l.line_no}>Line {l.line_no}, {l.description}: {formatMoney(l.approved)} of {formatMoney(l.amount)} ({l.why})</li>
                      ))}
                      {(() => {
                        const full = r.partial.lines.filter((l) => Number(l.approved) >= Number(l.amount)).length
                        return full > 0 && <li>{full} other line{full === 1 ? '' : 's'} in full</li>
                      })()}
                    </ul>
                  </div>
                )}
                {r.state === 'APPROVED_PARTIAL' && (
                  <p>Approved so far: {formatMoney(r.approved_so_far, invoice?.currency)}. Approve the rest once it's delivered, or reject the rest.</p>
                )}
                {r.blocks.length > 0 && (
                  <label className="flex items-start gap-2 rounded-md border border-red-300 bg-red-50 p-2.5 dark:bg-red-950/40">
                    <input type="checkbox" className="mt-0.5" checked={override} onChange={(e) => setOverride(e.target.checked)} />
                    <span>Override: pay it even though {r.blocks.length === 1 ? 'this finding blocks' : 'these findings block'} payment. Say why in the note.</span>
                  </label>
                )}
                <label className="block">
                  <span className="mb-1 block font-medium">Note <span className="font-normal text-muted-foreground">(required; goes into the audit trail)</span></span>
                  <textarea
                    ref={noteRef}
                    aria-label="Note"
                    value={note}
                    rows={2}
                    className="w-full rounded-md border bg-background px-2 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    placeholder="e.g. Vendor confirmed the new bank details by phone"
                    onChange={(e) => setNote(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && pending) {
                        e.preventDefault()
                        run(pending)
                      }
                    }}
                  />
                </label>
                <div className="flex flex-wrap gap-2">
                  {buttons.map((b) => (
                    <Button
                      key={b.action}
                      size="sm"
                      variant={b.action === 'approve' ? 'default' : b.action === 'reject' ? 'destructive' : 'outline'}
                      className={cn(pending === b.action && 'ring-2 ring-ring ring-offset-1')}
                      aria-keyshortcuts={b.key}
                      aria-describedby={`hint-${b.action}`}
                      disabled={busy || (b.action === 'reprocess' && rereading)}
                      onClick={() => run(b.action)}
                    >
                      {b.label} <kbd aria-hidden className="ml-1 opacity-60">{b.key}</kbd>
                      <span id={`hint-${b.action}`} className="sr-only">{b.hint}</span>
                    </Button>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Values</CardTitle>
              <p className="text-sm text-muted-foreground">Hover a value to see it on the page. Correct anything misread.</p>
            </CardHeader>
            <CardContent>
              {invoice ? (
                <CorrectableFields
                  fields={invoice.fields}
                  correctable={r.correctable}
                  currency={invoice.currency}
                  canCorrect={allowed.has('correct')}
                  editing={editing}
                  draft={draft}
                  saving={correct.isPending}
                  hoveredId={hoveredId}
                  onHover={setHoveredId}
                  onEdit={(f) => {
                    setEditing(f)
                    setPicked(null)
                  }}
                  onDraft={setDraft}
                  onSave={save}
                />
              ) : (
                <Skeleton className="h-48 w-full" />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Purchase order{r.po_number ? ` ${r.po_number}` : ''}</CardTitle>
            </CardHeader>
            <CardContent>
              <MatchTable matches={r.matches} poNumber={r.po_number} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">All checks</CardTitle>
            </CardHeader>
            <CardContent>
              <CheckList checks={invoice?.checks ?? []} />
            </CardContent>
          </Card>

          {(r.decisions.length > 0 || r.approvals.length > 0) && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">History</CardTitle>
              </CardHeader>
              <CardContent>
                <ol className="space-y-2 text-sm">
                  {r.decisions.map((d) => (
                    <li key={`d${d.id}`} className="border-l-2 pl-3">
                      <div className="font-medium">{ACTIONS.find((a) => a.action === d.action)?.label ?? d.action}</div>
                      <div>{d.note}</div>
                      <div className="text-xs text-muted-foreground">{formatDateTime(d.decided_at)} · you</div>
                    </li>
                  ))}
                  {r.approvals.map((a) => (
                    <li key={`a${a.id}`} className="border-l-2 border-emerald-400 pl-3">
                      <div className="font-medium">{a.kind === 'auto' ? 'Approved automatically' : 'Approval'}: {formatMoney(a.amount, a.currency)}</div>
                      <ul className="text-xs">
                        {a.reasons.filter((x) => x.kind !== 'note').map((x, i) => <li key={i}>{x.text}</li>)}
                      </ul>
                      <div className="text-xs text-muted-foreground">{formatDateTime(a.created_at)} · rules {a.rules_ver}</div>
                    </li>
                  ))}
                </ol>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
