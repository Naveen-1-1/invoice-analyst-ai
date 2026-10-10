import { useEffect, useRef } from 'react'
import type { FieldOut } from '@/api/generated'
import { TierBadge } from '@/components/TierBadge'
import { Button } from '@/components/ui/button'
import { FIELD_LABELS, formatValue } from '@/lib/format'
import { cn } from '@/lib/utils'

const CLEARABLE = new Set(['po_number', 'vendor_tax_id', 'due_date'])
const PLACEHOLDER: Record<string, string> = { invoice_date: 'YYYY-MM-DD', due_date: 'YYYY-MM-DD', currency: 'e.g. ZAR' }

type Props = {
  fields: FieldOut[]
  correctable: string[]
  currency?: string | null
  canCorrect: boolean
  editing: string | null
  draft: string
  saving: boolean
  hoveredId: number | null
  onHover: (id: number | null) => void
  onEdit: (field: string | null) => void
  onDraft: (text: string) => void
  onSave: () => void
}

// The header values a reviewer can correct. While one is being corrected, the words on the page become clickable.
export function CorrectableFields(p: Props) {
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (p.editing) input.current?.focus()
  }, [p.editing])

  return (
    <ul className="divide-y text-sm">
      {p.correctable.map((name) => {
        const f = p.fields.find((x) => x.field === name && x.line_no == null)
        const editing = p.editing === name
        return (
          <li
            key={name}
            className={cn('py-2', f && f.id === p.hoveredId && 'bg-accent')}
            onMouseEnter={() => f && p.onHover(f.id)}
            onMouseLeave={() => p.onHover(null)}
          >
            <div className="flex items-start gap-3">
              <div className="w-32 shrink-0 pt-0.5 text-muted-foreground">{FIELD_LABELS[name] ?? name}</div>
              <div className="min-w-0 flex-1">
                {editing ? (
                  <form
                    className="flex flex-wrap items-center gap-2"
                    onSubmit={(e) => {
                      e.preventDefault()
                      p.onSave()
                    }}
                  >
                    <input
                      ref={input}
                      aria-label={`New value for ${FIELD_LABELS[name] ?? name}`}
                      className="h-8 min-w-0 flex-1 rounded-md border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      value={p.draft}
                      placeholder={PLACEHOLDER[name]}
                      onFocus={(e) => e.target.select()}
                      onChange={(e) => p.onDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') p.onEdit(null)
                      }}
                    />
                    <Button type="submit" size="sm" disabled={p.saving}>{p.saving ? 'Checking…' : 'Save'}</Button>
                    <Button type="button" size="sm" variant="ghost" onClick={() => p.onEdit(null)}>Cancel</Button>
                    <p className="w-full text-xs text-muted-foreground">
                      Type it, or click the words on the page (shift-click adds the next word).
                      {CLEARABLE.has(name) && ' Leave it empty if the invoice has none.'} Saving runs the checks again.
                    </p>
                  </form>
                ) : (
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-medium break-words">{f ? formatValue(name, f.value, p.currency) : '—'}</span>
                    {f?.source === 'human' && (
                      <span className="rounded border border-sky-300 bg-sky-50 px-1 text-xs text-sky-900 dark:bg-sky-950 dark:text-sky-200">
                        corrected
                      </span>
                    )}
                    {f?.raw_text && f.source !== 'human' && f.raw_text !== String(f.value) && (
                      <span className="text-xs text-muted-foreground">printed “{f.raw_text}”</span>
                    )}
                  </div>
                )}
              </div>
              {!editing && (
                <div className="flex shrink-0 items-center gap-2">
                  {f && <TierBadge tier={f.tier} />}
                  {p.canCorrect && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7"
                      aria-label={`Correct ${(FIELD_LABELS[name] ?? name).toLowerCase()}`}
                      onClick={() => {
                        p.onDraft(f?.value == null ? '' : String(f.value))
                        p.onEdit(name)
                      }}
                    >
                      Correct
                    </Button>
                  )}
                </div>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
