import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const STYLES: Record<string, string> = {
  high: 'bg-emerald-100 text-emerald-900 border-emerald-300 dark:bg-emerald-950 dark:text-emerald-200',
  medium: 'bg-amber-100 text-amber-900 border-amber-300 dark:bg-amber-950 dark:text-amber-200',
  low: 'bg-red-100 text-red-900 border-red-300 dark:bg-red-950 dark:text-red-200',
}

// Confidence tier as a labelled chip (the word is always shown, never colour alone).
export function TierBadge({ tier, confidence, className }: { tier?: string | null; confidence?: number | null; className?: string }) {
  if (!tier) return <span className="text-xs text-muted-foreground">—</span>
  const pct = confidence != null ? `${Math.round(confidence * 1000) / 10}%` : null
  return (
    <Badge
      variant="outline"
      className={cn('font-medium', STYLES[tier], className)}
      title={pct ? `Fields rated ${tier} were right ${pct} of the time on the dev set` : undefined}
    >
      {tier}
      {pct && ` · ${pct}`}
    </Badge>
  )
}

export const TIER_STROKE: Record<string, string> = {
  high: '#059669',
  medium: '#d97706',
  low: '#dc2626',
}
