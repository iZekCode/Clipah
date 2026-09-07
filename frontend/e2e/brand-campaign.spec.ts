import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import {
  seedMember,
  seedMemberWithClip,
  signIn,
  uniqueEmail,
  type SeededMember,
} from './support/seed'

/**
 * Publishing a brand, reusing a look, and writing copy from an approved cut, end to end.
 *
 * What a component test cannot see lives here: a version published against a real
 * Postgres, an archived look that still resolves for the clips that carry it, and copy
 * derived from one immutable Revision by the deterministic generator itself.
 */

const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'

/** The rules one Workspace publishes for its clips. */
function definition(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    logoAssetId: null,
    fonts: [{ family: 'Inter', assetId: null }],
    colors: [
      { name: 'Paper', hex: '#FFFFFF' },
      { name: 'Ink', hex: '#000000' },
      { name: 'Highlight', hex: '#FFD166' },
    ],
    captionRules: {
      minFontSize: 12,
      maxFontSize: 200,
      allowedAlignments: ['left', 'center', 'right'],
      reservedPlacements: [],
    },
    visualExclusions: ['alkohol'],
    claimRules: { requiredAttribution: null, forbiddenClaimPhrases: ['dijamin untung'] },
    ...overrides,
  }
}

/** The headers every state-changing request needs to prove it came from our own site. */
function writeHeaders(member: SeededMember): Record<string, string> {
  return { 'X-CSRF-Token': member.csrfToken, Origin: SITE }
}

/** Publish one Brand Kit through the API. */
async function publishKit(
  request: APIRequestContext,
  member: SeededMember,
  name: string,
): Promise<string> {
  const response = await request.post(`/api/v1/brand-kits?workspace_id=${member.workspaceId}`, {
    headers: writeHeaders(member),
    data: { name, definition: definition() },
  })
  expect(response.status()).toBe(201)
  return (await response.json()).id as string
}

/** Publish one Workspace-owned look through the API. */
async function publishLook(
  request: APIRequestContext,
  member: SeededMember,
  name: string,
): Promise<string> {
  const response = await request.post(`/api/v1/templates?workspace_id=${member.workspaceId}`, {
    headers: writeHeaders(member),
    data: {
      name,
      brandKitId: null,
      definition: {
        kind: 'clip_look',
        captionMode: 'karaoke',
        captionStyle: {
          fontFamily: 'Inter',
          fontSize: 56,
          color: '#FFFFFF',
          highlightColor: '#FFD166',
          align: 'center',
          weight: 600,
          italic: false,
          decoration: 'none',
          letterSpacing: 0,
          lineHeight: 1.2,
          backgroundEnabled: false,
          backgroundColor: '#000000',
        },
        textStyle: {
          fontFamily: 'Inter',
          fontSize: 42,
          color: '#FFFFFF',
          align: 'center',
          weight: 500,
          italic: false,
          decoration: 'none',
          letterSpacing: 0,
          lineHeight: 1.2,
          backgroundEnabled: false,
          backgroundColor: '#000000',
        },
      },
    },
  })
  expect(response.status()).toBe(201)
  return (await response.json()).id as string
}

/** Open one page as a seeded member. */
async function open(page: Page, member: SeededMember, path: string): Promise<void> {
  await signIn(page.context(), member, SITE)
  await page.goto(path)
}

test('a published brand kit is listed at the version its rules live at', async ({ page }) => {
  const member = await seedMember({
    email: uniqueEmail('brand-kit'),
    displayName: 'Branding Member',
    workspaceName: 'Branding Workspace',
  })
  await signIn(page.context(), member, SITE)
  await publishKit(page.request, member, 'Kanal Utama')

  await open(page, member, '/dashboard/brand-kits')

  await expect(page.getByText('Kanal Utama')).toBeVisible()
  await expect(page.getByText(/version 1/i)).toBeVisible()
})

test('editing a brand kit publishes a new version and leaves the old one standing', async ({
  page,
}) => {
  const member = await seedMember({
    email: uniqueEmail('brand-version'),
    displayName: 'Branding Member',
    workspaceName: 'Branding Workspace',
  })
  await signIn(page.context(), member, SITE)
  const kitId = await publishKit(page.request, member, 'Kanal Utama')

  const updated = await page.request.patch(
    `/api/v1/brand-kits/${kitId}?workspace_id=${member.workspaceId}`,
    {
      headers: writeHeaders(member),
      data: { definition: definition({ visualExclusions: ['alkohol', 'judi'] }) },
    },
  )
  expect(updated.status()).toBe(200)

  const first = await page.request.get(
    `/api/v1/brand-kits/${kitId}/versions/1?workspace_id=${member.workspaceId}`,
  )
  expect(first.status()).toBe(200)
  expect((await first.json()).definition.visualExclusions).toEqual(['alkohol'])
  await open(page, member, '/dashboard/brand-kits')
  await expect(page.getByText(/version 2/i)).toBeVisible()
})

test('an archived look leaves the library but still resolves for the clips that use it', async ({
  page,
}) => {
  const member = await seedMember({
    email: uniqueEmail('look-archive'),
    displayName: 'Branding Member',
    workspaceName: 'Branding Workspace',
  })
  await signIn(page.context(), member, SITE)
  const templateId = await publishLook(page.request, member, 'Sorotan')

  await open(page, member, '/dashboard/templates')
  await page.getByRole('button', { name: /archive/i }).click()

  await expect(page.getByText(/still renders for the clips that use it/i)).toBeVisible()
  const version = await page.request.get(
    `/api/v1/templates/${templateId}/versions/1?workspace_id=${member.workspaceId}`,
  )
  expect(version.status()).toBe(200)
  expect((await version.json()).definition.captionMode).toBe('karaoke')
})

test('opening a clip with a look writes that exact version into the composition', async ({
  page,
}) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('look-apply'),
    displayName: 'Branding Member',
    workspaceName: 'Branding Workspace',
    projectName: 'Branded Project',
  })
  await signIn(page.context(), member, SITE)
  const templateId = await publishLook(page.request, member, 'Sorotan')

  const created = await page.request.post(
    `/api/v1/projects/${member.project.projectId}/candidates/${member.project.candidateId}` +
      `/edits?workspace_id=${member.workspaceId}`,
    { headers: writeHeaders(member), data: { templateId, brandKitId: null } },
  )

  expect(created.status()).toBe(201)
  const body = await created.json()
  expect(body.composition.template).toEqual({ id: templateId, version: 1 })
  expect(body.brandViolations).toEqual([])
})

test('campaign copy is written from one approved cut and publishes nothing', async ({ page }) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('campaign'),
    displayName: 'Branding Member',
    workspaceName: 'Branding Workspace',
    projectName: 'Campaign Project',
  })
  await signIn(page.context(), member, SITE)
  const created = await page.request.post(
    `/api/v1/projects/${member.project.projectId}/candidates/${member.project.candidateId}` +
      `/edits?workspace_id=${member.workspaceId}`,
    { headers: writeHeaders(member) },
  )
  expect(created.status()).toBe(201)
  const edit = await created.json()

  await open(
    page,
    member,
    `/dashboard/clips/${member.project.candidateId}?projectId=${member.project.projectId}` +
      `&editId=${edit.id}&revision=${edit.currentRevision}`,
  )
  await page.getByRole('button', { name: /write campaign copy/i }).click()

  await expect(page.getByText(new RegExp(`revision ${edit.currentRevision}`, 'i'))).toBeVisible()
  await expect(page.getByText(/without a language model/i)).toBeVisible()
  await expect(page.getByRole('link', { name: /open the publishing composer/i })).toBeVisible()
})
