import { type Page, expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { REFS, STATE_FILE } from './global-setup'

type Item = { invoice_id: number; filename: string; labels: string[]; blocks: number }

const workspace = () => (JSON.parse(readFileSync(STATE_FILE, 'utf8')) as { workspace: number }).workspace

async function openReview(page: Page, ref: string) {
  const res = await page.request.get(`/api/review?workspace_id=${workspace()}`)
  const item = ((await res.json()) as Item[]).find((i) => i.filename.startsWith(ref))
  expect(item, `${ref} should be waiting for review`).toBeTruthy()
  await page.goto(`/review/${item!.invoice_id}`)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
}

const status = (page: Page) => page.getByRole('status').first()

test('the board shows the decision for each arrival', async ({ page }) => {
  await page.goto(`/board/${workspace()}`)
  await expect(page.getByRole('region', { name: 'To pay' })).toContainText(REFS.auto) // approved automatically
  const review = page.getByRole('region', { name: 'Needs review' })
  for (const ref of [REFS.partial, REFS.noPo, REFS.bank, REFS.medium]) await expect(review).toContainText(ref)
})

test('the queue puts payment-blocked invoices first', async ({ page }) => {
  await page.goto('/review')
  await page.getByLabel('Workspace').selectOption(String(workspace()))
  const rows = page.getByRole('row')
  await expect(rows).toHaveCount(5) // header + 4
  await expect(rows.nth(1)).toContainText('payment blocked')
  await expect(rows.nth(1)).toContainText('bank details changed')
})

test('approve: a note is required, and a payment block needs an override', async ({ page }) => {
  await openReview(page, REFS.bank)
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(status(page)).toContainText('Add a note')
  await page.getByLabel('Note').fill('Vendor confirmed the new account by phone')
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(status(page)).toContainText("Tick 'override'")
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(status(page)).toContainText('Approved.')
  await expect(page.getByText('approved', { exact: true })).toBeVisible()
  await expect(page.getByText('Vendor confirmed the new account by phone')).toBeVisible() // in the history
})

test('correct: a typed value is checked again, and a word picked on the page fixes it', async ({ page }) => {
  await openReview(page, REFS.medium)
  const correctPo = page.getByRole('button', { name: 'Correct po number' })
  await correctPo.click()
  await page.getByLabel('New value for PO number').fill('4500463189')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('PO 4500463189 is not in our purchase orders.').first()).toBeVisible()

  await correctPo.click()
  await page.locator('svg[aria-label^="Words on page 1"] rect', { has: page.locator('title', { hasText: /^4500463188$/ }) })
    .first().click()
  await expect(page.getByLabel('New value for PO number')).toHaveValue('4500463188')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(status(page)).toContainText('checks ran again')
  await expect(page.getByText('is not in our purchase orders')).toHaveCount(0)
  await expect(page.getByText('corrected', { exact: true })).toBeVisible()
})

test('reject with the keyboard', async ({ page }) => {
  await openReview(page, REFS.noPo)
  await page.locator('body').press('r')
  await expect(page.getByLabel('Note')).toBeFocused()
  await page.getByLabel('Note').fill('Not one of our shipments')
  await page.getByLabel('Note').press('Control+Enter')
  await expect(status(page)).toContainText('Rejected.')
  await expect(page.getByText('rejected', { exact: true })).toBeVisible()
})

test('approve the received part, then the rest', async ({ page }) => {
  await openReview(page, REFS.partial)
  await expect(page.getByText('Received part: USD 1,714.23 of USD 1,759.59')).toBeVisible()
  await page.getByLabel('Note').fill('Pay for the 2 binder packs delivered')
  await page.getByRole('button', { name: 'Approve received part' }).click()
  await expect(status(page)).toContainText('Received part approved.')
  await expect(page.getByText('partly approved', { exact: true })).toBeVisible()
  await expect(page.getByText('USD 45.36 is on hold')).toBeVisible()

  await page.getByLabel('Note').fill('The third pack arrived')
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(status(page)).toContainText('Approved.')
  await expect(page.getByText('approved', { exact: true })).toBeVisible()
})

test('pay the planned runs, and the books balance', async ({ page }) => {
  // after the approvals above: the automatic one, the bank-change override, and the partial one and its rest
  await page.goto(`/payments/${workspace()}`)
  const next = page.getByRole('button', { name: /^Pay the next run/ })
  for (let k = 0; k < 10 && (await next.isEnabled()); k++) {
    await next.click()
    await expect(status(page)).toContainText('Paid the run of')
  }
  await expect(next).toBeDisabled() // nothing left planned
  await page.goto(`/board/${workspace()}`)
  const paid = page.getByRole('region', { name: 'Paid' })
  for (const ref of [REFS.auto, REFS.bank, REFS.partial]) await expect(paid).toContainText(ref)
  await page.goto(`/ledger/${workspace()}`)
  await expect(page.getByText(/debits = credits/).first()).toBeVisible()
  await expect(page.getByText(/payables ≠/)).toHaveCount(0)
  await page.goto(`/audit/${workspace()}`)
  await expect(page.getByRole('status')).toContainText('chain verified')
})
