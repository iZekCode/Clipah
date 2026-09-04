import { expect, test } from '@playwright/test'

import { seedMember, signIn, uniqueEmail } from './support/seed'

/**
 * The advanced editor in a real browser, against a real backend.
 *
 * The parts a component test cannot see live here: the Project media endpoint refusing a
 * Workspace the caller has no standing on, and the backend refusing the documents this
 * editor is careful never to produce — an item below the minimum length, two items
 * sharing an instant on one lane, and content timed past the clip. A composition that
 * would be refused on save is a composition the timeline must never build.
 */

const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'
const UNKNOWN_ID = '99999999-9999-4999-8999-999999999999'

test("another Workspace's media is not listed for the assets panel", async ({ page }) => {
  const member = await seedMember({
    email: uniqueEmail('editor-assets'),
    displayName: 'Editing Member',
    workspaceName: 'Editing Workspace',
  })
  await signIn(page.context(), member, SITE)

  const refused = await page.request.get(`/api/v1/projects/${UNKNOWN_ID}/assets`, {
    params: { workspace_id: member.workspaceId },
  })

  expect(refused.status()).toBe(404)
  expect((await refused.json()).error.code).toBe('NOT_FOUND')
})

test('the assets panel is not reachable without a session', async ({ page }) => {
  const anonymous = await page.request.get(`/api/v1/projects/${UNKNOWN_ID}/assets`, {
    params: { workspace_id: UNKNOWN_ID },
  })

  expect(anonymous.status()).toBe(401)
})

test('an editor route still asks an anonymous visitor to sign in', async ({ page }) => {
  await page.goto(`/editor/${UNKNOWN_ID}`)

  await expect(page.getByRole('link', { name: /sign in/i })).toBeVisible()
  await expect(page.getByRole('region', { name: /^timeline$/i })).toHaveCount(0)
})

test('an Edit of another Workspace refuses every save this editor could send', async ({
  page,
}) => {
  const member = await seedMember({
    email: uniqueEmail('editor-advanced-outsider'),
    displayName: 'Outside Member',
    workspaceName: 'Outside Workspace',
  })
  await signIn(page.context(), member, SITE)

  const refused = await page.request.put(`/api/v1/edits/${UNKNOWN_ID}`, {
    headers: { 'X-CSRF-Token': member.csrfToken, Origin: SITE },
    params: { workspace_id: member.workspaceId },
    data: { expectedRevision: 1, composition: {} },
  })

  expect(refused.status()).toBe(404)
})

// Driving the timeline itself needs a Project that has been ingested, transcribed, and
// analysed, because the clip a member edits is produced by the pipeline and no API can
// stage a Clip Candidate. These scenarios belong with the seeding helper that can drive
// that pipeline end to end, which the repository owner runs before Phase C is signed off.
test.fixme('a member drags, snaps, and ripple-deletes on a real clip', async () => {})
test.fixme('a member adds a music bed and a text overlay and exports the result', async () => {})
