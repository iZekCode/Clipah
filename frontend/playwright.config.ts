import { defineConfig, devices } from '@playwright/test'

/**
 * Browser tests run against a real stack: Postgres, the FastAPI backend, and this
 * application. Nothing here is stubbed, because the point of these tests is the parts a
 * component test cannot see — cookies, redirects, and the backend's own refusals.
 */
const baseURL = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL,
    trace: 'retain-on-failure',
  },
  // Two engines are named because one of the adoption gates in `plan.md` is about how
  // Safari degrades when a codec is unsupported, and only WebKit can answer that.
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
  // A production build, not `pnpm dev`. The development server compiles a route the first
  // time it is asked for, and that compilation races every assertion with a five-second
  // timeout — a flake that says nothing about the application. Building first costs a
  // minute once and then serves each route immediately.
  webServer: process.env.CLIPAH_E2E_BASE_URL
    ? undefined
    : {
        command: 'pnpm build && pnpm start',
        url: baseURL,
        reuseExistingServer: true,
        timeout: 300_000,
      },
})
