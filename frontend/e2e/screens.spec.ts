import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { STATE_FILE } from './global-setup'

const workspace = () => (JSON.parse(readFileSync(STATE_FILE, 'utf8')) as { workspace: number }).workspace

test('vendors, a PO with its lines and deliveries, and back to the vendor', async ({ page }) => {
  await page.goto(`/vendors/${workspace()}`)
  await expect(page.getByRole('heading', { name: 'Vendors, orders and deliveries' })).toBeVisible()
  await page.getByRole('tab', { name: 'Purchase orders' }).click()
  const po = page.getByRole('tabpanel').getByRole('link').first()
  const number = (await po.textContent()) ?? ''
  await po.click()
  await expect(page.getByRole('heading', { name: `PO ${number}` })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Received' })).toBeVisible()
  await expect(page.getByText(/^Deliveries \(\d+\)$/)).toBeVisible()
  await page.locator('main p').first().getByRole('link').click() // the vendor, above the title
  await expect(page.getByText('Bank accounts')).toBeVisible()
})

test('settings show each value', async ({ page }) => {
  await page.goto('/settings')
  await expect(page.getByText(/at most USD 10,000/)).toBeVisible()
})

test('a document shows its steps and the model calls behind its reading', async ({ page }) => {
  const res = await page.request.get(`/api/workspaces/${workspace()}/board`)
  const card = ((await res.json()).cards as { document_id: number }[])[0]
  await page.goto(`/documents/${card.document_id}`)
  const steps = page.getByRole('list', { name: 'Steps' })
  await expect(steps).toContainText('Received')
  await expect(steps).toContainText('Read')
  await page.getByRole('tab', { name: 'Model calls' }).click()
  await expect(page.getByText(/^Call \d+$/).first()).toBeVisible()
})

test('the evaluation page compares two runs and charts calibration', async ({ page }) => {
  await page.goto('/evaluation')
  await expect(page.getByText('Calibration: how often each tier was right')).toBeVisible()
  await expect(page.getByText(/Documents fully right: \d+ → \d+ of \d+/)).toBeVisible()
})
