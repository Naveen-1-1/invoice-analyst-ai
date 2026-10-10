import { execFileSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
export const STATE_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), '.workspace.json')

// The five dev arrivals the tests use, one per review situation (replayed from cached readings: no model calls).
export const REFS = {
  auto: 'clean-us-net30-terms', // clean, PO matched, high confidence: approved automatically
  partial: 'clean-us-many-line-items', // 3 binder packs invoiced, 2 received
  noPo: 'freight-clearing-za-4', // no PO number
  bank: 'freight-carrier-za-4', // bank details differ from the vendor list: blocks payment
  medium: 'tax-inclusive-total-na', // subtotal worked out, not printed: medium confidence
}

export default function globalSetup() {
  const out = execFileSync(
    path.join(ROOT, 'backend/.venv/bin/python'),
    ['-m', 'invoice_analyst', 'inbox', 'run', '--set', 'dev', '--label', 'e2e', '--speed', '0', '--worker',
     '--only', Object.values(REFS).join(',')],
    { cwd: ROOT, env: { ...process.env, PYTHONPATH: path.join(ROOT, 'backend/src') }, encoding: 'utf8' },
  )
  const match = out.match(/Inbox run (\d+) \(workspace (\d+)\)/)
  if (!match || !out.includes("'status': 'finished'")) throw new Error(`couldn't set up the e2e workspace:\n${out}`)
  writeFileSync(STATE_FILE, JSON.stringify({ run: Number(match[1]), workspace: Number(match[2]) }))
}
