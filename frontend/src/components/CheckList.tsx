import type { CheckOut } from '@/api/generated'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const SEVERITY: Record<string, { label: string; className: string; order: number }> = {
  block: { label: 'blocks payment', className: 'bg-red-100 text-red-900 border-red-300 dark:bg-red-950 dark:text-red-200', order: 0 },
  review: { label: 'needs review', className: 'bg-amber-100 text-amber-900 border-amber-300 dark:bg-amber-950 dark:text-amber-200', order: 1 },
  info: { label: 'note', className: 'bg-muted text-muted-foreground', order: 2 },
}

const CATEGORY_LABELS: Record<string, string> = {
  arithmetic: 'Arithmetic',
  doctype: 'Document type',
  duplicate: 'Duplicates',
  vendor: 'Vendor',
  bank: 'Bank details',
  match: 'Three-way match',
  pattern: 'Patterns',
}

// What the checks found: problems first (blocks, then reviews, then notes), then what passed.
export function CheckList({ checks }: { checks: CheckOut[] }) {
  if (checks.length === 0) return <p className="text-sm text-muted-foreground">No checks have run on this invoice yet.</p>
  const raised = checks
    .filter((c) => c.outcome === 'fail' || c.outcome === 'warn')
    .sort((a, b) => (SEVERITY[a.severity]?.order ?? 9) - (SEVERITY[b.severity]?.order ?? 9))
  const passed = checks.filter((c) => c.outcome === 'pass')
  const skipped = checks.filter((c) => c.outcome === 'skip')
  return (
    <div className="space-y-5 text-sm">
      <section>
        <h3 className="mb-2 font-semibold">
          {raised.length ? `${raised.length} finding${raised.length === 1 ? '' : 's'}` : 'Nothing to look at'}
        </h3>
        <ul className="space-y-2">
          {raised.map((c) => {
            const sev = SEVERITY[c.severity] ?? SEVERITY.info
            return (
              <li key={c.id} className="rounded-md border p-2.5">
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <Badge variant="outline" className={cn('font-medium', sev.className)}>{sev.label}</Badge>
                  <span className="text-xs text-muted-foreground">{CATEGORY_LABELS[c.category] ?? c.category} · {c.code}</span>
                </div>
                <p>{c.message}</p>
              </li>
            )
          })}
        </ul>
      </section>
      {passed.length > 0 && (
        <section>
          <h3 className="mb-2 font-semibold">Passed</h3>
          <ul className="space-y-1">
            {passed.map((c) => (
              <li key={c.id} className="flex gap-2">
                <span className="w-3 shrink-0 font-bold text-emerald-700 dark:text-emerald-400" aria-label="passed">✓</span>
                <span>{c.message}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {skipped.length > 0 && (
        <section>
          <h3 className="mb-2 font-semibold text-muted-foreground">Not applicable</h3>
          <ul className="space-y-1 text-muted-foreground">
            {skipped.map((c) => <li key={c.id}>{c.message}</li>)}
          </ul>
        </section>
      )}
    </div>
  )
}
