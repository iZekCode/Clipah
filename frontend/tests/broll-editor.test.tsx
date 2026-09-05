/**
 * The B-roll copilot: proposals a member reads, decides on, and then edits like anything else.
 *
 * The rule the whole feature turns on is that a proposal is not an edit. Nothing the
 * planner suggested reaches the preview, the timeline, or the export until a member
 * accepts it, and once accepted it stops being a suggestion and becomes an ordinary
 * overlay that trims, moves, and deletes like every other one.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { ClipDetail } from '@/features/clips/ClipDetail'
import { EditorScreen } from '@/features/editor/EditorScreen'
import {
  canonicalJson,
  editorReducer,
  initialEditorState,
  type BrollPlacement,
  type EditorAction,
  type EditorState,
} from '@/features/editor/store'
import type { BrollSuggestionResponse } from '@/lib/api/generated/model'

import { renderWithApi, stubApi, errorBody, type StubbedApi } from './support/api'
import { composition, currentUser, edit, project, workspace } from './support/fixtures'

const EDIT_ID = edit().id
const PROJECT_ID = project().id
const CANDIDATE_ID = edit().candidateId
const BROLL_ASSET = '11111111-2222-4333-8444-555555555555'
const OTHER_ASSET = '99999999-8888-4777-8666-555555555555'
const SUGGESTION_ID = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const SHOW_EDIT = `GET /api/v1/edits/${EDIT_ID}`
const SAVE_EDIT = `PUT /api/v1/edits/${EDIT_ID}`
const DECIDE = `POST /api/v1/edits/${EDIT_ID}/broll-decisions`
const PROXY = `GET /api/v1/projects/${PROJECT_ID}/proxy`
const ASSETS = `GET /api/v1/projects/${PROJECT_ID}/assets`
const SUGGESTIONS = `GET /api/v1/projects/${PROJECT_ID}/candidates/${CANDIDATE_ID}/broll-suggestions`
const PLAN = `POST /api/v1/projects/${PROJECT_ID}/candidates/${CANDIDATE_ID}/broll-plans`
const RETRIEVE = `POST /api/v1/projects/${PROJECT_ID}/candidates/${CANDIDATE_ID}/broll-retrievals`

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/editor',
}))

/** Apply a sequence of actions to a fresh state, the way the screen would. */
function reduce(...actions: EditorAction[]): EditorState {
  return actions.reduce(editorReducer, initialEditorState(composition()))
}

/** One suggestion as the planner placed it, in the source time the backend reports. */
function placement(overrides: Partial<BrollPlacement> = {}): BrollPlacement {
  return {
    suggestionId: SUGGESTION_ID,
    assetId: BROLL_ASSET,
    mediaKind: 'video',
    startMs: 6_000,
    endMs: 10_000,
    ...overrides,
  }
}

/** One suggestion as the API reports it, licensed and ready to decide on. */
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
    endMs: 10_000,
    durationMs: 4_000,
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
    sourceType: 'stock',
    assetId: BROLL_ASSET,
    provenance: {
      provider: 'pexels',
      author: 'A Photographer',
      authorUrl: 'https://example.test/authors/1',
      sourceUrl: 'https://example.test/videos/1',
      licenseName: 'Pexels License',
      licenseUrl: 'https://example.test/license',
      attributionText: 'Video by A Photographer on Pexels',
      generated: false,
    },
    relevanceScore: 0.91,
    createdAt: '2026-02-01T00:00:00+00:00',
    decidedAt: null,
    ...overrides,
  }
}

/** Stub the whole editor screen, with whichever suggestions the test wants to show. */
function stubEditor(
  suggestions: BrollSuggestionResponse[],
  overrides: Record<string, unknown> = {},
): StubbedApi {
  return stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [SHOW_EDIT]: { body: edit() },
    [SAVE_EDIT]: { body: edit({ currentRevision: 2 }) },
    [DECIDE]: { body: edit({ currentRevision: 2 }) },
    [PROXY]: {
      body: { url: 'https://cdn.test/proxy.mp4', contentType: 'video/mp4', width: 1080, height: 1920, durationMs: 30_000 },
    },
    [ASSETS]: { body: { assets: [] } },
    [SUGGESTIONS]: { body: { suggestions } },
    [PLAN]: { status: 202, body: { jobId: 'job-1', status: 'queued' } },
    [RETRIEVE]: { status: 202, body: { jobId: 'job-2', status: 'queued' } },
    ...overrides,
  } as Parameters<typeof stubApi>[0])
}

/** Open the editor and wait until the B-roll panel has finished its first read. */
async function openEditor(): Promise<void> {
  renderWithApi(<EditorScreen editId={EDIT_ID} />)
  await screen.findByRole('region', { name: /b-roll/i })
}

describe('the document a decision produces', () => {
  test('a proposed suggestion is absent from the composition until it is accepted', () => {
    const state = reduce()

    expect(state.composition.overlays).toEqual([])
  })

  test('accepting a suggestion creates exactly one overlay that preserves the dialogue', () => {
    const state = reduce({ type: 'acceptSuggestion', placement: placement() })

    expect(state.composition.overlays).toHaveLength(1)
    const overlay = state.composition.overlays[0]
    expect(overlay).toMatchObject({
      type: 'video',
      assetId: BROLL_ASSET,
      preserveDialogueAudio: true,
      origin: { type: 'brollSuggestion', suggestionId: SUGGESTION_ID, provenanceId: null },
    })
  })

  test('an accepted suggestion lands at the placement time the planner decided', () => {
    // The backend reports beats in the source's own time; the composition starts at
    // `sourceRange.inMs`, so a shot at 6s of a clip that begins at 1s is drawn at 5s.
    const state = reduce({ type: 'acceptSuggestion', placement: placement() })

    expect(state.composition.overlays[0]).toMatchObject({
      timelineStartMs: 5_000,
      timelineEndMs: 9_000,
    })
  })

  test('accepting the same suggestion twice leaves one overlay', () => {
    const once = reduce({ type: 'acceptSuggestion', placement: placement() })
    const twice = editorReducer(once, { type: 'acceptSuggestion', placement: placement() })

    expect(twice.composition.overlays).toHaveLength(1)
    expect(canonicalJson(twice.composition)).toBe(canonicalJson(once.composition))
  })

  test('a still image is accepted as an image overlay with a gentle move', () => {
    const state = reduce({
      type: 'acceptSuggestion',
      placement: placement({ mediaKind: 'image' }),
    })

    expect(state.composition.overlays[0]).toMatchObject({
      type: 'image',
      assetId: BROLL_ASSET,
      motion: 'kenBurnsIn',
    })
  })

  test('replacing the media keeps the overlay, its window, and its suggestion', () => {
    const accepted = reduce({ type: 'acceptSuggestion', placement: placement() })
    const replaced = editorReducer(accepted, {
      type: 'replaceSuggestionMedia',
      suggestionId: SUGGESTION_ID,
      assetId: OTHER_ASSET,
    })

    const before = accepted.composition.overlays[0]
    const after = replaced.composition.overlays[0]
    expect(after).toMatchObject({
      id: before?.id,
      assetId: OTHER_ASSET,
      timelineStartMs: before?.timelineStartMs,
      timelineEndMs: before?.timelineEndMs,
      origin: { type: 'brollSuggestion', suggestionId: SUGGESTION_ID },
    })
  })

  test('a replacement can be reversed to the picture it replaced', () => {
    const accepted = reduce({ type: 'acceptSuggestion', placement: placement() })
    const replaced = editorReducer(accepted, {
      type: 'replaceSuggestionMedia',
      suggestionId: SUGGESTION_ID,
      assetId: OTHER_ASSET,
    })

    const reversed = editorReducer(replaced, { type: 'undo' })

    expect(canonicalJson(reversed.composition)).toBe(canonicalJson(accepted.composition))
  })

  test('removing a suggestion deletes only its own overlay', () => {
    const withText = reduce({ type: 'addText', text: 'A member wrote this' })
    const accepted = editorReducer(withText, {
      type: 'acceptSuggestion',
      placement: placement(),
    })

    const removed = editorReducer(accepted, {
      type: 'removeSuggestion',
      suggestionId: SUGGESTION_ID,
    })

    expect(removed.composition.overlays.map((overlay) => overlay.type)).toEqual(['text'])
  })

  test('removing a suggestion nothing placed changes nothing', () => {
    const state = reduce()
    const removed = editorReducer(state, {
      type: 'removeSuggestion',
      suggestionId: SUGGESTION_ID,
    })

    expect(canonicalJson(removed.composition)).toBe(canonicalJson(state.composition))
  })

  test('undo and redo restore the exact document either side of a decision', () => {
    const before = reduce()
    const accepted = editorReducer(before, { type: 'acceptSuggestion', placement: placement() })

    const undone = editorReducer(accepted, { type: 'undo' })
    const redone = editorReducer(undone, { type: 'redo' })

    expect(canonicalJson(undone.composition)).toBe(canonicalJson(before.composition))
    expect(canonicalJson(redone.composition)).toBe(canonicalJson(accepted.composition))
  })

  test('an accepted suggestion is an ordinary overlay the existing operations edit', () => {
    const accepted = reduce({ type: 'acceptSuggestion', placement: placement() })
    const overlayId = accepted.composition.overlays[0]?.id ?? ''

    const moved = editorReducer(accepted, {
      type: 'moveOverlay',
      overlayId,
      startMs: 12_000,
      endMs: 15_000,
    })
    const deleted = editorReducer(moved, { type: 'deleteOverlay', overlayId })

    expect(moved.composition.overlays[0]).toMatchObject({
      timelineStartMs: 12_000,
      timelineEndMs: 15_000,
    })
    expect(deleted.composition.overlays).toEqual([])
  })
})

describe('the clip detail page', () => {
  test('every picture in the clip is listed with the licence that traces it', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [SUGGESTIONS]: { body: { suggestions: [suggestion({ status: 'placed' })] } },
    })
    renderWithApi(<ClipDetail projectId={PROJECT_ID} candidateId={CANDIDATE_ID} />)

    const row = await screen.findByRole('article', { name: /a shortened signup form/i })
    await userEvent.click(within(row).getByRole('button', { name: /where this came from/i }))

    expect(within(row).getByRole('link', { name: /a photographer/i })).toHaveAttribute(
      'href',
      'https://example.test/authors/1',
    )
    expect(within(row).getByText(/on this clip/i)).toBeInTheDocument()
  })

  test('generated media is labelled on the clip page too', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [SUGGESTIONS]: {
        body: {
          suggestions: [
            suggestion({
              status: 'placed',
              sourceType: 'generated',
              provenance: { ...suggestion().provenance!, generated: true },
            }),
          ],
        },
      },
    })
    renderWithApi(<ClipDetail projectId={PROJECT_ID} candidateId={CANDIDATE_ID} />)

    const row = await screen.findByRole('article', { name: /a shortened signup form/i })
    expect(within(row).getByText(/ai-generated/i)).toBeInTheDocument()
  })

  test('a clip reached without its Project says how to open it properly', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
    })
    renderWithApi(<ClipDetail projectId={null} candidateId={CANDIDATE_ID} />)

    expect(await screen.findByText(/open this clip from its project/i)).toBeInTheDocument()
  })

  test('a suggestion nobody accepted contributes no licence to the clip', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [SUGGESTIONS]: { body: { suggestions: [suggestion({ status: 'rejected' })] } },
    })
    renderWithApi(<ClipDetail projectId={PROJECT_ID} candidateId={CANDIDATE_ID} />)

    expect(await screen.findByText(/no b-roll in this clip/i)).toBeInTheDocument()
  })
})

describe('the B-roll panel', () => {
  test('coverage cannot be chosen until a member asks for suggestions', async () => {
    stubEditor([])
    await openEditor()

    expect(screen.getByRole('combobox', { name: /coverage/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /suggest b-roll/i })).toBeEnabled()
  })

  test('asking for B-roll plans the clip and then searches for its pictures', async () => {
    const api = stubEditor([])
    await openEditor()

    await userEvent.click(screen.getByRole('button', { name: /suggest b-roll/i }))

    await waitFor(() => {
      expect(api.calls.filter((call) => call.path.endsWith('/broll-plans'))).toHaveLength(1)
    })
    await waitFor(() => {
      expect(api.calls.filter((call) => call.path.endsWith('/broll-retrievals'))).toHaveLength(1)
    })
    expect(screen.getByRole('combobox', { name: /coverage/i })).toBeEnabled()
  })

  test('pressing the button twice buys one plan and one search', async () => {
    // A second press is a replay rather than a second purchase: the backend binds one
    // key to one Job, so pressing again reaches the work already admitted.
    const api = stubEditor([])
    await openEditor()
    const button = screen.getByRole('button', { name: /suggest b-roll/i })

    await userEvent.click(button)
    await userEvent.click(button)

    await waitFor(() => {
      expect(api.calls.filter((call) => call.path.endsWith('/broll-plans'))).toHaveLength(2)
    })
    for (const suffix of ['/broll-plans', '/broll-retrievals']) {
      const keys = api.calls
        .filter((call) => call.path.endsWith(suffix))
        .map((call) => call.headers.get('Idempotency-Key'))
      expect(new Set(keys).size).toBe(1)
    }
  })

  test('a clip with no suggestions yet says so rather than looking broken', async () => {
    stubEditor([])
    await openEditor()

    expect(await screen.findByText(/no b-roll suggestions yet/i)).toBeInTheDocument()
  })

  test('a failure to read suggestions reports the request identifier', async () => {
    stubEditor([], { [SUGGESTIONS]: { status: 500, body: errorBody(500) } })
    await openEditor()

    expect(await screen.findByText(/request-1234/)).toBeInTheDocument()
  })

  test('a spent stock budget is explained rather than reported as a failure', async () => {
    stubEditor([], {
      [RETRIEVE]: { status: 429, body: errorBody(429, 'QUOTA_EXCEEDED') },
    })
    await openEditor()

    await userEvent.click(screen.getByRole('button', { name: /suggest b-roll/i }))

    expect(
      await screen.findByText(/monthly stock allowance/i),
    ).toBeInTheDocument()
  })

  test('a suggestion explains what it wants to show and why it belongs there', async () => {
    stubEditor([suggestion()])
    await openEditor()

    const card = await screen.findByRole('article', { name: /a shortened signup form/i })
    expect(within(card).getByText(/the sentence names an object/i)).toBeInTheDocument()
    expect(within(card).getByText(/82%/)).toBeInTheDocument()
    expect(within(card).getByText(/0:05/)).toBeInTheDocument()
  })

  test('provenance names the author, the source, and the licence', async () => {
    stubEditor([suggestion()])
    await openEditor()
    const card = await screen.findByRole('article', { name: /a shortened signup form/i })

    await userEvent.click(within(card).getByRole('button', { name: /where this came from/i }))

    const source = within(card).getByRole('link', { name: /view the original/i })
    const author = within(card).getByRole('link', { name: /a photographer/i })
    const licence = within(card).getByRole('link', { name: /pexels license/i })
    expect(source).toHaveAttribute('href', 'https://example.test/videos/1')
    expect(author).toHaveAttribute('href', 'https://example.test/authors/1')
    expect(licence).toHaveAttribute('href', 'https://example.test/license')
  })

  test('media a model drew is labelled as generated', async () => {
    stubEditor([
      suggestion({
        sourceType: 'generated',
        provenance: {
          provider: 'fal',
          author: 'Generated by Clipah',
          authorUrl: '',
          sourceUrl: '',
          licenseName: 'Generated media',
          licenseUrl: '',
          attributionText: 'Generated media',
          generated: true,
        },
      }),
    ])
    await openEditor()

    const card = await screen.findByRole('article', { name: /a shortened signup form/i })
    expect(within(card).getByText(/ai-generated/i)).toBeInTheDocument()
  })

  test('a suggestion still waiting for a picture cannot be accepted', async () => {
    stubEditor([suggestion({ assetId: null, provenance: null, sourceType: null })])
    await openEditor()

    const card = await screen.findByRole('article', { name: /a shortened signup form/i })
    expect(within(card).getByRole('button', { name: /^accept$/i })).toBeDisabled()
    expect(within(card).getByText(/no picture found/i)).toBeInTheDocument()
  })

  test('accepting a suggestion sends the decision with the document it produced', async () => {
    const api = stubEditor([suggestion()])
    await openEditor()
    const card = await screen.findByRole('article', { name: /a shortened signup form/i })

    await userEvent.click(within(card).getByRole('button', { name: /^accept$/i }))

    await waitFor(() => {
      expect(api.calls.some((call) => call.path.endsWith('/broll-decisions'))).toBe(true)
    })
    const decision = api.calls.find((call) => call.path.endsWith('/broll-decisions'))
    const body = decision?.body as {
      expectedRevision: number
      decision: { suggestionId: string; action: string }
      composition: { overlays: Array<{ assetId?: string; preserveDialogueAudio?: boolean }> }
    }
    expect(body.decision).toEqual({ suggestionId: SUGGESTION_ID, action: 'accept' })
    expect(body.expectedRevision).toBe(1)
    expect(body.composition.overlays[0]).toMatchObject({
      assetId: BROLL_ASSET,
      preserveDialogueAudio: true,
    })
  })

  test('rejecting a suggestion sends the refusal and leaves the clip alone', async () => {
    const api = stubEditor([suggestion()])
    await openEditor()
    const card = await screen.findByRole('article', { name: /a shortened signup form/i })

    await userEvent.click(within(card).getByRole('button', { name: /^reject$/i }))

    await waitFor(() => {
      expect(api.calls.some((call) => call.path.endsWith('/broll-decisions'))).toBe(true)
    })
    const decision = api.calls.find((call) => call.path.endsWith('/broll-decisions'))
    const body = decision?.body as {
      decision: { action: string }
      composition: { overlays: unknown[] }
    }
    expect(body.decision.action).toBe('reject')
    expect(body.composition.overlays).toEqual([])
  })

  test('a placed suggestion offers to remove the picture it put on the timeline', async () => {
    const api = stubEditor([suggestion({ status: 'placed' })])
    await openEditor()
    const card = await screen.findByRole('article', { name: /a shortened signup form/i })

    await userEvent.click(within(card).getByRole('button', { name: /^remove$/i }))

    await waitFor(() => {
      expect(api.calls.some((call) => call.path.endsWith('/broll-decisions'))).toBe(true)
    })
    const decision = api.calls.find((call) => call.path.endsWith('/broll-decisions'))
    expect((decision?.body as { decision: { action: string } }).decision.action).toBe('remove')
  })

  test('a decision another tab already made is reported rather than overwritten', async () => {
    stubEditor([suggestion()], {
      [DECIDE]: {
        status: 409,
        body: errorBody(409, 'EDIT_REVISION_CONFLICT'),
        headers: { 'X-Clipah-Current-Revision': '4' },
      },
    })
    await openEditor()
    const card = await screen.findByRole('article', { name: /a shortened signup form/i })

    await userEvent.click(within(card).getByRole('button', { name: /^accept$/i }))

    expect(await screen.findByText(/changed since you opened it/i)).toBeInTheDocument()
  })

  test('every suggestion control is reachable and named for a screen reader', async () => {
    stubEditor([suggestion()])
    await openEditor()
    const card = await screen.findByRole('article', { name: /a shortened signup form/i })

    const accept = within(card).getByRole('button', { name: /^accept$/i })
    accept.focus()

    expect(accept).toHaveFocus()
    expect(within(card).getByRole('button', { name: /^reject$/i })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: /b-roll/i })).toBeInTheDocument()
  })

  test('an accepted suggestion is drawn on the timeline where it plays', async () => {
    stubEditor([suggestion()])
    await openEditor()
    const card = await screen.findByRole('article', { name: /a shortened signup form/i })

    await userEvent.click(within(card).getByRole('button', { name: /^accept$/i }))

    const lane = await screen.findByRole('group', { name: /overlays/i })
    const shot = within(lane).getByRole('button', { name: /b-roll from 0:05\.0 to 0:09\.0/i })
    expect(shot).toBeInTheDocument()
  })

  test('the overlay lane is absent while a clip carries no overlays', async () => {
    stubEditor([suggestion()])
    await openEditor()

    expect(screen.queryByRole('group', { name: /overlays/i })).toBeNull()
  })

  test('a suggestion is rendered as text, whatever the model wrote in it', async () => {
    stubEditor([
      suggestion({
        visualIntent: {
          ...suggestion().visualIntent,
          subject: '<img src=x onerror="alert(1)">a shortened signup form',
        },
      }),
    ])
    await openEditor()

    const card = await screen.findByRole('article', { name: /a shortened signup form/i })
    expect(card.querySelector('img')).toBeNull()
    expect(
      within(card).getByText(/<img src=x onerror="alert\(1\)">a shortened signup form/),
    ).toBeInTheDocument()
  })
})
