import { useListModelCalls } from '@/api/generated'
import { Skeleton } from '@/components/ui/skeleton'
import { errorText, formatDateTime } from '@/lib/format'

// Decision 50: every model call made for this file, folded away until opened.
export function ModelCalls({ documentId }: { documentId: number }) {
  const calls = useListModelCalls(documentId)
  if (calls.isPending) return <Skeleton className="h-32 w-full" />
  if (calls.isError) return <p className="text-sm text-red-700">{errorText(calls.error)}</p>
  if (!calls.data.length) return <p className="text-sm text-muted-foreground">No model call has been made for this file.</p>
  return (
    <div className="space-y-2 text-sm">
      <p className="text-muted-foreground">
        Newest first. A reading reused from the cache comes from a call made for an earlier copy of the same file.
        Page images aren't kept with the call (only their size); the pages are shown on the left.
      </p>
      {calls.data.map((c) => (
        <details key={c.id} className="rounded-md border" open={calls.data.length === 1}>
          <summary className="cursor-pointer px-3 py-2">
            <span className="font-medium">Call {c.id}</span>{' '}
            <span className="text-muted-foreground">
              {c.model} · {formatDateTime(c.created_at)} · {c.ok ? 'ok' : c.http_status === 200 ? 'answer incomplete, asked again' : `failed${c.http_status ? ` (${c.http_status})` : ''}`}
              {c.latency_ms != null && ` · ${(c.latency_ms / 1000).toFixed(1)} s`}
              {c.tokens_in != null && ` · ${c.tokens_in.toLocaleString()} in, ${c.tokens_out?.toLocaleString() ?? '?'} out`}
              {c.attempt > 1 && ` · attempt ${c.attempt}`}
              {c.this_document ? ' · for this document' : ` · for an earlier copy (${c.workspace ?? 'another workspace'})`}
            </span>
          </summary>
          <div className="space-y-3 border-t px-3 py-2">
            <div className="text-xs text-muted-foreground">
              Settings: {Object.entries(c.params).filter(([k]) => k !== 'tool_choice').map(([k, v]) => `${k} ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ') || '—'}
              {c.finish_reason && ` · finished: ${c.finish_reason}`}
            </div>
            {c.error && <p className="text-red-700 dark:text-red-400">{c.error}</p>}
            {c.messages.map((m, k) => (
              <section key={k}>
                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{m.role === 'system' ? 'Instructions' : 'Sent'}</h4>
                {m.parts.map((p, j) => p.kind === 'text'
                  ? <pre key={j} className="mt-1 max-h-60 overflow-auto rounded bg-muted p-2 text-xs whitespace-pre-wrap">{p.text}</pre>
                  : <p key={j} className="mt-1 text-xs text-muted-foreground">[page image, sent as {p.image_bytes ? `${(p.image_bytes / 1024).toFixed(1)} KB` : 'an image'}]</p>)}
              </section>
            ))}
            {c.reasoning && (
              <section>
                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">The model's reasoning</h4>
                <pre className="mt-1 max-h-60 overflow-auto rounded bg-muted p-2 text-xs whitespace-pre-wrap">{c.reasoning}</pre>
              </section>
            )}
            {c.answer && (
              <section>
                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">The answer</h4>
                <pre className="mt-1 max-h-80 overflow-auto rounded bg-muted p-2 text-xs">{c.answer}</pre>
              </section>
            )}
          </div>
        </details>
      ))}
    </div>
  )
}
