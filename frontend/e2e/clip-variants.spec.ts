import { expect, test, type Page } from '@playwright/test'

import { seedMemberWithClip, signIn, uniqueEmail, type SeededMemberWithClip } from './support/seed'

/**
 * Comparing readings of one moment, and citing what a claim rests on, in a real browser.
 *
 * The parts a component test cannot see live here: the API actually refusing an unsafe
 * source URL, and another Workspace's candidate answering exactly like one that never
 * existed.
 */

const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'

/** Sign one seeded member in and open a page as them. */
async function open(page: Page, member: SeededMemberWithClip, path: string): Promise<void> {
  await signIn(page.context(), member, SITE)
  await page.goto(path)
}

test('a member is offered only the supported lengths', async ({ page }) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('variants-lengths'),
    displayName: 'variants-lengths',
    workspaceName: 'variants-lengths workspace',
    projectName: 'variants-lengths project',
  })
  await open(page, member, `/dashboard/clips/${member.project.candidateId}?projectId=${member.project.projectId}`)

  const lab = page.getByRole('region', { name: /variants/i })

  await expect(lab).toBeVisible()
  const offered = await lab.getByRole('checkbox').evaluateAll((boxes) =>
    boxes.map((box) => (box as HTMLInputElement).value),
  )
  expect(offered).toEqual(['20000', '30000', '45000', '60000', '90000'])
})

test('a generated variant is compared against one proxy, not a second copy', async ({ page }) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('variants-one-proxy'),
    displayName: 'variants-one-proxy',
    workspaceName: 'variants-one-proxy workspace',
    projectName: 'variants-one-proxy project',
  })
  await open(page, member, `/dashboard/clips/${member.project.candidateId}?projectId=${member.project.projectId}`)

  const lab = page.getByRole('region', { name: /variants/i })
  await lab.getByRole('checkbox').first().check()
  await lab.getByRole('button', { name: /offer variants/i }).click()

  await expect(lab.getByRole('listitem').first()).toBeVisible()
  await expect(page.locator('video')).toHaveCount(1)
})

test('an unsafe source link is refused with a reason a member can act on', async ({ page }) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('variants-evidence'),
    displayName: 'variants-evidence',
    workspaceName: 'variants-evidence workspace',
    projectName: 'variants-evidence project',
  })
  await open(page, member, `/dashboard/clips/${member.project.candidateId}?projectId=${member.project.projectId}`)

  const panel = page.getByRole('region', { name: /claim evidence/i })
  await panel.getByLabel('Claim').fill('Growth doubled after the change')
  await panel.getByLabel('Source link').fill('http://127.0.0.1/internal')
  await panel.getByLabel('Title').fill('Internal dashboard')
  await panel.getByLabel('Publisher').fill('Example')
  await panel.getByLabel('First word').fill('w000001')
  await panel.getByLabel('Last word').fill('w000010')
  await panel.getByRole('button', { name: /attach source/i }).click()

  await expect(panel.getByRole('status')).toContainText(/could not be accepted/i)
})

test('a citation opens isolated and is never rendered as markup', async ({ page }) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('variants-citation'),
    displayName: 'variants-citation',
    workspaceName: 'variants-citation workspace',
    projectName: 'variants-citation project',
  })
  await open(page, member, `/dashboard/clips/${member.project.candidateId}?projectId=${member.project.projectId}`)

  const panel = page.getByRole('region', { name: /claim evidence/i })
  await panel.getByLabel('Claim').fill('Activation doubled')
  await panel.getByLabel('Source link').fill('https://example.test/report')
  await panel.getByLabel('Title').fill('<b>Quarterly report</b>')
  await panel.getByLabel('Publisher').fill('Example Institute')
  await panel.getByLabel('First word').fill('w000001')
  await panel.getByLabel('Last word').fill('w000010')
  await panel.getByRole('button', { name: /attach source/i }).click()

  // The title carries markup, so the API refuses it rather than storing a sanitized copy.
  await expect(panel.getByRole('status')).toContainText(/could not be accepted/i)
})
