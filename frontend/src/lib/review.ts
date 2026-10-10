// Words for invoice states and reviewer actions, shared by the board, the queue and the review screen.

export const STATE_LABELS: Record<string, string> = {
  EXTRACTED: 'read, waiting for checks',
  CHECKED: 'checked',
  MATCHED: 'checked, waiting for a decision',
  AUTO_APPROVED: 'approved automatically',
  NEEDS_REVIEW: 'needs review',
  ON_HOLD: 'on hold',
  APPROVED: 'approved',
  APPROVED_PARTIAL: 'partly approved',
  REJECTED: 'rejected',
}

export const STATE_STYLES: Record<string, string> = {
  AUTO_APPROVED: 'bg-emerald-100 text-emerald-900 border-emerald-300 dark:bg-emerald-950 dark:text-emerald-200',
  APPROVED: 'bg-emerald-100 text-emerald-900 border-emerald-300 dark:bg-emerald-950 dark:text-emerald-200',
  APPROVED_PARTIAL: 'bg-sky-100 text-sky-900 border-sky-300 dark:bg-sky-950 dark:text-sky-200',
  NEEDS_REVIEW: 'bg-amber-100 text-amber-900 border-amber-300 dark:bg-amber-950 dark:text-amber-200',
  ON_HOLD: 'bg-slate-100 text-slate-900 border-slate-300 dark:bg-slate-900 dark:text-slate-200',
  REJECTED: 'bg-red-100 text-red-900 border-red-300 dark:bg-red-950 dark:text-red-200',
}

export type Action = 'approve' | 'approve_partial' | 'reject' | 'hold' | 'release' | 'reprocess'

// key: the keyboard shortcut (when the cursor isn't in a text box)
export const ACTIONS: { action: Action; label: string; key: string; hint: string }[] = [
  { action: 'approve', label: 'Approve', key: 'a', hint: 'Pay it in full' },
  { action: 'approve_partial', label: 'Approve received part', key: 'p', hint: 'Pay what was delivered; hold the rest' },
  { action: 'reject', label: 'Reject', key: 'r', hint: "Don't pay it" },
  { action: 'hold', label: 'Hold', key: 'h', hint: 'Park it for now' },
  { action: 'release', label: 'Release', key: 'h', hint: 'Bring it back to review' },
  { action: 'reprocess', label: 'Read again', key: 'x', hint: 'Have the model read it again (ignores the cache)' },
]

export const DONE_WORDS: Record<Action, string> = {
  approve: 'Approved',
  approve_partial: 'Received part approved',
  reject: 'Rejected',
  hold: 'Put on hold',
  release: 'Released back to review',
  reprocess: 'Queued to be read again',
}

export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  return Boolean(el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)))
}
