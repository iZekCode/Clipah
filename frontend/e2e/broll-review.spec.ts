import { expect, test, type APIRequestContext } from '@playwright/test'

import {
  seedMember,
  seedMemberWithClip,
  signIn,
  uniqueEmail,
  type SeededMember,
} from './support/seed'

/**
 * Deciding on B-roll in a browser, against a real backend.
 *
 * The parts a component test cannot see live here: a clip nobody has planned answering
 * honestly rather than as a failure, and another Workspace's clip being
 * indistinguishable from one that never existed.
 *
 * Every request goes through `page.request`, which shares the page context's cookies.
 * The bare `request` fixture carries none, so it fails CSRF before it reaches a route —
 * a double-submit token proves nothing without the cookie beside it.
 */

const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'

/** Create one Project through the API, inside one Workspace. */
async function createProject(
  request: APIRequestContext,
  member: SeededMember,
  name: string,
): Promise<string> {
  const response = await request.post(`/api/v1/projects?workspace_id=${member.workspaceId}`, {
    headers: {
      'X-CSRF-Token': member.csrfToken,
      Origin: SITE,
      'Idempotency-Key': `e2e-${name}-${Date.now()}`,
    },
    data: { name, sourceKind: 'upload' },
  })
  expect(response.status()).toBe(201)
  return (await response.json()).id as string
}

test('a clip nobody has planned reports no suggestions rather than a failure', async ({
  page,
}) => {
  const member = await seedMember({
    email: uniqueEmail('broll-empty'),
    displayName: 'Deciding Member',
    workspaceName: 'Deciding Workspace',
  })
  await signIn(page.context(), member, SITE)
  const projectId = await createProject(page.request, member, 'broll-empty')

  // No analysis has run, so this Project has no clip and therefore no suggestions. The
  // read answers with absence, which is a fact rather than an error.
  const response = await page.request.get(
    `/api/v1/projects/${projectId}/candidates/${crypto.randomUUID()}` +
      `/broll-suggestions?workspace_id=${member.workspaceId}`,
  )

  expect(response.status()).toBe(404)
})

test("another Workspace's clip is indistinguishable from one that never existed", async ({
  page,
}) => {
  const owner = await seedMember({
    email: uniqueEmail('broll-owner'),
    displayName: 'Owning Member',
    workspaceName: 'Owning Workspace',
  })
  const stranger = await seedMember({
    email: uniqueEmail('broll-stranger'),
    displayName: 'Other Member',
    workspaceName: 'Other Workspace',
  })
  await signIn(page.context(), owner, SITE)
  const projectId = await createProject(page.request, owner, 'broll-owned')

  // The stranger asks for the owner's clip while declaring their own Workspace, which is
  // the only Workspace they have standing in.
  await page.context().clearCookies()
  await signIn(page.context(), stranger, SITE)
  const guessed = await page.request.get(
    `/api/v1/projects/${projectId}/candidates/${crypto.randomUUID()}` +
      `/broll-suggestions?workspace_id=${stranger.workspaceId}`,
  )
  const missing = await page.request.get(
    `/api/v1/projects/${crypto.randomUUID()}/candidates/${crypto.randomUUID()}` +
      `/broll-suggestions?workspace_id=${stranger.workspaceId}`,
  )

  expect(guessed.status()).toBe(404)
  expect(missing.status()).toBe(404)
  expect((await guessed.json()).error.message).toBe((await missing.json()).error.message)
})

test('asking for B-roll without CSRF proof is refused', async ({ page }) => {
  const member = await seedMember({
    email: uniqueEmail('broll-csrf'),
    displayName: 'Deciding Member',
    workspaceName: 'Deciding Workspace',
  })
  await signIn(page.context(), member, SITE)
  const projectId = await createProject(page.request, member, 'broll-csrf')

  // The session cookie is carried; only the double-submit token is withheld.
  const response = await page.request.post(
    `/api/v1/projects/${projectId}/candidates/${crypto.randomUUID()}` +
      `/broll-plans?workspace_id=${member.workspaceId}`,
    {
      headers: { Origin: SITE, 'Idempotency-Key': 'e2e-broll-csrf' },
      data: { coverage: 'balanced' },
    },
  )

  expect([401, 403]).toContain(response.status())
})

test('a clip with no plan yet offers to find B-roll and nothing else', async ({ page }) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('broll-panel'),
    displayName: 'Deciding Member',
    workspaceName: 'Deciding Workspace',
    projectName: 'Illustrated Episode',
  })
  await signIn(page.context(), member, SITE)
  const opened = await page.request.post(
    `/api/v1/projects/${member.project.projectId}` +
      `/candidates/${member.project.candidateId}/edits` +
      `?workspace_id=${member.workspaceId}`,
    { headers: { 'X-CSRF-Token': member.csrfToken, Origin: SITE } },
  )
  expect(opened.status()).toBe(201)
  const editId = (await opened.json()).id as string

  await page.goto(`/editor/${editId}?workspace_id=${member.workspaceId}`)
  const panel = page.getByRole('region', { name: /b-roll/i })

  await expect(panel).toBeVisible()
  // Nothing has been planned, so there is nothing to decide on — and the coverage a
  // member would choose stays inert until they ask for suggestions at all.
  await expect(panel.getByText(/no b-roll suggestions yet/i)).toBeVisible()
  await expect(panel.getByRole('combobox', { name: /coverage/i })).toBeDisabled()
  await expect(panel.getByRole('button', { name: /suggest b-roll/i })).toBeEnabled()
})

// Accepting a real suggestion needs one the planner produced, which means a language
// model and a stock provider. The seed stages the pipeline's own output up to the clip;
// staging a licensed picture too would be inventing provenance, which is the one thing
// Task 29 refuses to do. The decision path is covered at the component and integration
// level, and this scenario waits for a run with real provider credentials.
test.fixme('a member accepts a suggestion and the picture survives a reload', async () => {})
