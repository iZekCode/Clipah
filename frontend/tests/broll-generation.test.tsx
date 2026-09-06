/**
 * Generated B-roll: a price a member agrees to before any model is asked to draw anything.
 *
 * Two rules shape every test here. Generation is offered only where stock could not
 * answer the beat, so a good picture suppresses the offer entirely. And nothing bills a
 * Workspace until a member has read the estimate and confirmed it — twice, for video.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test } from 'vitest'

import { BrollPanel } from '@/features/broll/BrollPanel'
import type {
  BrollSuggestionResponse,
  GenerationOfferResponse,
} from '@/lib/api/generated/model'

import {
  renderWithApi,
  stubApi,
  type RecordedRequest,
  type StubbedApi,
} from './support/api'
import { currentUser, project, workspace } from './support/fixtures'

const PROJECT_ID = project().id
const WORKSPACE_ID = workspace().id
const CANDIDATE_ID = '77777777-8888-4999-8aaa-bbbbbbbbbbbb'
const SUGGESTION_ID = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
const GENERATED_ASSET = '12121212-3434-4565-8787-909090909090'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const ASSETS = `GET /api/v1/projects/${PROJECT_ID}/assets`
const SUGGESTIONS = `GET /api/v1/projects/${PROJECT_ID}/candidates/${CANDIDATE_ID}/broll-suggestions`
const ESTIMATE = `POST /api/v1/broll-suggestions/${SUGGESTION_ID}/generation-estimates`
const GENERATE = `POST /api/v1/broll-suggestions/${SUGGESTION_ID}/generate`

/** One proposal whose beat stock could not illustrate. */
function suggestion(overrides: Partial<BrollSuggestionResponse> = {}): BrollSuggestionResponse {
  return {
    id: SUGGESTION_ID,
    projectId: PROJECT_ID,
    candidateId: CANDIDATE_ID,
    coverage: 'balanced',
    plannerVersion: 'broll-plan/1',
    beatStartWordId: 'w000002',
    beatEndWordId: 'w000003',
    startMs: 6_000,
    endMs: 11_000,
    durationMs: 5_000,
    visualIntent: {
      subject: 'a shortened signup form',
      action: 'a hand deleting form fields',
      setting: 'a laptop screen on a desk',
      mood: 'focused',
      portraitSuitable: true,
      factualRiskFlags: [],
      confidence: 0.82,
    },
    searchTerms: { id: ['formulir pendaftaran'], en: ['signup form'] },
    exclusions: [],
    status: 'proposed',
    placementReason: 'The sentence names an object the viewer cannot see',
    sourceType: null,
    assetId: null,
    provenance: null,
    relevanceScore: null,
    createdAt: '2026-02-01T00:00:00+00:00',
    decidedAt: null,
    ...overrides,
  }
}

/** The estimate a member is shown for one still. */
function imageOffer(overrides: Partial<GenerationOfferResponse> = {}): GenerationOfferResponse {
  return {
    available: true,
    reason: null,
    videoOffered: false,
    confirmationToken: 'sealed-image-token',
    estimate: {
      mediaKind: 'image',
      outputCount: 1,
      durationMs: null,
      width: 1080,
      height: 1920,
      latencyClass: 'standard',
      imageUnits: '1',
      videoUnits: '0',
      generatedSeconds: '0',
      providerCredits: '1',
      costUsd: '0.08',
    },
    ...overrides,
  }
}

/** The estimate a member is shown for one generated clip. */
function videoOffer(overrides: Partial<GenerationOfferResponse> = {}): GenerationOfferResponse {
  return {
    available: true,
    reason: null,
    videoOffered: false,
    confirmationToken: 'sealed-video-token',
    estimate: {
      mediaKind: 'video',
      outputCount: 1,
      durationMs: 5_000,
      width: 720,
      height: 1280,
      latencyClass: 'slow',
      imageUnits: '0',
      videoUnits: '1',
      generatedSeconds: '5',
      providerCredits: '25',
      costUsd: '0.25',
    },
    ...overrides,
  }
}

/** Stub the panel's reads, with whichever offers and suggestions the test wants. */
function stubPanel(
  suggestions: BrollSuggestionResponse[],
  overrides: Record<string, unknown> = {},
): StubbedApi {
  return stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [ASSETS]: { body: { assets: [] } },
    [SUGGESTIONS]: { body: { suggestions } },
    [ESTIMATE]: { body: imageOffer() },
    [GENERATE]: { status: 202, body: { jobId: 'job-generate-1', status: 'queued' } },
    ...overrides,
  } as Parameters<typeof stubApi>[0])
}

/** Read every recorded request to one stubbed route. */
function callsTo(api: StubbedApi, route: string): RecordedRequest[] {
  const [method, path] = route.split(' ')
  return api.calls.filter((call) => call.method === method && call.path === path)
}

/** Render the panel the way the editor does, and wait for its first read. */
async function openPanel(): Promise<void> {
  renderWithApi(
    <BrollPanel
      projectId={PROJECT_ID}
      candidateId={CANDIDATE_ID}
      workspaceId={WORKSPACE_ID}
      clipStartMs={1_000}
      deciding={false}
      onDecide={() => {}}
    />,
  )
  await screen.findByRole('region', { name: /b-roll/i })
}

describe('offering to generate a picture', () => {
  test('a beat with no picture offers to generate a still', async () => {
    stubPanel([suggestion()])
    await openPanel()

    expect(await screen.findByRole('button', { name: /generate still/i })).toBeVisible()
  })

  test('a good stock picture suppresses the offer entirely', async () => {
    stubPanel([suggestion({ assetId: GENERATED_ASSET, relevanceScore: 0.91, sourceType: 'stock' })])
    await openPanel()

    await screen.findByRole('button', { name: /accept/i })
    expect(screen.queryByRole('button', { name: /generate still/i })).toBeNull()
  })

  test('a decided suggestion is never offered another generation', async () => {
    stubPanel([suggestion({ status: 'rejected' })])
    await openPanel()

    expect(screen.queryByRole('button', { name: /generate still/i })).toBeNull()
  })
})

describe('the estimate a member agrees to', () => {
  test('the complete still estimate is shown before any work is admitted', async () => {
    const api = stubPanel([suggestion()])
    const user = userEvent.setup()
    await openPanel()

    await user.click(await screen.findByRole('button', { name: /generate still/i }))

    const dialog = await screen.findByRole('dialog', { name: /generate a still/i })
    expect(dialog).toHaveTextContent('$0.08')
    expect(dialog).toHaveTextContent('1080 × 1920')
    expect(callsTo(api, GENERATE)).toHaveLength(0)
  })

  test('an unavailable provider says so instead of showing a price', async () => {
    stubPanel([suggestion()], {
      [ESTIMATE]: {
        body: {
          available: false,
          reason: 'provider_unavailable',
          videoOffered: false,
          estimate: null,
          confirmationToken: null,
        },
      },
    })
    const user = userEvent.setup()
    await openPanel()

    await user.click(await screen.findByRole('button', { name: /generate still/i }))

    expect(await screen.findByRole('dialog')).toHaveTextContent(/not available/i)
  })

  test('confirming a still submits the sealed token exactly once', async () => {
    const api = stubPanel([suggestion()])
    const user = userEvent.setup()
    await openPanel()

    await user.click(await screen.findByRole('button', { name: /generate still/i }))
    await user.click(await screen.findByRole('button', { name: /generate for \$0\.08/i }))

    await waitFor(() => expect(callsTo(api, GENERATE)).toHaveLength(1))
    expect(callsTo(api, GENERATE)[0]?.body).toEqual({
      confirmationToken: 'sealed-image-token',
      videoConfirmed: false,
    })
    expect(callsTo(api, GENERATE)[0]?.headers.get('Idempotency-Key')).toBe(
      `generate:${SUGGESTION_ID}:image`,
    )
  })

  test('a quota refusal is explained in words a member can act on', async () => {
    stubPanel([suggestion()], {
      [GENERATE]: {
        status: 429,
        body: { error: { code: 'QUOTA_EXCEEDED', message: 'no', requestId: 'r1' } },
      },
    })
    const user = userEvent.setup()
    await openPanel()

    await user.click(await screen.findByRole('button', { name: /generate still/i }))
    await user.click(await screen.findByRole('button', { name: /generate for \$0\.08/i }))

    expect(await screen.findByRole('status')).toHaveTextContent(/monthly/i)
  })
})

describe('generated video, which is never one click away', () => {
  test('video is not offered while the deployment has it disabled', async () => {
    stubPanel([suggestion()], {
      [ESTIMATE]: { body: imageOffer() },
    })
    const user = userEvent.setup()
    await openPanel()

    await user.click(await screen.findByRole('button', { name: /generate still/i }))
    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).queryByRole('button', { name: /consider video/i })).toBeNull()
  })

  test('a video estimate opens its own confirmation and submits only when confirmed', async () => {
    const api = stubPanel([suggestion()], {
      [ESTIMATE]: { body: imageOffer({ videoOffered: true }) },
    })
    const user = userEvent.setup()
    await openPanel()

    await user.click(await screen.findByRole('button', { name: /generate still/i }))
    api.set(ESTIMATE, { body: videoOffer() })
    await user.click(await screen.findByRole('button', { name: /consider video instead/i }))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('$0.25')
    expect(dialog).toHaveTextContent('5s')
    expect(callsTo(api, GENERATE)).toHaveLength(0)

    await user.click(await screen.findByRole('button', { name: /generate video for \$0\.25/i }))
    await waitFor(() => expect(callsTo(api, GENERATE)).toHaveLength(1))
    expect(callsTo(api, GENERATE)[0]?.body).toEqual({
      confirmationToken: 'sealed-video-token',
      videoConfirmed: true,
    })
  })
})

describe('what a member sees while a model is working', () => {
  test('an admitted generation reports that it continues in the background', async () => {
    stubPanel([suggestion()])
    const user = userEvent.setup()
    await openPanel()

    await user.click(await screen.findByRole('button', { name: /generate still/i }))
    await user.click(await screen.findByRole('button', { name: /generate for \$0\.08/i }))

    expect(await screen.findByRole('status')).toHaveTextContent(/background/i)
  })

  test('a suggestion already generating offers no second generation', async () => {
    stubPanel([suggestion({ status: 'generating' })])
    await openPanel()

    expect(screen.queryByRole('button', { name: /generate still/i })).toBeNull()
    expect(await screen.findByText(/generating/i)).toBeVisible()
  })

  test('provider and planner text is rendered as text, never as markup', async () => {
    stubPanel([
      suggestion({
        visualIntent: {
          ...suggestion().visualIntent,
          subject: '<img src=x onerror="alert(1)">a form',
        },
      }),
    ])
    await openPanel()

    expect(await screen.findByText(/<img src=x/)).toBeVisible()
    expect(document.querySelector('img')).toBeNull()
  })
})