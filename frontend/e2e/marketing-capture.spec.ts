import { expect, test } from '@playwright/test'

/**
 * Opt-in: records the product loop and stills for the public pages from a real account.
 *
 * `CLIPAH_MARKETING_STORAGE_STATE` is a Playwright storage state saved after signing in by
 * hand (`pnpm exec playwright codegen --save-storage=…`), and `CLIPAH_MARKETING_PROJECT_ID`
 * names a Project with real media and moments. Nothing runs without both.
 */
const STATE = process.env.CLIPAH_MARKETING_STORAGE_STATE
const PROJECT = process.env.CLIPAH_MARKETING_PROJECT_ID

test.skip(STATE === undefined || PROJECT === undefined, 'marketing capture is opt-in')
test.use({
  storageState: STATE,
  viewport: { width: 1440, height: 900 },
  video: { mode: 'on', size: { width: 1440, height: 900 } },
})

test('record review mode and the editor', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto(`/dashboard/projects/${PROJECT}/review`)
  await expect(page.getByRole('region', { name: 'Moment' })).toBeVisible()
  await page.waitForTimeout(1_500)
  await page.screenshot({ path: 'public/marketing/review.png' })
  await page.keyboard.press('j')
  await page.waitForTimeout(1_500)
  await page.keyboard.press('j')
  await page.waitForTimeout(1_500)
  await page.keyboard.press('e')
  await expect(page).toHaveURL(/\/editor\//)
  await expect(page.getByRole('region', { name: /^timeline$/i })).toBeVisible()
  await page.waitForTimeout(1_500)
  await page.screenshot({ path: 'public/marketing/edit.png' })
  await page.keyboard.press(' ')
  await page.waitForTimeout(3_000)
  await page.goto('/dashboard/publishing')
  await page.waitForTimeout(1_500)
  await page.screenshot({ path: 'public/marketing/publish.png' })
})
