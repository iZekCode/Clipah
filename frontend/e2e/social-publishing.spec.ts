import { expect, test } from '@playwright/test'

import { seedMember, signIn, uniqueEmail } from './support/seed'

/**
 * Publishing against the real stack, with only the gates this deployment has opened.
 *
 * Connecting a provider needs that provider's own OAuth credentials, so a deployment
 * without them cannot be driven through a publication here. What a browser can prove
 * without them is what matters most: a closed gate offers nothing, an open one offers
 * exactly its own provider, and a batch belonging to somebody else stays invisible.
 */
const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'

/** What this deployment has switched on, read the way the browser itself reads it. */
async function gatesFor(context: import('@playwright/test').BrowserContext) {
  const response = await context.request.get('/api/v1/me')
  expect(response.status()).toBe(200)
  return (await response.json()).capabilities as Record<string, boolean>
}

test('the publishing dashboard reports an empty workspace honestly', async ({ browser }) => {
  const member = await seedMember({
    email: uniqueEmail('publishing-empty'),
    displayName: 'Publishing Owner',
    workspaceName: 'Publishing Workspace',
  })
  const context = await browser.newContext({ baseURL: SITE, reducedMotion: 'reduce' })
  await signIn(context, member, SITE)

  const page = await context.newPage()
  await page.goto('/dashboard/publishing')

  await expect(page.getByRole('heading', { name: 'Publishing', level: 1 })).toBeVisible()
  await expect(page.getByText(/Nothing has been published from this workspace yet/)).toBeVisible()
})

test('connections offer exactly the providers this deployment has switched on', async ({
  browser,
}) => {
  const member = await seedMember({
    email: uniqueEmail('publishing-gates'),
    displayName: 'Gate Owner',
    workspaceName: 'Gate Workspace',
  })
  const context = await browser.newContext({ baseURL: SITE, reducedMotion: 'reduce' })
  await signIn(context, member, SITE)
  const gates = await gatesFor(context)

  const page = await context.newPage()
  await page.goto('/dashboard/settings/connections')

  if (!gates.socialPublishing) {
    await expect(page.getByText(/Social publishing is not enabled/)).toBeVisible()
    await expect(page.getByRole('button', { name: /^Connect / })).toHaveCount(0)
    return
  }
  for (const [provider, label] of [
    ['youtubePublishing', 'YouTube'],
    ['instagramPublishing', 'Instagram'],
    ['tiktokPublishing', 'TikTok'],
  ] as Array<[string, string]>) {
    await expect(page.getByRole('button', { name: `Connect ${label}` })).toHaveCount(
      gates[provider] ? 1 : 0,
    )
  }
})

test('an unknown publication batch is refused exactly like a forbidden one', async ({
  browser,
}) => {
  const owner = await seedMember({
    email: uniqueEmail('publishing-owner'),
    displayName: 'Batch Owner',
    workspaceName: 'Batch Workspace',
  })
  const stranger = await seedMember({
    email: uniqueEmail('publishing-stranger'),
    displayName: 'Batch Stranger',
    workspaceName: 'Stranger Workspace',
  })
  const context = await browser.newContext({ baseURL: SITE, reducedMotion: 'reduce' })
  await signIn(context, stranger, SITE)

  const guessed = await context.request.get(
    `/api/v1/publication-batches/${owner.workspaceId}?workspace_id=${stranger.workspaceId}`,
  )
  const missing = await context.request.get(
    '/api/v1/publication-batches/00000000-0000-4000-8000-000000000000' +
      `?workspace_id=${stranger.workspaceId}`,
  )

  expect(guessed.status()).toBe(404)
  expect(missing.status()).toBe(404)
  expect((await guessed.json()).error.message).toBe((await missing.json()).error.message)
})

test('the batch page reports a refusal instead of an empty timeline', async ({ browser }) => {
  const member = await seedMember({
    email: uniqueEmail('publishing-refusal'),
    displayName: 'Refusal Owner',
    workspaceName: 'Refusal Workspace',
  })
  const context = await browser.newContext({ baseURL: SITE, reducedMotion: 'reduce' })
  await signIn(context, member, SITE)

  const page = await context.newPage()
  await page.goto('/dashboard/publishing/00000000-0000-4000-8000-000000000000')

  await expect(page.getByText(/The requested resource was not found/)).toBeVisible()
})

test('the composer refuses to publish until a destination is chosen', async ({ browser }) => {
  const member = await seedMember({
    email: uniqueEmail('publishing-composer'),
    displayName: 'Composer Owner',
    workspaceName: 'Composer Workspace',
  })
  const context = await browser.newContext({ baseURL: SITE, reducedMotion: 'reduce' })
  await signIn(context, member, SITE)
  const gates = await gatesFor(context)

  const page = await context.newPage()
  await page.goto(
    '/dashboard/publishing?editId=00000000-0000-4000-8000-000000000001&revision=1' +
      '&renderArtifactId=00000000-0000-4000-8000-000000000002',
  )

  if (!gates.socialPublishing) {
    await expect(page.getByText(/Social publishing is not enabled/).first()).toBeVisible()
    return
  }
  await expect(page.getByRole('button', { name: /Review and publish/ })).toBeDisabled()
})
