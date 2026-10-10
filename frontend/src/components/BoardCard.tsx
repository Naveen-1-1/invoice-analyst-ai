import { Link } from '@tanstack/react-router'
import type { CardOut } from '@/api/generated'
import { TierBadge } from '@/components/TierBadge'
import { formatMoney } from '@/lib/format'

// One arrival on the board: what it is, how sure we are, and what the checks found.
export function BoardCard({ card }: { card: CardOut }) {
  const blocks = card.findings.block ?? 0
  const reviews = card.findings.review ?? 0
  return (
    <Link
      to="/documents/$documentId"
      params={{ documentId: String(card.document_id) }}
      className="block rounded-md border bg-card p-2.5 text-xs shadow-xs transition-colors hover:bg-accent"
    >
      <div className="mb-1 flex items-start justify-between gap-2">
        <span className="truncate font-medium" title={card.ref ?? card.filename}>
          {card.seq != null && <span className="text-muted-foreground">#{card.seq} </span>}
          {card.ref ?? card.filename}
        </span>
        {card.tier && <TierBadge tier={card.tier} className="shrink-0 px-1.5 py-0 text-[10px]" />}
      </div>
      {card.vendor && <div className="truncate text-muted-foreground">{card.vendor}</div>}
      {card.total != null && (
        <div className="tabular-nums">
          {formatMoney(card.total, card.currency)}
          {card.invoices > 1 && <span className="text-muted-foreground"> · {card.invoices} invoices</span>}
        </div>
      )}
      {(blocks > 0 || reviews > 0) && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {blocks > 0 && (
            <span className="rounded border border-red-300 bg-red-100 px-1 text-red-900 dark:bg-red-950 dark:text-red-200">
              {blocks} block{blocks > 1 ? 's' : ''}
            </span>
          )}
          {reviews > 0 && (
            <span className="rounded border border-amber-300 bg-amber-100 px-1 text-amber-900 dark:bg-amber-950 dark:text-amber-200">
              {reviews} to review
            </span>
          )}
          {card.codes.slice(0, 2).map((c) => (
            <span key={c} className="rounded bg-muted px-1 text-muted-foreground">{c}</span>
          ))}
        </div>
      )}
    </Link>
  )
}
