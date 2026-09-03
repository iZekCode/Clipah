import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import { seedMember, signIn, uniqueEmail, type SeededMember } from './support/seed'

/**
 * Reviewing ranked clips in a browser, against a real backend.
 *
 * The parts a component test cannot see live here: a Project that has produced no
 * analysis yet, and another Workspace's Project answering exactly like one that never
 * existed.
 */

const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'

/** Sign one seeded member in and open a page as them. */
async function open(page: Page, member: SeededMember, path: string): Promise<void> {
  await signIn(page.context(), member, SITE)
  await page.goto(path)
}

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

test('a Project with no analysis says there is nothing to review yet', async ({ page }) => {
  const member = await seedMember({
    email: uniqueEmail('review-empty'),
    displayName: 'Reviewing Member',
    workspaceName: 'Reviewing Workspace',
  })
  await signIn(page.context(), member, SITE)
  const projectId = await createProject(page.request, member, 'Not Analysed Yet')

  await page.goto(`/dashboard/projects/${projectId}`)

  await expect(page.getByText(/no clips to review yet/i)).toBeVisible()
  await expect(page.getByRole('list', { name: /ranked clips/i })).toHaveCount(0)
})

test("another Workspace's Project reveals no clips and no explanation", async ({
  page,
  browser,
}) => {
  const owner = await seedMember({
    email: uniqueEmail('review-owner'),
    displayName: 'Owning Member',
    workspaceName: 'Owning Workspace',
  })
  const outsider = await seedMember({
    email: uniqueEmail('review-outsider'),
    displayName: 'Outside Member',
    workspaceName: 'Outside Workspace',
  })

  const ownerContext = await browser.newContext({ baseURL: SITE })
  await signIn(ownerContext, owner, SITE)
  const projectId = await createProject(ownerContext.request, owner, 'Private Analysis')
  await ownerContext.close()

  await open(page, outsider, `/dashboard/projects/${projectId}`)

  await expect(page.getByRole('list', { name: /ranked clips/i })).toHaveCount(0)
  await expect(page.getByText(/permission|member|workspace does not/i)).toHaveCount(0)
})

test('the proxy a preview would play is refused without a session', async ({ page, request }) => {
  const member = await seedMember({
    email: uniqueEmail('review-proxy'),
    displayName: 'Proxy Member',
    workspaceName: 'Proxy Workspace',
  })
  await signIn(page.context(), member, SITE)
  const projectId = await createProject(page.request, member, 'Proxy Guarded')

  const anonymous = await request.get(
    `/api/v1/projects/${projectId}/proxy?workspace_id=${member.workspaceId}`,
  )

  expect(anonymous.status()).toBe(401)
  expect((await anonymous.json()).error.code).toBe('UNAUTHENTICATED')
})

// Creating an Edit from a reviewed candidate needs the composition domain and the
// `POST /projects/{projectId}/candidates/{candidateId}/edits` route that Task 22 builds.
// The review surface is complete without it, and the scenario belongs with that task.
test.fixme('a reviewer turns a candidate into an edit exactly once', async () => {})
