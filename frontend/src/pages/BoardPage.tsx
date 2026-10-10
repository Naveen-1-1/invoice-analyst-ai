import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'
import type { BoardOut, CardOut } from '@/api/generated'
import { getGetBoardQueryKey, useGetBoard, useListWorkspaces } from '@/api/generated'
import { BoardCard } from '@/components/BoardCard'
import { UploadDrop } from '@/components/UploadDrop'
import { Skeleton } from '@/components/ui/skeleton'
import { errorText } from '@/lib/format'
import { useLiveEvents } from '@/lib/useLiveEvents'

type Column = { key: string; title: string; hint: string; match: (c: CardOut) => boolean }

const decided = (c: CardOut) => ['APPROVED', 'APPROVED_PARTIAL', 'REJECTED'].includes(c.invoice_state ?? '')
const inReview = (c: CardOut) => ['NEEDS_REVIEW', 'ON_HOLD'].includes(c.invoice_state ?? '')

const COLUMNS: Column[] = [
  { key: 'arrived', title: 'Arrived', hint: 'Waiting to be read', match: (c) => c.state === 'RECEIVED' },
  { key: 'reading', title: 'Reading', hint: 'Prepared; the model is reading it', match: (c) => c.state === 'PREPARED' },
  { key: 'checking', title: 'Checking', hint: 'Read; checked in arrival order, then decided',
    match: (c) => ['EXTRACTED', 'SPLIT'].includes(c.state) && ['EXTRACTED', 'CHECKED', 'MATCHED'].includes(c.invoice_state ?? '') },
  { key: 'auto', title: 'Approved automatically', hint: 'Clean, high confidence, PO matched, under USD 10,000',
    match: (c) => c.invoice_state === 'AUTO_APPROVED' },
  { key: 'review', title: 'Needs review', hint: 'Waiting for you (including held)', match: inReview },
  { key: 'decided', title: 'Decided by you', hint: 'Approved, partly approved or rejected', match: decided },
  { key: 'other', title: 'Not invoices / failed', hint: 'Statements, other documents, unreadable files',
    match: (c) => c.state === 'NOT_INVOICE' || c.state === 'FAILED' },
]

function simulatedDate(board: BoardOut, now: number): string | null {
  const c = board.clock
  if (!c || !c.seconds_per_day) return null
  const elapsed = Math.max(0, (now - new Date(c.start_real).getTime()) / 1000)
  const date = new Date(`${c.start_sim}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + Math.floor(elapsed / c.seconds_per_day))
  return date.toISOString().slice(0, 10)
}

export function BoardPage() {
  const params = useParams({ strict: false }) as { workspaceId?: string }
  const navigate = useNavigate()
  const workspaces = useListWorkspaces()
  const workspaceId = params.workspaceId ? Number(params.workspaceId) : workspaces.data?.[0]?.id
  const queryClient = useQueryClient()
  const live = useLiveEvents(workspaceId, () => {
    if (workspaceId != null) queryClient.invalidateQueries({ queryKey: getGetBoardQueryKey(workspaceId) })
  })
  const board = useGetBoard(workspaceId ?? 0, {
    query: { enabled: workspaceId != null, refetchInterval: live ? 30_000 : 5_000 },
  })
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const columns = useMemo(
    () => COLUMNS.map((col) => ({ ...col, cards: (board.data?.cards ?? []).filter(col.match) })),
    [board.data],
  )

  const b = board.data
  const pending = b ? b.cards.filter((c) => c.state === 'RECEIVED' || c.state === 'PREPARED').length : 0
  const simDate = b ? simulatedDate(b, now) : null

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Inbox board</h1>
          <p className="text-sm text-muted-foreground">
            Every arrival and where it is in the pipeline. Updates live{live ? '' : ' (reconnecting; refreshing every 5 s)'}.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Workspace</span>
          <select
            className="rounded-md border bg-background px-2 py-1 text-sm"
            value={workspaceId ?? ''}
            onChange={(e) => navigate({ to: '/board/$workspaceId', params: { workspaceId: e.target.value } })}
          >
            {(workspaces.data ?? []).map((w) => (
              <option key={w.id} value={w.id}>{w.name} ({w.documents})</option>
            ))}
          </select>
        </label>
      </div>

      <UploadDrop onUploaded={(id) => navigate({ to: '/board/$workspaceId', params: { workspaceId: String(id) } })} />

      {board.isError && <p className="text-sm text-red-700">Couldn't load the board: {errorText(board.error)}</p>}
      {workspaces.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">No inbox yet. Start one with <code>make inbox-live</code>, or drop an invoice above.</p>
      )}
      {!b && workspaceId != null && !board.isError && <Skeleton className="h-96 w-full" />}

      {b && (
        <>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
            {simDate && <span>Simulated date <strong className="tabular-nums">{simDate}</strong></span>}
            <span>
              {b.cards.length}{b.expected ? ` of ${b.expected}` : ''} arrived
              {b.run_status && <span className="text-muted-foreground"> · run {b.run_status}</span>}
            </span>
            <span className={b.worker_online ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground'}>
              {b.worker_online ? '● worker online' : '○ worker offline'}
              {!b.worker_online && pending > 0 && ` — ${pending} waiting; start it with make worker`}
            </span>
          </div>
          {b.llm_paused_until && (
            <div role="status" className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
              The model API asked us to slow down: reading is paused until{' '}
              {new Date(b.llm_paused_until).toLocaleTimeString()}. Nothing is lost; it resumes on its own.
            </div>
          )}
          <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(14rem,1fr))] xl:grid-cols-7">
            {columns.map((col) => (
              <section key={col.key} className="flex min-h-40 flex-col rounded-lg border bg-muted/40" aria-label={col.title}>
                <header className="border-b px-2.5 py-2">
                  <div className="flex items-baseline justify-between">
                    <h2 className="text-sm font-semibold">{col.title}</h2>
                    <span className="text-sm tabular-nums text-muted-foreground">{col.cards.length}</span>
                  </div>
                  <p className="text-xs text-muted-foreground">{col.hint}</p>
                </header>
                <div className="flex max-h-[65vh] flex-col gap-2 overflow-y-auto p-2">
                  {col.cards.map((c) => <BoardCard key={c.document_id} card={c} />)}
                </div>
              </section>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">The next milestone adds payment columns (scheduled, paid).</p>
        </>
      )}
    </div>
  )
}
