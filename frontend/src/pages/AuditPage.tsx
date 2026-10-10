import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import type { AuditEventOut } from '@/api/generated'
import { useListAudit, useVerifyAudit } from '@/api/generated'
import { WorkspacePicker, useWorkspace } from '@/components/WorkspacePicker'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { errorText, eventText, formatDateTime } from '@/lib/format'

const ACTOR: Record<string, string> = { user: 'you', system: 'the system', llm: 'the model' }
const PAGE = 100

function Subject({ e }: { e: AuditEventOut }) {
  if (e.subject_id == null) return <>{e.subject_type}</>
  if (e.subject_type === 'document')
    return <Link to="/documents/$documentId" params={{ documentId: String(e.subject_id) }} className="hover:underline">document {e.subject_id}</Link>
  if (e.subject_type === 'invoice')
    return <Link to="/review/$invoiceId" params={{ invoiceId: String(e.subject_id) }} className="hover:underline">invoice {e.subject_id}</Link>
  return <>{e.subject_type.replace('_', ' ')} {e.subject_id}</>
}

export function AuditPage() {
  const workspaceId = useWorkspace()
  const [actor, setActor] = useState('')
  const [action, setAction] = useState('')
  const [page, setPage] = useState(0)
  const verdict = useVerifyAudit(workspaceId ?? 0, { query: { enabled: workspaceId != null } })
  const audit = useListAudit(workspaceId ?? 0, { actor: actor || undefined, action: action || undefined, limit: PAGE,
                                                 offset: page * PAGE }, { query: { enabled: workspaceId != null } })
  const v = verdict.data
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Audit trail</h1>
          <p className="text-sm text-muted-foreground">
            Everything the model, the rules and you did, in order. Events can't be changed or deleted, and each one's
            hash covers the one before it, so any tampering breaks the chain.
          </p>
        </div>
        <WorkspacePicker section="/audit" value={workspaceId} />
      </div>
      {v && (
        <Badge variant="outline" role="status" className={v.ok
          ? 'border-emerald-300 bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200'
          : 'border-red-300 bg-red-50 text-red-900 dark:bg-red-950 dark:text-red-200'}>
          {v.ok ? '✓ ' : '✗ '}{v.words}
        </Badge>
      )}
      <Card>
        <CardContent className="space-y-3 pt-6">
          <div className="flex flex-wrap gap-3 text-sm">
            <label className="flex items-center gap-2">
              <span className="text-muted-foreground">Who</span>
              <select className="rounded-md border bg-background px-2 py-1" value={actor}
                      onChange={(e) => { setActor(e.target.value); setPage(0) }}>
                <option value="">Anyone</option>
                {Object.entries(ACTOR).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2">
              <span className="text-muted-foreground">What</span>
              <select className="rounded-md border bg-background px-2 py-1" value={action}
                      onChange={(e) => { setAction(e.target.value); setPage(0) }}>
                <option value="">Everything</option>
                {(audit.data?.actions ?? []).map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </label>
            {audit.data && <span className="self-center text-muted-foreground">{audit.data.total} events</span>}
          </div>
          {audit.isError && <p className="text-sm text-red-700">{errorText(audit.error)}</p>}
          {audit.isPending ? <Skeleton className="h-64 w-full" /> : audit.data && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-14">#</TableHead>
                  <TableHead className="w-40">When</TableHead>
                  <TableHead className="w-24">Who</TableHead>
                  <TableHead>What</TableHead>
                  <TableHead className="w-32">About</TableHead>
                  <TableHead className="w-24">Hash</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {audit.data.events.map((e) => {
                  const { title, detail } = eventText(e)
                  return (
                    <TableRow key={e.seq} className={v && !v.ok && v.broken_at === e.seq ? 'bg-red-50 dark:bg-red-950/40' : ''}>
                      <TableCell className="align-top text-muted-foreground tabular-nums">{e.seq}</TableCell>
                      <TableCell className="align-top text-xs">{formatDateTime(e.occurred_at)}</TableCell>
                      <TableCell className="align-top">{ACTOR[e.actor] ?? e.actor}</TableCell>
                      <TableCell className="max-w-xl align-top whitespace-normal">
                        <div className="font-medium">{title}</div>
                        {detail && <div className="text-xs text-muted-foreground">{detail}</div>}
                      </TableCell>
                      <TableCell className="align-top text-xs"><Subject e={e} /></TableCell>
                      <TableCell className="align-top font-mono text-xs text-muted-foreground" title={e.hash}>{e.hash.slice(0, 8)}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
          {audit.data && audit.data.total > PAGE && (
            <div className="flex items-center gap-2 text-sm">
              <button type="button" className="rounded border px-2 py-0.5 disabled:opacity-40" disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</button>
              <span className="text-muted-foreground">{page * PAGE + 1}–{Math.min((page + 1) * PAGE, audit.data.total)} of {audit.data.total}</span>
              <button type="button" className="rounded border px-2 py-0.5 disabled:opacity-40" disabled={(page + 1) * PAGE >= audit.data.total} onClick={() => setPage(page + 1)}>Older</button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
