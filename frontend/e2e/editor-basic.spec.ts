import { expect, test, type Page } from '@playwright/test'

import { alertOf } from './support/locators'
import {
  seedMember,
  seedMemberWithClip,
  signIn,
  uniqueEmail,
  type SeededMember,
} from './support/seed'

/**
 * The editor in a real browser, against a real backend.
 *
 * The parts a component test cannot see live here: an Edit identifier that belongs to
 * another Workspace answering exactly like one that never existed, and an unauthenticated
 * visitor being asked to sign in rather than shown someone else's clip.
 */

const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'
const UNKNOWN_EDIT_ID = '99999999-9999-4999-8999-999999999999'

/** Sign one seeded member in and open a page as them. */
async function open(page: Page, member: SeededMember, path: string): Promise<void> {
  await signIn(page.context(), member, SITE)
  await page.goto(path)
}

test('an Edit that does not exist is not explained away', async ({ page }) => {
  const member = await seedMember({
    email: uniqueEmail('editor-missing'),
    displayName: 'Editing Member',
    workspaceName: 'Editing Workspace',
  })

  await open(page, member, `/editor/${UNKNOWN_EDIT_ID}?workspace_id=${member.workspaceId}`)

  await expect(alertOf(page)).toContainText(/not found/i)
  await expect(page.getByRole('region', { name: /timeline/i })).toHaveCount(0)
})

test('the editor is not reachable without a session', async ({ page }) => {
  await page.goto(`/editor/${UNKNOWN_EDIT_ID}`)

  await expect(page.getByRole('link', { name: /sign in/i })).toBeVisible()
  await expect(page.getByRole('region', { name: /timeline/i })).toHaveCount(0)
})

test('saving an Edit is refused for a Workspace the member does not belong to', async ({
  page,
}) => {
  const member = await seedMember({
    email: uniqueEmail('editor-outsider'),
    displayName: 'Outside Member',
    workspaceName: 'Outside Workspace',
  })
  await signIn(page.context(), member, SITE)

  const refused = await page.request.put(`/api/v1/edits/${UNKNOWN_EDIT_ID}`, {
    headers: { 'X-CSRF-Token': member.csrfToken, Origin: SITE },
    params: { workspace_id: member.workspaceId },
    data: { expectedRevision: 1, composition: {} },
  })

  expect(refused.status()).toBe(404)
  expect((await refused.json()).error.code).toBe('NOT_FOUND')
})

test('a member trims a real clip and the Revision survives a reload', async ({ page }) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('editor-trim'),
    displayName: 'Editing Member',
    workspaceName: 'Editing Workspace',
    projectName: 'Edited Episode',
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
  const inspector = page.getByRole('region', { name: /inspector/i })
  await expect(inspector).toBeVisible()

  // Trim through the control a member actually uses. The wait is on the save itself
  // rather than on the words beside it: the editor already reads "Saved" before anything
  // has been edited, so waiting for that text would prove nothing and reload too early.
  const saved = page.waitForResponse(
    (response) =>
      response.request().method() === 'PUT' &&
      response.url().includes(`/api/v1/edits/${editId}`) &&
      response.status() === 200,
  )
  const endsAt = inspector.getByRole('spinbutton', { name: /clip ends at/i })
  await endsAt.fill('20000')
  await endsAt.blur()
  await saved

  await page.reload()

  // The editor reopens on the Revision the backend kept, not on the seeded one.
  const reopened = page.getByRole('region', { name: /inspector/i })
  await expect(reopened.getByRole('spinbutton', { name: /clip ends at/i })).toHaveValue('20000')
})
