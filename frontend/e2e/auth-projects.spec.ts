import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import { alertOf } from './support/locators'
import { seedMember, signIn, uniqueEmail, type SeededMember } from './support/seed'

/**
 * What a signed-in member can actually do in a browser, against a real backend.
 *
 * Every refusal here is the backend's: the browser only shows what it was told, so these
 * tests fail if the frontend ever starts deciding access for itself.
 */

const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'

/** Sign one seeded member into a fresh page. */
async function open(page: Page, member: SeededMember, path: string): Promise<void> {
  await signIn(page.context(), member, SITE)
  await page.goto(path)
}

/** Create one Workspace through the API, as a member who is already signed in. */
async function createWorkspace(
  request: APIRequestContext,
  member: SeededMember,
  name: string,
): Promise<string> {
  const response = await request.post('/api/v1/workspaces', {
    headers: { 'X-CSRF-Token': member.csrfToken, Origin: SITE },
    data: { name },
  })
  expect(response.status()).toBe(201)
  return (await response.json()).id as string
}

/** Create one Project through the API, inside one Workspace. */
async function createProject(
  request: APIRequestContext,
  member: SeededMember,
  workspaceId: string,
  name: string,
): Promise<string> {
  const response = await request.post(`/api/v1/projects?workspace_id=${workspaceId}`, {
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

test('a newly bootstrapped member lands in their own personal Workspace', async ({ page }) => {
  const member = await seedMember({
    email: uniqueEmail('bootstrap'),
    displayName: 'Bootstrap Member',
    workspaceName: 'Bootstrap Workspace',
  })

  await open(page, member, '/dashboard')

  await expect(page.getByRole('combobox', { name: /workspace/i })).toHaveValue(member.workspaceId)
})

test('switching Workspace shows only the Projects of the Workspace being viewed', async ({
  page,
}) => {
  const member = await seedMember({
    email: uniqueEmail('switch'),
    displayName: 'Switching Member',
    workspaceName: 'First Workspace',
  })
  await signIn(page.context(), member, SITE)
  const second = await createWorkspace(page.request, member, 'Second Workspace')
  await createProject(page.request, member, member.workspaceId, 'Only In First')

  await page.goto('/dashboard/projects')
  await expect(page.getByRole('link', { name: 'Only In First' })).toBeVisible()

  await page.getByRole('combobox', { name: /workspace/i }).selectOption(second)

  await expect(page.getByRole('link', { name: 'Only In First' })).toHaveCount(0)
  await expect(page.getByText(/no projects yet/i)).toBeVisible()
})

test('a member creates, renames, deletes, and recovers a Project', async ({ page }) => {
  const member = await seedMember({
    email: uniqueEmail('lifecycle'),
    displayName: 'Lifecycle Member',
    workspaceName: 'Lifecycle Workspace',
  })

  await open(page, member, '/dashboard/projects')

  await page.getByRole('button', { name: /create project/i }).click()
  await page.getByRole('textbox', { name: /new project name/i }).fill('Episode 12')
  await page.getByRole('button', { name: /start project/i }).click()
  await expect(page.getByRole('link', { name: 'Episode 12' })).toBeVisible()

  await page.getByRole('button', { name: /rename episode 12/i }).click()
  await page.getByRole('textbox', { name: /project name/i }).fill('Episode 12 final')
  await page.getByRole('button', { name: /save/i }).click()
  await expect(page.getByRole('link', { name: 'Episode 12 final' })).toBeVisible()

  await page.getByRole('button', { name: /delete episode 12 final/i }).click()
  await expect(page.getByRole('link', { name: 'Episode 12 final' })).toHaveCount(0)

  await page.getByRole('button', { name: /restore episode 12 final/i }).click()
  await page.reload()
  await expect(page.getByRole('link', { name: 'Episode 12 final' })).toBeVisible()
})

test("another Workspace's Project URL answers like a Project that never existed", async ({
  page,
  browser,
}) => {
  const owner = await seedMember({
    email: uniqueEmail('owner'),
    displayName: 'Owning Member',
    workspaceName: 'Owning Workspace',
  })
  const outsider = await seedMember({
    email: uniqueEmail('outsider'),
    displayName: 'Outside Member',
    workspaceName: 'Outside Workspace',
  })

  const ownerContext = await browser.newContext({ baseURL: SITE })
  await signIn(ownerContext, owner, SITE)
  const projectId = await createProject(
    ownerContext.request,
    owner,
    owner.workspaceId,
    'Private Episode',
  )
  await ownerContext.close()

  await open(page, outsider, `/dashboard/projects/${projectId}`)

  const alert = alertOf(page)
  await expect(alert).toContainText(/not found/i)
  await expect(alert).not.toContainText(/permission|member|workspace/i)
})

test('an owner removes a member and that member loses the Workspace', async ({ browser }) => {
  const owner = await seedMember({
    email: uniqueEmail('removing-owner'),
    displayName: 'Removing Owner',
    workspaceName: 'Removal Team',
    teamWorkspace: true,
  })
  const member = await seedMember({
    email: uniqueEmail('removed-member'),
    displayName: 'Removed Member',
    workspaceName: 'Removed Personal',
  })
  const ownerContext = await browser.newContext({ baseURL: SITE })
  const memberContext = await browser.newContext({ baseURL: SITE })
  await signIn(ownerContext, owner, SITE)
  await signIn(memberContext, member, SITE)
  const invitation = await ownerContext.request.post(
    `/api/v1/workspaces/${owner.workspaceId}/invites`,
    {
      headers: { 'X-CSRF-Token': owner.csrfToken, Origin: SITE },
      data: { email: member.userId + '@delivery.test', role: 'viewer' },
    },
  )
  expect(invitation.status()).toBe(201)
  const token = (await invitation.json()).token as string
  const accepted = await memberContext.request.post(`/api/v1/workspace-invites/${token}/accept`, {
    headers: { 'X-CSRF-Token': member.csrfToken, Origin: SITE },
  })
  expect(accepted.status()).toBe(200)

  const removed = await ownerContext.request.delete(
    `/api/v1/workspaces/${owner.workspaceId}/members/${member.userId}`,
    { headers: { 'X-CSRF-Token': owner.csrfToken, Origin: SITE } },
  )
  expect(removed.status()).toBe(204)
  const refused = await memberContext.request.get(`/api/v1/workspaces/${owner.workspaceId}`)
  expect(refused.status()).toBe(404)
  expect((await refused.json()).error.code).toBe('NOT_FOUND')
  await ownerContext.close()
  await memberContext.close()
})
