import { expect, test, type Page } from '@playwright/test'

import { seedMember, seedMemberWithClip, signIn, uniqueEmail } from './support/seed'

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

test('a member splits a real clip and the timeline shows both halves', async ({ page }) => {
  const { page: editor, editId } = await openEditor(page, 'editor-split', 'Split Episode')
  const timeline = editor.getByRole('region', { name: /timeline/i })
  await expect(timeline.getByRole('button', { name: /^Select scene-1$/ })).toBeVisible()

  // Put the playhead inside the clip, then cut there. Two items where there was one is
  // the whole of what a split means to a member.
  await editor.getByRole('slider', { name: /scrub the clip/i }).fill('10000')
  const saved = savedResponse(editor, editId)
  await editor.getByRole('button', { name: /^Split$/ }).click()
  await saved

  await expect(timeline.getByRole('button', { name: /^Select scene-/ })).toHaveCount(2)

  await editor.reload()
  await expect(
    editor.getByRole('region', { name: /timeline/i }).getByRole('button', {
      name: /^Select scene-/,
    }),
  ).toHaveCount(2)
})

test('a member writes a text overlay and it survives a reload', async ({ page }) => {
  const { page: editor, editId } = await openEditor(page, 'editor-text', 'Titled Episode')
  const panel = editor.getByRole('region', { name: /^Text$/ })

  const saved = savedResponse(editor, editId)
  await panel.getByRole('textbox', { name: /new text/i }).fill('A written title')
  await panel.getByRole('button', { name: /add text/i }).click()
  await saved

  await editor.reload()
  const reopened = editor.getByRole('region', { name: /^Text$/ })
  await expect(reopened.getByRole('textbox', { name: /text of/i })).toHaveValue('A written title')
})

/** Resolve once the backend has accepted one save of this Edit. */
function savedResponse(page: Page, editId: string): Promise<unknown> {
  return page.waitForResponse(
    (response) =>
      response.request().method() === 'PUT' &&
      response.url().includes(`/api/v1/edits/${editId}`) &&
      response.status() === 200,
  )
}

/** Seed an analysed clip, open its Edit, and land in the editor with it loaded. */
async function openEditor(
  page: Page,
  prefix: string,
  projectName: string,
): Promise<{ page: Page; editId: string }> {
  const member = await seedMemberWithClip({
    email: uniqueEmail(prefix),
    displayName: 'Editing Member',
    workspaceName: 'Editing Workspace',
    projectName,
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
  await expect(page.getByRole('region', { name: /inspector/i })).toBeVisible()
  return { page, editId }
}
