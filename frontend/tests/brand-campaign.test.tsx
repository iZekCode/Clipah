/**
 * The brand a Workspace publishes, the looks it reuses, and the copy it derives.
 *
 * Three properties are proven here. A published version is never edited in place, so the
 * surface that changes a brand always says a new version was published. Nothing here
 * publishes to a platform: campaign copy is text a member reads and decides about. And
 * every piece of that copy came from a transcript somebody else spoke, so all of it is
 * rendered as React children.
 */
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { BrandKitEditor } from '@/features/brand-kits/BrandKitEditor'
import { ClipCard } from '@/features/clips/ClipCard'
import { CampaignPanel } from '@/features/campaigns/CampaignPanel'
import { TemplateLibrary } from '@/features/templates/TemplateLibrary'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'
import type {
  BrandKitResponse,
  CampaignOutputResponse,
  TemplateResponse,
} from '@/lib/api/generated/model'

import { renderWithApi, stubApi, type StubbedApi } from './support/api'
import { candidate, currentUser, edit, workspace } from './support/fixtures'

const WORKSPACE_ID = workspace().id
const EDIT_ID = '77777777-8888-4999-8aaa-bbbbbbbbbbbb'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const BRAND_KITS = 'GET /api/v1/brand-kits'
const CREATE_BRAND_KIT = 'POST /api/v1/brand-kits'
const TEMPLATES = 'GET /api/v1/templates'
const CAMPAIGN_OUTPUTS = `GET /api/v1/edits/${EDIT_ID}/campaign-outputs`
const CREATE_CAMPAIGN_OUTPUTS = `POST /api/v1/edits/${EDIT_ID}/campaign-outputs`

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard/brand-kits',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}))

/** One Brand Kit as the API reports it. */
function brandKit(overrides: Partial<BrandKitResponse> = {}): BrandKitResponse {
  return {
    id: '44444444-5555-4666-8777-888888888888',
    name: 'Kanal Utama',
    version: 1,
    definition: {
      logoAssetId: null,
      fonts: [{ family: 'Inter', assetId: null }],
      colors: [
        { name: 'Paper', hex: '#FFFFFF' },
        { name: 'Ink', hex: '#000000' },
      ],
      captionRules: {
        minFontSize: 32,
        maxFontSize: 72,
        allowedAlignments: ['center'],
        reservedPlacements: ['lowerThird'],
      },
      visualExclusions: ['alkohol'],
      claimRules: { requiredAttribution: null, forbiddenClaimPhrases: ['dijamin untung'] },
    },
    createdAt: '2026-09-01T00:00:00+00:00',
    updatedAt: '2026-09-01T00:00:00+00:00',
    archivedAt: null,
    ...overrides,
  }
}

/** One Workspace-owned look as the API reports it. */
function template(overrides: Partial<TemplateResponse> = {}): TemplateResponse {
  return {
    id: '99999999-aaaa-4bbb-8ccc-dddddddddddd',
    brandKitId: null,
    name: 'Sorotan',
    kind: 'clip_look',
    version: 2,
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
    createdAt: '2026-09-01T00:00:00+00:00',
    updatedAt: '2026-09-02T00:00:00+00:00',
    archivedAt: null,
    ...overrides,
  }
}

/** One piece of campaign copy as the API reports it. */
function output(overrides: Partial<CampaignOutputResponse> = {}): CampaignOutputResponse {
  return {
    id: 'eeeeeeee-ffff-4000-8111-222222222222',
    revision: 3,
    platform: 'tiktok',
    language: 'id',
    title: 'Formulir itu batasnya',
    postCopy: 'Kutipan: “Formulir pendaftaran itu yang bikin berhenti”\n\nIntinya: aktivasi naik',
    cta: 'Ikuti untuk klip lainnya.',
    hashtags: ['#produk', '#aktivasi'],
    thumbnailBrief: {
      text: 'Formulir itu',
      visualDirection: 'Wajah pembicara di sisi kanan, teks pendek di kiri atas.',
      avoid: ['alkohol'],
    },
    warnings: [],
    modelMetadata: {
      generator: 'clipah-deterministic-campaign',
      version: 1,
      deterministic: true,
      provider: null,
      model: null,
    },
    createdAt: '2026-09-03T00:00:00+00:00',
    ...overrides,
  }
}

/** Stub the reads these surfaces make. */
function stubBrand(overrides: Record<string, unknown> = {}): StubbedApi {
  return stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [BRAND_KITS]: { body: { brandKits: [brandKit()] } },
    [CREATE_BRAND_KIT]: { status: 201, body: brandKit({ version: 1 }) },
    [`PATCH /api/v1/brand-kits/${brandKit().id}`]: { body: brandKit({ version: 2 }) },
    [`DELETE /api/v1/brand-kits/${brandKit().id}`]: { status: 204 },
    [TEMPLATES]: { body: { templates: [template()] } },
    [`PATCH /api/v1/templates/${template().id}`]: { body: template({ version: 3 }) },
    [`DELETE /api/v1/templates/${template().id}`]: { status: 204 },
    [CAMPAIGN_OUTPUTS]: { body: { campaignOutputs: [] } },
    [CREATE_CAMPAIGN_OUTPUTS]: { status: 201, body: { campaignOutputs: [output()] } },
    ...overrides,
  } as Parameters<typeof stubApi>[0])
}

describe('the brand a Workspace publishes', () => {
  test('each kit is listed with the version its rules are published at', async () => {
    stubBrand()
    renderWithApi(
      <WorkspaceProvider>
        <BrandKitEditor />
      </WorkspaceProvider>,
    )

    const item = await screen.findByRole('listitem')
    expect(within(item).getByText('Kanal Utama')).toBeVisible()
    expect(within(item).getByText(/version 1/i)).toBeVisible()
  })

  test('publishing a kit sends the rules the member actually filled in', async () => {
    const api = stubBrand()
    renderWithApi(
      <WorkspaceProvider>
        <BrandKitEditor />
      </WorkspaceProvider>,
    )
    await screen.findByRole('listitem')

    await userEvent.type(await screen.findByLabelText(/brand kit name/i), 'Kanal Kedua')
    await userEvent.clear(screen.getByLabelText(/colours/i))
    await userEvent.type(screen.getByLabelText(/colours/i), '#123456')
    await userEvent.clear(screen.getByLabelText(/never show/i))
    await userEvent.type(screen.getByLabelText(/never show/i), 'alkohol, judi')
    await userEvent.click(screen.getByRole('button', { name: /publish brand kit/i }))

    const call = api.calls.find((request) => request.method === 'POST')
    expect(call).toBeDefined()
    const body = call?.body as { name: string; definition: Record<string, unknown> }
    expect(body.name).toBe('Kanal Kedua')
    expect(body.definition.colors).toEqual([{ name: '#123456', hex: '#123456' }])
    expect(body.definition.visualExclusions).toEqual(['alkohol', 'judi'])
    expect(call?.params.get('workspace_id')).toBe(WORKSPACE_ID)
  })

  test('a colour the browser can already tell is invalid never reaches the backend', async () => {
    const api = stubBrand()
    renderWithApi(
      <WorkspaceProvider>
        <BrandKitEditor />
      </WorkspaceProvider>,
    )
    await screen.findByRole('listitem')

    await userEvent.type(screen.getByLabelText(/brand kit name/i), 'Kanal Kedua')
    await userEvent.clear(screen.getByLabelText(/colours/i))
    await userEvent.type(screen.getByLabelText(/colours/i), 'putih')
    await userEvent.click(screen.getByRole('button', { name: /publish brand kit/i }))

    expect(screen.getByRole('alert')).toHaveTextContent(/#RRGGBB/i)
    expect(api.calls.some((request) => request.method === 'POST')).toBe(false)
  })

  test('editing a kit says plainly that a new version was published', async () => {
    stubBrand()
    renderWithApi(
      <WorkspaceProvider>
        <BrandKitEditor />
      </WorkspaceProvider>,
    )
    const item = await screen.findByRole('listitem')

    await userEvent.click(within(item).getByRole('button', { name: /edit/i }))
    await userEvent.click(screen.getByRole('button', { name: /publish new version/i }))

    expect(await screen.findByText(/version 2/i)).toBeVisible()
  })

  test('a member who cannot write sees the brand but no way to change it', async () => {
    stubBrand({ [WORKSPACES]: { body: { workspaces: [workspace({ role: 'viewer' })] } } })
    renderWithApi(
      <WorkspaceProvider>
        <BrandKitEditor />
      </WorkspaceProvider>,
    )

    expect(await screen.findByText('Kanal Utama')).toBeVisible()
    expect(screen.queryByRole('button', { name: /publish brand kit/i })).toBeNull()
  })

  test('a kit named after something that looks like markup is shown as text', async () => {
    stubBrand({ [BRAND_KITS]: { body: { brandKits: [brandKit({ name: '<img src=x>' })] } } })
    const { container } = renderWithApi(
      <WorkspaceProvider>
        <BrandKitEditor />
      </WorkspaceProvider>,
    )

    expect(await screen.findByText('<img src=x>')).toBeVisible()
    expect(container.querySelector('img')).toBeNull()
  })
})

describe('the looks a Workspace reuses', () => {
  test('each look is listed at the version it is currently published at', async () => {
    stubBrand()
    renderWithApi(
      <WorkspaceProvider>
        <TemplateLibrary />
      </WorkspaceProvider>,
    )

    const item = await screen.findByRole('listitem')
    expect(within(item).getByText('Sorotan')).toBeVisible()
    expect(within(item).getByText(/version 2/i)).toBeVisible()
    expect(within(item).getByText(/karaoke/i)).toBeVisible()
  })

  test('archiving a look keeps it available to the clips that already use it', async () => {
    const api = stubBrand()
    renderWithApi(
      <WorkspaceProvider>
        <TemplateLibrary />
      </WorkspaceProvider>,
    )
    await screen.findByRole('listitem')

    await userEvent.click(screen.getByRole('button', { name: /archive/i }))

    expect(api.calls.some((request) => request.method === 'DELETE')).toBe(true)
    expect(await screen.findByText(/still renders for the clips that use it/i)).toBeVisible()
  })

  test('archived looks are out of the way until a member asks for them', async () => {
    const api = stubBrand()
    renderWithApi(
      <WorkspaceProvider>
        <TemplateLibrary />
      </WorkspaceProvider>,
    )
    await screen.findByRole('listitem')

    await userEvent.click(screen.getByRole('checkbox', { name: /show archived/i }))

    const read = api.calls.filter((request) => request.path === '/api/v1/templates')
    expect(read.at(-1)?.params.get('include_archived')).toBe('true')
  })
})

describe('copy derived from one approved cut', () => {
  test('a member chooses the destinations and languages, and the cut is named', async () => {
    const api = stubBrand()
    renderWithApi(
      <WorkspaceProvider>
        <CampaignPanel editId={EDIT_ID} revision={3} />
      </WorkspaceProvider>,
    )
    await screen.findByRole('button', { name: /write campaign copy/i })

    await userEvent.click(screen.getByRole('checkbox', { name: /english/i }))
    await userEvent.click(screen.getByRole('button', { name: /write campaign copy/i }))

    const call = api.calls.find((request) => request.method === 'POST')
    expect(call?.body).toEqual({
      revision: 3,
      platforms: ['tiktok'],
      languages: ['id', 'en'],
    })
  })

  test('every part of the copy is shown, with what produced it', async () => {
    stubBrand({ [CAMPAIGN_OUTPUTS]: { body: { campaignOutputs: [output()] } } })
    renderWithApi(
      <WorkspaceProvider>
        <CampaignPanel editId={EDIT_ID} revision={3} />
      </WorkspaceProvider>,
    )

    const item = await screen.findByRole('listitem')
    expect(within(item).getByText('Formulir itu batasnya')).toBeVisible()
    expect(within(item).getByText(/kutipan/i)).toBeVisible()
    expect(within(item).getByText(/ikuti untuk klip lainnya/i)).toBeVisible()
    expect(within(item).getByText(/#produk/)).toBeVisible()
    expect(within(item).getByText(/wajah pembicara/i)).toBeVisible()
    expect(within(item).getByText(/alkohol/i)).toBeVisible()
    expect(within(item).getByText(/revision 3/i)).toBeVisible()
    expect(within(item).getByText(/without a language model/i)).toBeVisible()
  })

  test('a caveat on the clip is shown beside the copy it qualifies', async () => {
    stubBrand({
      [CAMPAIGN_OUTPUTS]: {
        body: {
          campaignOutputs: [
            output({
              warnings: [
                { type: 'claim_needs_source', detail: 'Copy ini mengulang klaim: dijamin untung' },
              ],
            }),
          ],
        },
      },
    })
    renderWithApi(
      <WorkspaceProvider>
        <CampaignPanel editId={EDIT_ID} revision={3} />
      </WorkspaceProvider>,
    )

    const item = await screen.findByRole('listitem')
    expect(within(item).getByText(/claim needs source/i)).toBeVisible()
    expect(within(item).getByText(/dijamin untung/i)).toBeVisible()
  })

  test('copy that contains markup is shown as the text somebody said', async () => {
    stubBrand({
      [CAMPAIGN_OUTPUTS]: {
        body: { campaignOutputs: [output({ postCopy: 'Lihat <img src=x> ini' })] },
      },
    })
    const { container } = renderWithApi(
      <WorkspaceProvider>
        <CampaignPanel editId={EDIT_ID} revision={3} />
      </WorkspaceProvider>,
    )

    expect(await screen.findByText(/Lihat <img src=x> ini/)).toBeVisible()
    expect(container.querySelector('img')).toBeNull()
  })

  test('copying one post puts exactly that post on the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    stubBrand({ [CAMPAIGN_OUTPUTS]: { body: { campaignOutputs: [output()] } } })
    renderWithApi(
      <WorkspaceProvider>
        <CampaignPanel editId={EDIT_ID} revision={3} />
      </WorkspaceProvider>,
    )
    const item = await screen.findByRole('listitem')

    await userEvent.click(within(item).getByRole('button', { name: /copy post/i }))

    expect(writeText).toHaveBeenCalledWith(output().postCopy)
  })

  test('nothing here posts anything: the composer is a link a member follows', async () => {
    const api = stubBrand({ [CAMPAIGN_OUTPUTS]: { body: { campaignOutputs: [output()] } } })
    renderWithApi(
      <WorkspaceProvider>
        <CampaignPanel editId={EDIT_ID} revision={3} />
      </WorkspaceProvider>,
    )

    const composer = await screen.findByRole('link', { name: /open the publishing composer/i })
    expect(composer).toHaveAttribute(
      'href',
      expect.stringContaining(`/dashboard/publishing?editId=${EDIT_ID}&revision=3`),
    )
    expect(api.calls.every((request) => request.method === 'GET')).toBe(true)
  })

  test('a refusal is reported with the identifier support can trace it by', async () => {
    stubBrand({
      [CREATE_CAMPAIGN_OUTPUTS]: {
        status: 403,
        body: {
          error: { code: 'FORBIDDEN', message: 'You cannot do that.', requestId: 'req-77' },
        },
      },
    })
    renderWithApi(
      <WorkspaceProvider>
        <CampaignPanel editId={EDIT_ID} revision={3} />
      </WorkspaceProvider>,
    )

    await userEvent.click(await screen.findByRole('button', { name: /write campaign copy/i }))

    expect(await screen.findByText(/req-77/)).toBeVisible()
  })
})

describe('opening a clip with a look and a brand', () => {
  test('the versions a member picked are what the Edit is created from', async () => {
    const api = stubBrand({
      [`POST /api/v1/projects/${candidate().projectId}/candidates/${candidate().id}/edits`]: {
        status: 201,
        body: edit(),
      },
    })
    renderWithApi(
      <WorkspaceProvider>
        <ClipCard candidate={candidate()} />
      </WorkspaceProvider>,
    )

    await userEvent.selectOptions(
      await screen.findByRole('combobox', { name: /look/i }),
      template().id,
    )
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /brand/i }), brandKit().id)
    await userEvent.click(screen.getByRole('button', { name: /edit this clip/i }))

    const call = api.calls.find((request) => request.method === 'POST')
    expect(call?.body).toEqual({ templateId: template().id, brandKitId: brandKit().id })
  })

  test('a Workspace that publishes no look is offered no choice to make', async () => {
    stubBrand({
      [TEMPLATES]: { body: { templates: [] } },
      [BRAND_KITS]: { body: { brandKits: [] } },
    })
    renderWithApi(
      <WorkspaceProvider>
        <ClipCard candidate={candidate()} />
      </WorkspaceProvider>,
    )

    expect(await screen.findByRole('button', { name: /edit this clip/i })).toBeVisible()
    expect(screen.queryByRole('combobox', { name: /look/i })).toBeNull()
  })
})
