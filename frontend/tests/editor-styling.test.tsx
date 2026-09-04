/**
 * Styling, karaoke, keyframes, templates, and motion.
 *
 * Everything here changes how a clip looks rather than what it contains, and all of it is
 * still one composition document. Two rules are worth stating: a caption word's timing is
 * the transcript's evidence until a member deliberately changes it, and a look applied
 * from a template is written into the document with the exact template version it came
 * from, so a later template change can never rewrite a Revision.
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { EditorScreen } from '@/features/editor/EditorScreen'
import {
  MOTIONS,
  TEMPLATES,
  motionDefinition,
  motionFits,
} from '@/features/editor/templates'
import {
  activeWordAt,
  canonicalJson,
  editorReducer,
  initialEditorState,
  interpolatedAt,
  editorReducer as reducer,
  type EditorAction,
  type EditorState,
} from '@/features/editor/store'
import type { CompositionV1 } from '@/lib/api/generated/model'

import { renderWithApi, stubApi, type StubbedApi } from './support/api'
import { composition, currentUser, edit, project, workspace } from './support/fixtures'

const EDIT_ID = edit().id
const PROJECT_ID = project().id
const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const SHOW_EDIT = `GET /api/v1/edits/${EDIT_ID}`
const SAVE_EDIT = `PUT /api/v1/edits/${EDIT_ID}`
const PROXY = `GET /api/v1/projects/${PROJECT_ID}/proxy`
const ASSETS = `GET /api/v1/projects/${PROJECT_ID}/assets`

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/editor',
}))

/** The movement one overlay carries, whichever kind of overlay it is. */
function motionOf(document: CompositionV1, overlayId: string): string | null {
  const overlay = document.overlays.find((candidate) => candidate.id === overlayId)
  return overlay !== undefined && overlay.type !== 'citation' ? overlay.motion : null
}

/** Apply a sequence of actions to a fresh state, the way the screen would. */
function reduce(...actions: EditorAction[]): EditorState {
  return actions.reduce(reducer, initialEditorState(composition()))
}

describe('caption timing and karaoke', () => {
  test('the active word is the one being said, and nothing is active between words', () => {
    const words = composition().captions.words

    expect(activeWordAt(words, 1_200)?.id).toBe('w000002')
    expect(activeWordAt(words, 950)).toBeNull()
    expect(activeWordAt(words, 20_899)?.id).toBe('w000004')
    expect(activeWordAt(words, 29_000)).toBeNull()
  })

  test('a word is retimed only inside the gap its neighbours leave it', () => {
    const state = reduce({
      type: 'retimeWord',
      wordId: 'w000002',
      startMs: 0,
      endMs: 20_000,
    })

    const words = state.composition.captions.words
    expect(words[1]?.startMs).toBe(900)
    expect(words[1]?.endMs).toBe(12_000)
    expect(words[0]?.endMs).toBe(900)
    expect(words[2]?.startMs).toBe(12_000)
  })

  test('a retimed word never ends before it starts', () => {
    const state = reduce({
      type: 'retimeWord',
      wordId: 'w000002',
      startMs: 5_000,
      endMs: 4_000,
    })

    const word = state.composition.captions.words[1]
    expect((word?.endMs ?? 0) - (word?.startMs ?? 0)).toBeGreaterThan(0)
  })

  test('retiming one word leaves every other word exactly where the transcript put it', () => {
    const before = composition().captions.words
    const state = reduce({ type: 'retimeWord', wordId: 'w000002', startMs: 1_500, endMs: 2_500 })

    const after = state.composition.captions.words
    expect([after[0], after[2], after[3]]).toEqual([before[0], before[2], before[3]])
  })
})

describe('keyframes', () => {
  const framing = { x: 0.3, y: 0.5, scale: 1, rotation: 0 }

  test('a keyframe is added to the selected item and kept in time order', () => {
    const state = reduce(
      { type: 'addKeyframe', targetId: 'scene-1', atMs: 4_000, transform: framing },
      { type: 'addKeyframe', targetId: 'scene-1', atMs: 1_000, transform: framing },
    )

    const item = state.composition.tracks[0]?.items[0]
    expect(item?.keyframes.map((frame) => frame.atMs)).toEqual([1_000, 4_000])
  })

  test('two keyframes never share one instant, because one of them would be unreachable', () => {
    const state = reduce(
      { type: 'addKeyframe', targetId: 'scene-1', atMs: 4_000, transform: framing },
      {
        type: 'addKeyframe',
        targetId: 'scene-1',
        atMs: 4_000,
        transform: { ...framing, x: 0.8 },
      },
    )

    const item = state.composition.tracks[0]?.items[0]
    expect(item?.keyframes).toHaveLength(1)
    expect(item?.keyframes[0]?.transform?.x).toBe(0.8)
  })

  test('a keyframe is moved and removed by the instant it sits at', () => {
    const moved = reduce(
      { type: 'addKeyframe', targetId: 'scene-1', atMs: 4_000, transform: framing },
      { type: 'moveKeyframe', targetId: 'scene-1', atMs: 4_000, toMs: 6_000 },
    )
    const removed = editorReducer(moved, {
      type: 'removeKeyframe',
      targetId: 'scene-1',
      atMs: 6_000,
    })

    expect(moved.composition.tracks[0]?.items[0]?.keyframes[0]?.atMs).toBe(6_000)
    expect(removed.composition.tracks[0]?.items[0]?.keyframes).toHaveLength(0)
  })

  test('a keyframe may not sit past the element it animates', () => {
    const state = reduce({
      type: 'addKeyframe',
      targetId: 'scene-1',
      atMs: 90_000,
      transform: framing,
    })

    expect(state.composition.tracks[0]?.items[0]?.keyframes[0]?.atMs).toBe(30_000)
  })

  test('a value between two keyframes is interpolated, and held outside them', () => {
    const keyframes = [
      { atMs: 0, easing: 'linear' as const, transform: framing, opacity: 1, style: null },
      {
        atMs: 10_000,
        easing: 'linear' as const,
        transform: { ...framing, x: 0.7 },
        opacity: 0,
        style: null,
      },
    ]

    expect(interpolatedAt(keyframes, 5_000).transform?.x).toBeCloseTo(0.5, 5)
    expect(interpolatedAt(keyframes, 5_000).opacity).toBeCloseTo(0.5, 5)
    expect(interpolatedAt(keyframes, -1_000).transform?.x).toBeCloseTo(0.3, 5)
    expect(interpolatedAt(keyframes, 99_000).transform?.x).toBeCloseTo(0.7, 5)
  })

  test('an eased keyframe still arrives at exactly the values it names', () => {
    const keyframes = [
      { atMs: 0, easing: 'easeInOut' as const, transform: framing, opacity: null, style: null },
      {
        atMs: 10_000,
        easing: 'easeInOut' as const,
        transform: { ...framing, x: 0.9 },
        opacity: null,
        style: null,
      },
    ]

    expect(interpolatedAt(keyframes, 0).transform?.x).toBeCloseTo(0.3, 5)
    expect(interpolatedAt(keyframes, 10_000).transform?.x).toBeCloseTo(0.9, 5)
    // A quarter of the way through an eased move is not a quarter of the distance.
    expect(interpolatedAt(keyframes, 2_500).transform?.x).toBeCloseTo(0.375, 5)
    expect(interpolatedAt(keyframes, 7_500).transform?.x).toBeCloseTo(0.825, 5)
  })

  test('with no keyframes at all there is nothing to interpolate', () => {
    expect(interpolatedAt([], 5_000)).toEqual({ transform: null, opacity: null })
  })
})

describe('templates and motion', () => {
  test('the editor offers exactly the templates the backend published', () => {
    expect(TEMPLATES.length).toBeGreaterThan(0)
    for (const template of TEMPLATES) {
      expect(template.version).toBeGreaterThan(0)
      expect(template.captionStyle.fontFamily).toBeTruthy()
    }
  })

  test('applying a template writes its look in and records the version it came from', () => {
    const template = TEMPLATES[0]!
    const state = reduce(
      { type: 'addText', text: 'A title' },
      { type: 'applyTemplate', template },
    )

    expect(state.composition.template).toEqual({ id: template.id, version: template.version })
    expect(state.composition.captions.style).toEqual(template.captionStyle)
    expect(state.composition.captions.mode).toBe(template.captionMode)
    const overlay = state.composition.overlays[0]
    expect(overlay?.type === 'text' ? overlay.style : null).toEqual(template.textStyle)
  })

  test('applying a template changes the look and never the clip', () => {
    const template = TEMPLATES[1]!
    const before = composition()
    const state = reduce({ type: 'applyTemplate', template })

    expect(state.composition.tracks).toEqual(before.tracks)
    expect(state.composition.captions.words).toEqual(before.captions.words)
    expect(state.composition.durationMs).toBe(before.durationMs)
  })

  test('applying one template twice produces exactly the same document', () => {
    const template = TEMPLATES[2]!
    const once = reduce({ type: 'applyTemplate', template })
    const twice = editorReducer(once, { type: 'applyTemplate', template })

    expect(canonicalJson(twice.composition)).toBe(canonicalJson(once.composition))
  })

  test('the editor knows the window every movement is legible within', () => {
    expect(MOTIONS.length).toBeGreaterThan(0)
    expect(motionDefinition('kenBurnsIn').minDurationMs).toBeGreaterThan(0)
    expect(motionFits('kenBurnsIn', 400)).toBe(false)
    expect(motionFits('kenBurnsIn', 4_000)).toBe(true)
    expect(motionFits('none', 10)).toBe(true)
  })

  test('a movement is not applied to an element too short to show it', () => {
    const state = reduce(
      { type: 'seek', ms: 0 },
      { type: 'addText', text: 'Brief' },
      { type: 'moveOverlay', overlayId: 'text-1', startMs: 0, endMs: 700 },
      { type: 'setMotion', targetId: 'text-1', preset: 'fade' },
    )

    expect(motionOf(state.composition, 'text-1')).toBe('none')
  })

  test('a movement inside its window is applied', () => {
    const state = reduce(
      { type: 'addText', text: 'Long enough' },
      { type: 'setMotion', targetId: 'text-1', preset: 'fade' },
    )

    expect(motionOf(state.composition, 'text-1')).toBe('fade')
  })
})

describe('the styling panels', () => {
  let api: StubbedApi

  /** Everything the editor reads before it can draw a clip. */
  function stub(document: CompositionV1): StubbedApi {
    return stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [SHOW_EDIT]: { body: edit({ composition: document }) },
      [PROXY]: {
        body: {
          url: 'https://storage.test/proxy.mp4?signature=short-lived',
          expiresAt: '2026-02-01T00:05:00+00:00',
          contentType: 'video/mp4',
          durationMs: 60_000,
          width: 1920,
          height: 1080,
        },
      },
      [ASSETS]: { body: { assets: [] } },
      [SAVE_EDIT]: (request) => ({
        body: {
          ...edit(),
          currentRevision: 2,
          composition: (request.body as { composition: CompositionV1 }).composition,
        },
      }),
    })
  }

  /** Open the editor and wait for the timeline to exist. */
  async function openEditor(document: CompositionV1 = composition()): Promise<void> {
    api = stub(document)
    renderWithApi(<EditorScreen editId={EDIT_ID} />)
    await screen.findByRole('region', { name: /^timeline$/i })
  }

  /** Save, then read back the composition the backend was asked to store. */
  async function saved(
    until: (document: CompositionV1) => boolean = () => true,
  ): Promise<CompositionV1> {
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => {
      const document = latest()
      expect(document !== null && until(document)).toBe(true)
    })
    return latest() as CompositionV1
  }

  /** The composition of the newest save the backend was asked to store. */
  function latest(): CompositionV1 | null {
    const last = api.calls.filter((call) => call.method === 'PUT').at(-1)
    return last === undefined ? null : (last.body as { composition: CompositionV1 }).composition
  }

  test.each([
    [
      'Caption letter spacing',
      '4',
      (document: CompositionV1) => document.captions.style.letterSpacing,
      4,
    ],
    [
      'Caption line height',
      '1.6',
      (document: CompositionV1) => document.captions.style.lineHeight,
      1.6,
    ],
  ])('the captions panel sets %s', async (label, typed, read, expected) => {
    const user = userEvent.setup()
    await openEditor()

    const panel = screen.getByRole('region', { name: /captions/i })
    const field = within(panel).getByLabelText(new RegExp(label, 'i'))
    await user.clear(field)
    await user.type(field, typed)
    await user.tab()

    expect(read(await saved((document) => read(document) === expected))).toBe(expected)
  })

  test('the captions panel sets weight, italic, and the decoration a renderer can draw', async () => {
    const user = userEvent.setup()
    await openEditor()

    const panel = screen.getByRole('region', { name: /captions/i })
    await user.selectOptions(within(panel).getByLabelText(/caption weight/i), '800')
    await user.click(within(panel).getByLabelText(/caption italic/i))
    await user.selectOptions(within(panel).getByLabelText(/caption decoration/i), 'underline')

    const document = await saved((saving) => saving.captions.style.decoration === 'underline')
    expect(document.captions.style.weight).toBe(800)
    expect(document.captions.style.italic).toBe(true)
    expect(document.captions.style.decoration).toBe('underline')
  })

  test('a template is applied by name and the version is recorded', async () => {
    const user = userEvent.setup()
    await openEditor()
    const template = TEMPLATES[0]!

    const panel = screen.getByRole('region', { name: /templates/i })
    await user.click(within(panel).getByRole('button', { name: new RegExp(template.name, 'i') }))

    const document = await saved((saving) => saving.template !== null)
    expect(document.template).toEqual({ id: template.id, version: template.version })
    expect(document.captions.style.fontFamily).toBe(template.captionStyle.fontFamily)
  })

  test('the karaoke panel retimes one word and says which word is being said', async () => {
    const user = userEvent.setup()
    await openEditor()

    const panel = screen.getByRole('region', { name: /karaoke/i })
    fireEvent.change(screen.getByLabelText(/scrub the clip/i), { target: { value: '1200' } })
    expect(within(panel).getByRole('status')).toHaveTextContent('cara')

    const start = within(panel).getByLabelText(/start of w000002/i)
    await user.clear(start)
    await user.type(start, '1500')
    await user.tab()

    const document = await saved((saving) => saving.captions.words[1]?.startMs === 1_500)
    expect(document.captions.words[1]?.endMs).toBe(1_900)
  })

  test('the keyframe editor animates the selected item and lists what it added', async () => {
    const user = userEvent.setup()
    await openEditor()

    const panel = screen.getByRole('region', { name: /keyframes/i })
    fireEvent.change(screen.getByLabelText(/scrub the clip/i), { target: { value: '2000' } })
    await user.click(within(panel).getByRole('button', { name: /add a keyframe/i }))

    const document = await saved((saving) => saving.tracks[0]!.items[0]!.keyframes.length === 1)
    const keyframe = document.tracks[0]?.items[0]?.keyframes[0]
    expect(keyframe?.atMs).toBe(2_000)
    expect(within(panel).getByText(/0:02\.0/)).toBeVisible()

    await user.click(within(panel).getByRole('button', { name: /remove the keyframe at 0:02\.0/i }))

    expect((await saved((saving) => saving.tracks[0]!.items[0]!.keyframes.length === 0)).tracks[0]
      ?.items[0]?.keyframes).toHaveLength(0)
  })

  test('the motion panel refuses a movement the element is too short to show', async () => {
    const user = userEvent.setup()
    await openEditor()

    const text = screen.getByRole('region', { name: /^text$/i })
    await user.type(within(text).getByLabelText(/new text/i), 'Brief')
    await user.click(within(text).getByRole('button', { name: /add text/i }))
    const start = within(text).getByLabelText(/start of text-1/i)
    fireEvent.change(within(text).getByLabelText(/end of text-1/i), { target: { value: '700' } })
    fireEvent.change(start, { target: { value: '0' } })

    const motion = screen.getByRole('region', { name: /motion/i })
    await user.selectOptions(within(motion).getByLabelText(/movement of text-1/i), 'fade')

    const document = await saved()
    expect(motionOf(document, 'text-1')).toBe('none')
    expect(within(motion).getByText(/needs at least/i)).toBeVisible()
  })

  test('the motion panel applies a movement the element has room for', async () => {
    const user = userEvent.setup()
    await openEditor()

    const text = screen.getByRole('region', { name: /^text$/i })
    await user.type(within(text).getByLabelText(/new text/i), 'Long enough')
    await user.click(within(text).getByRole('button', { name: /add text/i }))

    const motion = screen.getByRole('region', { name: /motion/i })
    await user.selectOptions(within(motion).getByLabelText(/movement of text-1/i), 'fade')

    expect(motionOf(await saved((saving) => motionOf(saving, 'text-1') === 'fade'), 'text-1')).toBe(
      'fade',
    )
  })
})
