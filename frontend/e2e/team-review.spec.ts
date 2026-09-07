import { expect, test } from '@playwright/test'

import { seedMember, seedMemberWithClip, signIn, uniqueEmail } from './support/seed'

const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'

test('a reviewer accepts an invite, comments, and approves one immutable revision', async ({
  browser,
}) => {
  const owner = await seedMemberWithClip({
    email: uniqueEmail('review-owner'),
    displayName: 'Review Owner',
    workspaceName: 'Review Team',
    projectName: 'Review Episode',
    teamWorkspace: true,
  })
  const reviewer = await seedMember({
    email: uniqueEmail('reviewer'),
    displayName: 'Revision Reviewer',
    workspaceName: 'Reviewer Personal',
  })
  const ownerContext = await browser.newContext({ baseURL: SITE, reducedMotion: 'reduce' })
  const reviewerContext = await browser.newContext({ baseURL: SITE, reducedMotion: 'reduce' })
  await signIn(ownerContext, owner, SITE)
  await signIn(reviewerContext, reviewer, SITE)
  const ownerHeaders = { 'X-CSRF-Token': owner.csrfToken, Origin: SITE }
  const reviewerHeaders = { 'X-CSRF-Token': reviewer.csrfToken, Origin: SITE }

  const invitation = await ownerContext.request.post(
    `/api/v1/workspaces/${owner.workspaceId}/invites`,
    {
      headers: ownerHeaders,
      data: { email: 'delivery-only@example.test', role: 'reviewer' },
    },
  )
  expect(invitation.status()).toBe(201)
  const token = (await invitation.json()).token as string
  const invitePage = await reviewerContext.newPage()
  await invitePage.goto(`/invite/${token}`)
  await invitePage.getByRole('button', { name: /accept invite/i }).click()
  await expect(invitePage.getByText(/joined as reviewer/i)).toBeVisible()

  const opened = await ownerContext.request.post(
    `/api/v1/projects/${owner.project.projectId}/candidates/${owner.project.candidateId}/edits` +
      `?workspace_id=${owner.workspaceId}`,
    { headers: ownerHeaders },
  )
  expect(opened.status()).toBe(201)
  const openedEdit = (await opened.json()) as {
    id: string
    currentRevision: number
    composition: { audio: { gainDb: number } }
  }
  const editId = openedEdit.id
  const history = await reviewerContext.request.get(
    `/api/v1/edits/${editId}/revisions?workspace_id=${owner.workspaceId}`,
  )
  expect(history.status()).toBe(200)
  const revisionId = (await history.json()).revisions[0].id as string

  const comment = await reviewerContext.request.post(
    `/api/v1/edits/${editId}/review-comments?workspace_id=${owner.workspaceId}`,
    {
      headers: reviewerHeaders,
      data: {
        revisionId,
        text: 'Tighten the opening pause.',
        anchor: { kind: 'timestamp', timestampMs: 1_000 },
      },
    },
  )
  expect(comment.status()).toBe(201)
  const approval = await reviewerContext.request.post(
    `/api/v1/edits/${editId}/reviews?workspace_id=${owner.workspaceId}`,
    { headers: reviewerHeaders, data: { revisionId, decision: 'approve' } },
  )
  expect(approval.status()).toBe(201)

  const reviewerPage = await reviewerContext.newPage()
  await reviewerPage.addInitScript((workspaceId) => {
    window.sessionStorage.setItem('clipah.workspace', workspaceId)
  }, owner.workspaceId)
  await reviewerPage.goto(`/editor/${editId}`)
  await expect(reviewerPage.getByText('Tighten the opening pause.')).toBeVisible()
  await expect(reviewerPage.getByText('Approved', { exact: true })).toBeVisible()
  await expect(reviewerPage.getByRole('heading', { name: /accessibility quality/i })).toBeVisible()

  const changedComposition = structuredClone(openedEdit.composition)
  changedComposition.audio.gainDb = -3
  const revised = await ownerContext.request.put(
    `/api/v1/edits/${editId}?workspace_id=${owner.workspaceId}`,
    {
      headers: ownerHeaders,
      data: {
        expectedRevision: openedEdit.currentRevision,
        composition: changedComposition,
      },
    },
  )
  expect(revised.status()).toBe(200)

  await reviewerPage.reload()
  await expect(reviewerPage.getByText('Awaiting approval', { exact: true })).toBeVisible()
  const requestChanges = reviewerPage.getByRole('button', { name: /request changes/i })
  await requestChanges.focus()
  await expect(requestChanges).toBeFocused()
  const requested = reviewerPage.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      response.url().includes(`/api/v1/edits/${editId}/reviews`) &&
      response.status() === 201,
  )
  await reviewerPage.keyboard.press('Enter')
  await requested
  await expect(reviewerPage.getByText('Changes requested.')).toBeAttached()

  await ownerContext.close()
  await reviewerContext.close()
})
