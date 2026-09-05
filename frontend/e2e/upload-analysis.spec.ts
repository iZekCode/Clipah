import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import { seedMember, signIn, uniqueEmail, type SeededMember } from './support/seed'

/**
 * Getting media into a Project, in a browser, against a real backend and object store.
 *
 * The parts a component test cannot see live here: a signed part URL that only the real
 * object store will accept, the backend's own refusal of a source it will not import, and
 * a role that is refused server-side rather than merely hidden.
 */

const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'
const FIXTURE = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../backend/tests/fixtures/media/landscape.mp4',
)

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

test('a member uploads a video and the Project starts working on it', async ({ page }) => {
  const member = await seedMember({
    email: uniqueEmail('upload'),
    displayName: 'Uploading Member',
    workspaceName: 'Uploading Workspace',
  })
  await signIn(page.context(), member, SITE)
  const projectId = await createProject(page.request, member, 'Uploaded Episode')

  await page.goto(`/dashboard/projects/${projectId}`)
  await page.getByLabel(/video file/i).setInputFiles(FIXTURE)

  await expect(page.getByRole('main').getByRole('status').last()).toHaveText(/queued|importing|transcribing|finding moments/i, {
    timeout: 60_000,
  })
})

test('the browser refuses an address that is not YouTube without asking the backend', async ({
  page,
}) => {
  const member = await seedMember({
    email: uniqueEmail('host'),
    displayName: 'Careful Member',
    workspaceName: 'Careful Workspace',
  })
  await signIn(page.context(), member, SITE)
  const projectId = await createProject(page.request, member, 'Host Checked')

  const attempted: string[] = []
  await page.route('**/youtube-imports*', async (route) => {
    attempted.push(route.request().url())
    await route.abort()
  })

  await page.goto(`/dashboard/projects/${projectId}`)
  await page.getByLabel(/youtube video url/i).fill('https://vimeo.com/12345')
  await page.getByRole('button', { name: /import video/i }).click()

  await expect(page.getByRole('main').getByRole('alert')).toContainText(/youtube/i)
  expect(attempted).toEqual([])
})

test('the backend refuses a source form it does not support, in its own words', async ({
  page,
}) => {
  const member = await seedMember({
    email: uniqueEmail('unsupported'),
    displayName: 'Importing Member',
    workspaceName: 'Importing Workspace',
  })
  await signIn(page.context(), member, SITE)
  const projectId = await createProject(page.request, member, 'Unsupported Source')

  await page.goto(`/dashboard/projects/${projectId}`)
  await page.getByLabel(/youtube video url/i).fill('https://www.youtube.com/playlist?list=PL1')
  await page.getByRole('button', { name: /import video/i }).click()

  const alert = page.getByRole('main').getByRole('alert')
  await expect(alert).toContainText(/not supported|not publicly accessible/i)
  await expect(alert).not.toContainText(/traceback|yt-dlp|sql/i)
})

test('the public import never offers to take a cookie file', async ({ page }) => {
  const member = await seedMember({
    email: uniqueEmail('cookies'),
    displayName: 'Public Member',
    workspaceName: 'Public Workspace',
  })
  await signIn(page.context(), member, SITE)
  const projectId = await createProject(page.request, member, 'Public Only')

  await open(page, member, `/dashboard/projects/${projectId}`)

  await expect(page.getByLabel(/youtube video url/i)).toBeVisible()
  await expect(page.locator('input[type="file"]')).toHaveCount(1)
  await expect(page.getByText(/cookie/i)).toHaveCount(0)
})
