import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'

import { seedMemberWithClip, signIn, uniqueEmail } from './support/seed'

/**
 * Design screenshots of every route at the three widths the redesign is reviewed at.
 *
 * Opt-in: nothing runs unless `CLIPAH_CAPTURE_SCREENS` names the folder under
 * `docs/design/signal/` to write into. The same run proves no route scrolls sideways.
 */
const PHASE = process.env.CLIPAH_CAPTURE_SCREENS
const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'
const WIDTHS = [1440, 820, 390] as const
const OUTPUT = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../docs/design/signal',
  PHASE ?? 'unset',
)

test.skip(PHASE === undefined, 'set CLIPAH_CAPTURE_SCREENS=<folder> to capture screenshots')

test('every route renders without sideways scrolling at each review width', async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== 'chromium', 'screenshots are captured once, in Chromium')
  test.setTimeout(600_000)
  mkdirSync(OUTPUT, { recursive: true })

  const member = await seedMemberWithClip({
    email: uniqueEmail('design-screens'),
    displayName: 'Maya Creator',
    workspaceName: "Maya's Studio",
    projectName: 'Podcast episode 42',
  })
  await signIn(page.context(), member, SITE)
  const { projectId, candidateId } = member.project
  const created = await page.request.post(
    `/api/v1/projects/${projectId}/candidates/${candidateId}/edits?workspace_id=${member.workspaceId}`,
    {
      headers: { 'X-CSRF-Token': member.csrfToken, Origin: SITE },
      data: { templateId: null, brandKitId: null },
    },
  )
  expect(created.ok()).toBe(true)
  const editId = ((await created.json()) as { id: string }).id

  const routes: Array<[string, string]> = [
    ['landing', '/'],
    ['signin', '/signin'],
    ['demo', '/demo'],
    ['home', '/dashboard'],
    ['projects', '/dashboard/projects'],
    ['project', `/dashboard/projects/${projectId}`],
    ['review', `/dashboard/projects/${projectId}/review`],
    ['clips', '/dashboard/clips'],
    ['clip', `/dashboard/clips/${candidateId}`],
    ['publishing', '/dashboard/publishing'],
    ['publishing-new', '/dashboard/publishing/new'],
    ['assets', '/dashboard/assets'],
    ['templates', '/dashboard/templates'],
    ['brand-kits', '/dashboard/brand-kits'],
    ['settings', '/dashboard/settings'],
    ['team', '/dashboard/team'],
    ['connections', '/dashboard/settings/connections'],
    ['search', '/dashboard/search?q=moment'],
    ['editor', `/editor/${editId}`],
  ]

  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
    for (const [name, path] of routes) {
      await visit(page, path)
      await page.screenshot({ path: `${OUTPUT}/${name}-${width}.png`, fullPage: true })
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      )
      expect(overflow, `${name} at ${width}px scrolls sideways`).toBeLessThanOrEqual(0)
    }
  }
})

/** How long the API's sliding read window lasts, plus a margin. */
const RATE_WINDOW_MS = 61_000

/**
 * Open one route and wait until the network is quiet, so a screenshot shows data rather
 * than skeletons.
 *
 * Fifty-four page loads by one member exceed the API's per-minute read limit, and a
 * refused read would put an error notice in the design record. When any read is refused
 * the route is loaded again once the window has moved on.
 */
async function visit(page: Page, path: string): Promise<void> {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    let refused = false
    const onResponse = (response: { status(): number }) => {
      if (response.status() === 429) refused = true
    }
    page.on('response', onResponse)
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(400)
    page.off('response', onResponse)
    if (!refused) return
    await page.waitForTimeout(RATE_WINDOW_MS)
  }
  throw new Error(`${path} kept being refused by the rate limit`)
}
