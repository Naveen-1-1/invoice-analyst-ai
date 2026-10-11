import { defineConfig } from '@playwright/test'

// Web tests of the review flow. They run against the dev servers (make api, make web; started here if they aren't
// running) and a fresh workspace replayed from the dev inbox's cached readings (no model calls), set up in
// e2e/global-setup.ts. They use the installed Google Chrome, so no browser download is needed.
export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5180',
    channel: 'chrome',
    viewport: { width: 1400, height: 900 },
    trace: 'retain-on-failure',
  },
  webServer: [
    { command: 'make -C .. api', url: 'http://127.0.0.1:8000/api/workspaces', reuseExistingServer: true, timeout: 60_000 },
    { command: 'npm run dev', url: 'http://127.0.0.1:5180', reuseExistingServer: true, timeout: 60_000 },
  ],
})
