import type { EventOut } from '@/api/generated'
import { formatDateTime } from '@/lib/format'
import { cn } from '@/lib/utils'

type Step = { key: string; label: string; match: (e: EventOut) => boolean; detail?: (e: EventOut) => string | undefined }

const to = (e: EventOut) => String(e.data.to ?? '')
const STEPS: Step[] = [
  { key: 'received', label: 'Received', match: (e) => e.action === 'document.received' },
  { key: 'prepared', label: 'Prepared', match: (e) => e.action === 'document.state_changed' && to(e) === 'PREPARED',
    detail: (e) => (e.data.route === 'text_llm' ? 'PDF text layer' : 'page images') },
  { key: 'read', label: 'Read', match: (e) => e.action === 'document.state_changed' && ['EXTRACTED', 'SPLIT', 'NOT_INVOICE', 'FAILED'].includes(to(e)),
    detail: (e) => (e.data.cache_hit ? 'answer reused from an earlier identical call' : String(e.data.model ?? '')) },
  { key: 'checked', label: 'Checked', match: (e) => e.action === 'invoice.state_changed' && to(e) === 'CHECKED',
    detail: (e) => { const r = (e.data.raised as string[] | undefined) ?? []; return r.length ? `${r.length} finding(s)` : 'nothing found' } },
  { key: 'matched', label: 'Matched', match: (e) => e.action === 'invoice.state_changed' && to(e) === 'MATCHED',
    detail: (e) => { const r = (e.data.raised as string[] | undefined) ?? []; return r.length ? r.join(', ') : 'PO and deliveries agree' } },
  { key: 'decided', label: 'Decided', match: (e) => (e.action === 'invoice.state_changed' && ['AUTO_APPROVED', 'NEEDS_REVIEW'].includes(to(e))) || e.action === 'review.decided',
    detail: (e) => (e.action === 'review.decided' ? `by you: ${String(e.data.action).replace('_', ' ')}` : to(e) === 'AUTO_APPROVED' ? 'approved automatically' : 'sent to review') },
  { key: 'planned', label: 'Booked, planned', match: (e) => e.action === 'payment.planned',
    detail: (e) => (e.data.run_date ? `run of ${e.data.run_date}` : 'waiting') },
  { key: 'paid', label: 'Paid', match: (e) => e.action === 'invoice.paid', detail: (e) => `to ${e.data.to}` },
]

// The pipeline steps for one invoice, from its audit trail: done steps show when (and the latest event for the step).
export function StepTimeline({ events, invoiceId }: { events: EventOut[]; invoiceId: number | undefined }) {
  const mine = events.filter((e) => e.subject_type === 'document' || e.subject_id === invoiceId)
  const done = STEPS.map((s) => ({ step: s, event: [...mine].reverse().find(s.match) }))
  const reached = done.reduce((last, d, k) => (d.event ? k : last), -1)
  return (
    <ol className="flex flex-wrap gap-y-3 text-xs" aria-label="Steps">
      {done.map(({ step, event }, k) => (
        <li key={step.key} className="flex min-w-28 flex-1 items-start gap-2">
          <span className={cn('mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border text-[10px]',
            event ? 'border-emerald-600 bg-emerald-600 text-white' : k < reached ? 'border-amber-500 text-amber-600' : 'border-muted-foreground/40')}
                aria-hidden>{event ? '✓' : ''}</span>
          <span>
            <span className={cn('block font-medium', !event && 'text-muted-foreground')}>{step.label}</span>
            {event && <span className="block text-muted-foreground">{formatDateTime(event.occurred_at)}</span>}
            {event && step.detail?.(event) && <span className="block text-muted-foreground">{step.detail(event)}</span>}
            {!event && k < reached && <span className="block text-muted-foreground">skipped</span>}
          </span>
        </li>
      ))}
    </ol>
  )
}
