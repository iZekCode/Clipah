/**
 * The advanced editor: multi-track timeline, assets, sound, text, and scenes.
 *
 * Everything here is still one composition document. A drag, a ripple delete, a music
 * bed, a text overlay, and a scene label are all changes to that document, all of them
 * reversible, and all of them expressed in integer milliseconds so that what a member
 * sees on the timeline is exactly what the renderer will be asked to reproduce.
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { EditorScreen } from '@/features/editor/EditorScreen'
import {
  MIN_ITEM_MS,
  SNAP_THRESHOLD_PX,
  canonicalJson,
  editorReducer,
  initialEditorState,
  isTrackLocked,
  scenes,
  snap,
  snapTargets,
  snapThresholdMs,
  timelineItems,
  trackItems,
  type EditorAction,
  type EditorState,
} from '@/features/editor/store'
import type { CompositionV1 } from '@/lib/api/generated/model'

import { renderWithApi, stubApi, type StubbedApi } from './support/api'
import { composition, currentUser, edit, project, workspace } from './support/fixtures'

const MUSIC_ASSET_ID = '99999999-9999-4999-8999-999999999999'
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

/** Apply a sequence of actions to a fresh state, the way the screen would. */
function reduce(...actions: EditorAction[]): EditorState {
  return actions.reduce(editorReducer, initialEditorState(composition()))
}

/** Apply a sequence of actions to a state that already holds a music bed. */
function reduceWithMusic(...actions: EditorAction[]): EditorState {
  return actions.reduce(editorReducer, initialEditorState(withMusic()))
}

/** One composition carrying a second lane, so track-scoped behaviour can be asked about. */
function withMusic(overrides: Partial<CompositionV1> = {}): CompositionV1 {
  const base = composition()
  return {
    ...base,
    tracks: [
      ...base.tracks,
      {
        id: 'music-1',
        type: 'music',
        items: [
          {
            id: 'bed-1',
            sourceAssetId: MUSIC_ASSET_ID,
            timelineStartMs: 4_000,
            sourceInMs: 0,
            sourceOutMs: 6_000,
            transform: { x: 0.5, y: 0.5, scale: 1, rotation: 0 },
            crop: null,
            opacity: 1,
            blendMode: 'normal',
            motion: 'none',
            origin: { type: 'source', suggestionId: null, provenanceId: null },
            keyframes: [],
          },
        ],
      },
    ],
    ...overrides,
  }
}

/** The items of one track, in the order the timeline plays them. */
function itemsOf(state: EditorState, trackId: string) {
  return trackItems(state.composition, trackId)
}

describe('multi-track timeline operations', () => {
  test('duplicating an item plays the same media twice and moves nothing else', () => {
    const state = reduce(
      { type: 'split', itemId: 'scene-1', atMs: 12_000 },
      { type: 'duplicateItem', itemId: 'scene-1' },
    )

    const items = timelineItems(state.composition)
    expect(items.map((placed) => placed.startMs)).toEqual([0, 12_000, 24_000])
    expect(items[1]?.item.sourceInMs).toBe(items[0]?.item.sourceInMs)
    expect(items[1]?.item.sourceOutMs).toBe(items[0]?.item.sourceOutMs)
    expect(new Set(items.map((placed) => placed.item.id)).size).toBe(3)
    expect(state.composition.durationMs).toBe(42_000)
  })

  test('a duplicate carries no captions of its own and pushes the later ones back', () => {
    const state = reduce(
      { type: 'split', itemId: 'scene-1', atMs: 12_000 },
      { type: 'duplicateItem', itemId: 'scene-1' },
    )

    const words = state.composition.captions.words
    expect(words.map((word) => word.id)).toEqual(['w000001', 'w000002', 'w000003', 'w000004'])
    expect(words[2]?.startMs).toBe(24_000)
    expect(words[3]?.startMs).toBe(32_000)
  })

  test('splitting away the right side keeps everything up to the playhead', () => {
    const state = reduce({ type: 'splitSide', itemId: 'scene-1', atMs: 12_000, keep: 'left' })

    const items = timelineItems(state.composition)
    expect(items).toHaveLength(1)
    expect(items[0]?.endMs).toBe(12_000)
    expect(state.composition.durationMs).toBe(12_000)
    expect(state.composition.captions.words.map((word) => word.id)).toEqual([
      'w000001',
      'w000002',
    ])
  })

  test('splitting away the left side pulls what remains back to the start', () => {
    const state = reduce({ type: 'splitSide', itemId: 'scene-1', atMs: 12_000, keep: 'right' })

    const items = timelineItems(state.composition)
    expect(items).toHaveLength(1)
    expect(items[0]?.startMs).toBe(0)
    expect(items[0]?.item.sourceInMs).toBe(13_000)
    expect(state.composition.durationMs).toBe(18_000)
    expect(state.composition.captions.words.map((word) => word.id)).toEqual([
      'w000003',
      'w000004',
    ])
  })

  test('resizing an edge is a trim, and the timeline follows it', () => {
    const state = reduce({ type: 'resizeItem', itemId: 'scene-1', edge: 'end', toMs: 20_000 })

    expect(timelineItems(state.composition)[0]?.endMs).toBe(20_000)
    expect(state.composition.durationMs).toBe(20_000)
  })

  test('resizing the leading edge of a later item moves it earlier without a gap', () => {
    const state = reduce(
      { type: 'split', itemId: 'scene-1', atMs: 12_000 },
      { type: 'resizeItem', itemId: 'scene-1-2', edge: 'start', toMs: 15_000 },
    )

    const items = timelineItems(state.composition)
    expect(items[0]?.endMs).toBe(12_000)
    expect(items[1]?.startMs).toBe(12_000)
    expect(items[1]?.item.sourceInMs).toBe(16_000)
    expect(state.composition.durationMs).toBe(27_000)
  })

  test('no resize leaves an item shorter than the editor will draw', () => {
    const state = reduce({ type: 'resizeItem', itemId: 'scene-1', edge: 'end', toMs: 100 })

    expect(timelineItems(state.composition)[0]?.endMs).toBe(MIN_ITEM_MS)
  })

  test('dragging one video item over another reorders the two and carries the captions', () => {
    const state = reduce(
      { type: 'split', itemId: 'scene-1', atMs: 12_000 },
      { type: 'moveItem', itemId: 'scene-1-2', toMs: 0 },
    )

    const items = timelineItems(state.composition)
    expect(items.map((placed) => placed.item.id)).toEqual(['scene-1-2', 'scene-1'])
    expect(items[0]?.startMs).toBe(0)
    expect(items[1]?.startMs).toBe(18_000)
    const words = state.composition.captions.words
    expect(words.find((word) => word.id === 'w000003')?.startMs).toBe(0)
    expect(words.find((word) => word.id === 'w000001')?.startMs).toBe(18_000)
  })

  test('a sound item is dragged to where it was dropped and keeps its own offset', () => {
    const state = reduceWithMusic({ type: 'moveItem', itemId: 'bed-1', toMs: 9_000 })

    expect(itemsOf(state, 'music-1')[0]?.timelineStartMs).toBe(9_000)
    expect(itemsOf(state, 'main-video')[0]?.timelineStartMs).toBe(0)
  })

  test('a sound item may not be dragged onto another item on its own track', () => {
    const state = reduceWithMusic(
      {
        type: 'addSound',
        kind: 'music',
        assetId: MUSIC_ASSET_ID,
        atMs: 20_000,
        sourceInMs: 0,
        sourceOutMs: 5_000,
      },
      { type: 'moveItem', itemId: 'bed-1', toMs: 19_000 },
    )

    const items = itemsOf(state, 'music-1')
    expect(items[0]?.timelineStartMs).toBe(14_000)
    expect(items[1]?.timelineStartMs).toBe(20_000)
  })

  test('deleting a sound item leaves the gap it occupied unless a ripple is asked for', () => {
    const kept = reduceWithMusic(
      {
        type: 'addSound',
        kind: 'music',
        assetId: MUSIC_ASSET_ID,
        atMs: 20_000,
        sourceInMs: 0,
        sourceOutMs: 5_000,
      },
      { type: 'deleteItem', itemId: 'bed-1' },
    )
    const rippled = reduceWithMusic(
      {
        type: 'addSound',
        kind: 'music',
        assetId: MUSIC_ASSET_ID,
        atMs: 20_000,
        sourceInMs: 0,
        sourceOutMs: 5_000,
      },
      { type: 'deleteItem', itemId: 'bed-1', ripple: true },
    )

    expect(itemsOf(kept, 'music-1')[0]?.timelineStartMs).toBe(20_000)
    expect(itemsOf(rippled, 'music-1')[0]?.timelineStartMs).toBe(14_000)
  })

  test('an item on a locked track refuses every operation aimed at it', () => {
    const locked = reduceWithMusic({ type: 'toggleTrackLock', trackId: 'music-1' })
    const attempted = [
      { type: 'moveItem', itemId: 'bed-1', toMs: 12_000 },
      { type: 'resizeItem', itemId: 'bed-1', edge: 'end', toMs: 12_000 },
      { type: 'deleteItem', itemId: 'bed-1' },
      { type: 'duplicateItem', itemId: 'bed-1' },
      { type: 'split', itemId: 'bed-1', atMs: 6_000 },
    ] satisfies EditorAction[]

    expect(isTrackLocked(locked, 'music-1')).toBe(true)
    for (const action of attempted) {
      expect(canonicalJson(editorReducer(locked, action).composition)).toBe(
        canonicalJson(locked.composition),
      )
    }
  })
})

describe('assets, sound, and the source monitor', () => {
  test('a marked range of the source is added to the end of the timeline', () => {
    const state = reduce(
      { type: 'markIn', ms: 4_000 },
      { type: 'markOut', ms: 9_000 },
      { type: 'addFromSource' },
    )

    const items = timelineItems(state.composition)
    expect(items).toHaveLength(2)
    expect(items[1]?.startMs).toBe(30_000)
    expect(items[1]?.item.sourceInMs).toBe(5_000)
    expect(items[1]?.item.sourceOutMs).toBe(10_000)
    expect(state.composition.durationMs).toBe(35_000)
  })

  test('a source range is added only when the marks describe a usable span', () => {
    const state = reduce(
      { type: 'markIn', ms: 4_000 },
      { type: 'markOut', ms: 4_100 },
      { type: 'addFromSource' },
    )

    expect(timelineItems(state.composition)).toHaveLength(1)
  })

  test('adding an asset to a sound track places it at the moment it was dropped', () => {
    const state = reduce(
      { type: 'addTrack', trackType: 'music' },
      {
        type: 'addSound',
        kind: 'music',
        assetId: MUSIC_ASSET_ID,
        atMs: 5_000,
        sourceInMs: 0,
        sourceOutMs: 8_000,
      },
    )

    const items = itemsOf(state, 'music-1')
    expect(state.composition.tracks.map((track) => track.type)).toEqual(['video', 'music'])
    expect(items[0]?.timelineStartMs).toBe(5_000)
    expect(items[0]?.sourceAssetId).toBe(MUSIC_ASSET_ID)
  })

  test('extracting audio reuses one extracted-audio lane rather than making another', () => {
    const state = reduce(
      {
        type: 'addSound',
        kind: 'extractedAudio',
        assetId: MUSIC_ASSET_ID,
        atMs: 0,
        sourceInMs: 0,
        sourceOutMs: 6_000,
      },
      {
        type: 'addSound',
        kind: 'extractedAudio',
        assetId: MUSIC_ASSET_ID,
        atMs: 10_000,
        sourceInMs: 0,
        sourceOutMs: 6_000,
      },
    )

    const extracted = state.composition.tracks.filter((track) => track.type === 'extractedAudio')
    expect(extracted).toHaveLength(1)
    expect(extracted[0]?.items.map((item) => item.timelineStartMs)).toEqual([0, 10_000])
  })

  test('sound levels are set on the composition, never on an item', () => {
    const state = reduce({ type: 'audio', patch: { gainDb: -6, musicGainDb: -12 } })

    expect(state.composition.audio).toEqual({ gainDb: -6, musicGainDb: -12 })
  })
})

describe('text overlays', () => {
  test('adding text places one overlay at the playhead with a readable window', () => {
    const state = reduce({ type: 'seek', ms: 6_000 }, { type: 'addText', text: 'Watch this' })

    const overlay = state.composition.overlays[0]
    expect(state.composition.overlays).toHaveLength(1)
    expect(overlay?.type).toBe('text')
    expect(overlay?.timelineStartMs).toBe(6_000)
    expect(overlay?.timelineEndMs).toBe(9_000)
  })

  test('an overlay added near the end still stops inside the clip', () => {
    const state = reduce({ type: 'seek', ms: 29_500 }, { type: 'addText', text: 'Last word' })

    const overlay = state.composition.overlays[0]
    expect(overlay?.timelineEndMs).toBe(30_000)
    expect(overlay?.timelineStartMs).toBe(29_500 - 0)
  })

  test('overlay text and timing are edited without touching anything else', () => {
    const state = reduce(
      { type: 'addText', text: 'Draft' },
      { type: 'updateOverlay', overlayId: 'text-1', patch: { text: 'Final' } },
      { type: 'moveOverlay', overlayId: 'text-1', startMs: 5_000, endMs: 8_000 },
    )

    const overlay = state.composition.overlays[0]
    expect(overlay?.type === 'text' ? overlay.text : null).toBe('Final')
    expect([overlay?.timelineStartMs, overlay?.timelineEndMs]).toEqual([5_000, 8_000])
    expect(timelineItems(state.composition)).toHaveLength(1)
  })

  test('an overlay is removed by identity and nothing else goes with it', () => {
    const state = reduce(
      { type: 'addText', text: 'One' },
      { type: 'seek', ms: 10_000 },
      { type: 'addText', text: 'Two' },
      { type: 'deleteOverlay', overlayId: 'text-1' },
    )

    expect(state.composition.overlays).toHaveLength(1)
    expect(state.composition.overlays[0]?.id).toBe('text-2')
  })

  test('empty text is not an overlay', () => {
    const state = reduce({ type: 'addText', text: '   ' })

    expect(state.composition.overlays).toHaveLength(0)
  })
})

describe('bookmarks and scenes', () => {
  test('a bookmark is added at the playhead and kept in timeline order', () => {
    const state = reduce(
      { type: 'seek', ms: 12_000 },
      { type: 'addBookmark', label: 'Payoff' },
      { type: 'seek', ms: 3_000 },
      { type: 'addBookmark', label: 'Hook' },
    )

    expect(state.composition.bookmarks.map((bookmark) => bookmark.timelineMs)).toEqual([
      3_000,
      12_000,
    ])
    expect(state.composition.bookmarks.map((bookmark) => bookmark.label)).toEqual([
      'Hook',
      'Payoff',
    ])
  })

  test('a bookmark is renamed and removed by identity', () => {
    const added = reduce({ type: 'seek', ms: 4_000 }, { type: 'addBookmark', label: 'Draft' })
    const bookmarkId = added.composition.bookmarks[0]?.id ?? ''
    const renamed = editorReducer(added, {
      type: 'renameBookmark',
      bookmarkId,
      label: 'Hook lands',
    })
    const removed = editorReducer(renamed, { type: 'removeBookmark', bookmarkId })

    expect(renamed.composition.bookmarks[0]?.label).toBe('Hook lands')
    expect(removed.composition.bookmarks).toHaveLength(0)
  })

  test('scenes follow the speakers the transcript recorded', () => {
    const document = composition()
    const words = document.captions.words.map((word, index) =>
      index < 2 ? word : { ...word, speaker: 'SPEAKER_01' },
    )
    const state = initialEditorState({
      ...document,
      captions: { ...document.captions, words },
    })

    const list = scenes(state.composition)

    expect(list.map((entry) => entry.speaker)).toEqual(['SPEAKER_00', 'SPEAKER_01'])
    expect(list[0]?.startMs).toBe(0)
    expect(list[1]?.startMs).toBe(12_000)
    expect(list[1]?.endMs).toBe(20_900)
  })

  test('a scene carries the label a member gave the marker that starts it', () => {
    const state = reduce({ type: 'seek', ms: 0 }, { type: 'addBookmark', label: 'Cold open' })

    expect(scenes(state.composition)[0]?.label).toBe('Cold open')
  })
})

describe('snapping', () => {
  test('the snap targets are the edges a member would expect to land on', () => {
    const state = reduce(
      { type: 'split', itemId: 'scene-1', atMs: 12_000 },
      { type: 'seek', ms: 4_000 },
      { type: 'addBookmark', label: 'Hook' },
    )

    expect(snapTargets(state.composition)).toEqual([0, 4_000, 12_000, 30_000])
  })

  test('a value inside the threshold snaps and a value outside it does not', () => {
    const targets = [0, 12_000, 30_000]

    expect(snap(11_800, targets, 300)).toBe(12_000)
    expect(snap(11_600, targets, 300)).toBe(11_600)
  })

  test('the snapping threshold is a fixed distance on screen, so zoom decides it in time', () => {
    expect(snapThresholdMs(8)).toBe((SNAP_THRESHOLD_PX / 8) * 1_000)
    expect(snapThresholdMs(64)).toBeLessThan(snapThresholdMs(8))
  })
})

describe('undo and redo', () => {
  const operations: Array<[string, EditorAction[]]> = [
    ['duplicate', [{ type: 'duplicateItem', itemId: 'scene-1' }]],
    ['split away the right side', [{ type: 'splitSide', itemId: 'scene-1', atMs: 12_000, keep: 'left' }]],
    ['resize', [{ type: 'resizeItem', itemId: 'scene-1', edge: 'end', toMs: 20_000 }]],
    [
      'reorder',
      [
        { type: 'split', itemId: 'scene-1', atMs: 12_000 },
        { type: 'moveItem', itemId: 'scene-1-2', toMs: 0 },
      ],
    ],
    ['add text', [{ type: 'addText', text: 'Watch this' }]],
    ['add a bookmark', [{ type: 'seek', ms: 4_000 }, { type: 'addBookmark', label: 'Hook' }]],
    ['set the sound levels', [{ type: 'audio', patch: { gainDb: -6 } }]],
    [
      'add an asset to a sound track',
      [
        { type: 'addTrack', trackType: 'music' },
        {
          type: 'addSound',
          kind: 'music',
          assetId: MUSIC_ASSET_ID,
          atMs: 5_000,
          sourceInMs: 0,
          sourceOutMs: 8_000,
        },
      ],
    ],
  ]

  test.each(operations)('undoing %s restores the exact document it started from', (_name, actions) => {
    const before = canonicalJson(composition())

    const applied = reduce(...actions)
    const undone = actions.reduce((state) => editorReducer(state, { type: 'undo' }), applied)

    expect(canonicalJson(applied.composition)).not.toBe(before)
    expect(canonicalJson(undone.composition)).toBe(before)
  })

  test.each(operations)('redoing %s restores the exact document undo reversed', (_name, actions) => {
    const applied = reduce(...actions)
    const after = canonicalJson(applied.composition)

    const undone = actions.reduce((state) => editorReducer(state, { type: 'undo' }), applied)
    const redone = actions.reduce((state) => editorReducer(state, { type: 'redo' }), undone)

    expect(canonicalJson(redone.composition)).toBe(after)
  })
})

describe('the editor screen at full width', () => {
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
      [ASSETS]: {
        body: {
          assets: [
            {
              id: MUSIC_ASSET_ID,
              kind: 'source',
              contentType: 'audio/mpeg',
              sizeBytes: 2_048,
              durationMs: 12_000,
              width: null,
              height: null,
              createdAt: '2026-02-01T00:00:00+00:00',
            },
          ],
        },
      },
      [SAVE_EDIT]: (request) => ({
        body: {
          ...edit(),
          currentRevision: 2,
          composition: (request.body as { composition: CompositionV1 }).composition,
        },
      }),
    })
  }

  /** Open the editor on one composition and wait for the timeline to exist. */
  async function openEditor(document: CompositionV1 = composition()): Promise<void> {
    api = stub(document)
    renderWithApi(<EditorScreen editId={EDIT_ID} />)
    await screen.findByRole('region', { name: /timeline/i })
  }

  /**
   * Save, then read back the composition the backend was asked to store.
   *
   * A test that changed the document more than once waits for the Revision it means to
   * read with `until`, because autosave may already have sent an earlier one.
   */
  async function saved(until: (document: CompositionV1) => boolean = () => true): Promise<CompositionV1> {
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => {
      expect(latest() !== null && until(latest() as CompositionV1)).toBe(true)
    })
    return latest() as CompositionV1
  }

  /** The composition of the newest save the backend was asked to store. */
  function latest(): CompositionV1 | null {
    const last = api.calls.filter((call) => call.method === 'PUT').at(-1)
    return last === undefined ? null : (last.body as { composition: CompositionV1 }).composition
  }

  /**
   * Drag one control from where it is by a distance in pixels.
   *
   * jsdom has no `PointerEvent`, so the gesture is dispatched as mouse events carrying
   * the pointer event's own type: the listeners under test read `clientX` and nothing else.
   */
  function drag(element: Element, byPx: number): void {
    element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 0, button: 0 }))
    window.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: byPx }))
  }

  test('the timeline draws one row for every lane the composition carries', async () => {
    await openEditor(withMusic())

    const timeline = screen.getByRole('region', { name: /timeline/i })
    expect(within(timeline).getByRole('group', { name: /video lane main-video/i })).toBeVisible()
    expect(within(timeline).getByRole('group', { name: /music lane music-1/i })).toBeVisible()
  })

  test('dragging a sound item lands it where it was dropped and snaps to the clip end', async () => {
    await openEditor(withMusic())

    drag(screen.getByRole('button', { name: 'Select bed-1' }), 319)

    expect(trackItems(await saved(), 'music-1')[0]?.timelineStartMs).toBe(24_000)
  })

  test('dragging a trim handle never leaves an item shorter than the minimum', async () => {
    await openEditor(withMusic())

    drag(screen.getByRole('button', { name: /trim the end of bed-1/i }), -400)

    const item = trackItems(await saved(), 'music-1')[0]
    expect((item?.sourceOutMs ?? 0) - (item?.sourceInMs ?? 0)).toBe(MIN_ITEM_MS)
  })

  test('a locked lane refuses the drag a member aims at it', async () => {
    const user = userEvent.setup()
    await openEditor(withMusic())

    await user.click(screen.getByRole('checkbox', { name: /lock music-1/i }))
    const item = screen.getByRole('button', { name: 'Select bed-1' })
    expect(item).toHaveAttribute('aria-disabled', 'true')
    drag(item, 160)
    await user.click(screen.getByRole('checkbox', { name: /lock music-1/i }))
    // One accepted change, so there is a Revision to read the refused one back from.
    await user.click(screen.getByRole('button', { name: /add marker/i }))

    const document = await saved()
    expect(trackItems(document, 'music-1')[0]?.timelineStartMs).toBe(4_000)
    expect(document.bookmarks).toHaveLength(1)
  })

  test('an item is moved from the keyboard as well as from the pointer', async () => {
    const user = userEvent.setup()
    await openEditor(withMusic())

    const item = screen.getByRole('button', { name: 'Select bed-1' })
    item.focus()
    await user.keyboard('{Alt>}{ArrowRight}{/Alt}')

    expect(trackItems(await saved(), 'music-1')[0]?.timelineStartMs).toBe(4_100)
  })

  test('a marker is left at the playhead and jumped back to afterwards', async () => {
    const user = userEvent.setup()
    await openEditor()

    fireEvent.change(screen.getByLabelText(/scrub the clip/i), { target: { value: '12000' } })
    await user.type(screen.getByLabelText(/marker label/i), 'Payoff')
    await user.click(screen.getByRole('button', { name: /add marker/i }))
    fireEvent.change(screen.getByLabelText(/scrub the clip/i), { target: { value: '0' } })
    await user.click(screen.getByRole('button', { name: /next marker/i }))

    expect(screen.getByLabelText(/scrub the clip/i)).toHaveValue('12000')
    expect((await saved()).bookmarks).toEqual([
      { id: 'bookmark-1', timelineMs: 12_000, label: 'Payoff' },
    ])
  })

  test('a lane is selected on its own, and the selection is announced', async () => {
    const user = userEvent.setup()
    await openEditor(withMusic())

    const lane = screen.getByRole('button', { name: /select the lane music-1/i })
    await user.click(lane)

    expect(lane).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /select the lane main-video/i })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  test('a marker is removed from the timeline it was left on', async () => {
    const user = userEvent.setup()
    await openEditor()

    await user.type(screen.getByLabelText(/marker label/i), 'Payoff')
    await user.click(screen.getByRole('button', { name: /add marker/i }))
    await user.click(screen.getByRole('button', { name: /remove the marker payoff/i }))

    expect((await saved((document) => document.bookmarks.length === 0)).bookmarks).toEqual([])
  })

  test('naming a scene twice renames its marker rather than leaving two', async () => {
    const user = userEvent.setup()
    await openEditor()

    const list = screen.getByRole('region', { name: /scenes/i })
    const field = within(list).getAllByLabelText(/label the scene at/i)[0] as HTMLElement
    await user.type(field, 'Cold open')
    await user.click(within(list).getAllByRole('button', { name: /^label$/i })[0] as HTMLElement)
    await user.clear(field)
    await user.type(field, 'Hook')
    await user.click(within(list).getAllByRole('button', { name: /^label$/i })[0] as HTMLElement)

    const document = await saved((saving) => saving.bookmarks[0]?.label === 'Hook')
    expect(document.bookmarks).toEqual([{ id: 'bookmark-1', timelineMs: 0, label: 'Hook' }])
  })

  test('the assets panel offers the Project media and places it on a sound lane', async () => {
    const user = userEvent.setup()
    await openEditor()

    const assets = await screen.findByRole('region', { name: /assets/i })
    expect(within(assets).getByText(/audio\/mpeg/i)).toBeVisible()
    await user.click(within(assets).getByRole('button', { name: /add to a sound lane/i }))

    const document = await saved()
    const music = document.tracks.find((track) => track.type === 'music')
    expect(music?.items[0]?.sourceAssetId).toBe(MUSIC_ASSET_ID)
    expect(music?.items[0]?.sourceOutMs).toBe(12_000)
  })

  test('the assets panel extracts one asset onto its own audio lane', async () => {
    const user = userEvent.setup()
    await openEditor()

    const assets = await screen.findByRole('region', { name: /assets/i })
    await user.click(within(assets).getByRole('button', { name: /extract audio/i }))

    const document = await saved()
    expect(document.tracks.filter((track) => track.type === 'extractedAudio')).toHaveLength(1)
  })

  test('a marked span of the source is added to the end of the timeline', async () => {
    const user = userEvent.setup()
    await openEditor()

    const monitor = screen.getByRole('region', { name: /source monitor/i })
    fireEvent.change(within(monitor).getByLabelText(/source position/i), {
      target: { value: '4000' },
    })
    await user.click(within(monitor).getByRole('button', { name: /mark in/i }))
    fireEvent.change(within(monitor).getByLabelText(/source position/i), {
      target: { value: '9000' },
    })
    await user.click(within(monitor).getByRole('button', { name: /mark out/i }))
    await user.click(within(monitor).getByRole('button', { name: /add to timeline/i }))

    const document = await saved()
    expect(timelineItems(document)).toHaveLength(2)
    expect(timelineItems(document)[1]?.item.sourceInMs).toBe(5_000)

    await user.click(within(monitor).getByRole('button', { name: /clear marks/i }))

    expect(within(monitor).getByRole('button', { name: /add to timeline/i })).toBeDisabled()
  })

  test('the scene list names each speaker and jumps to the scene it describes', async () => {
    const user = userEvent.setup()
    const document = composition()
    await openEditor({
      ...document,
      captions: {
        ...document.captions,
        words: document.captions.words.map((word, index) =>
          index < 2 ? word : { ...word, speaker: 'SPEAKER_01' },
        ),
      },
    })

    const list = screen.getByRole('region', { name: /scenes/i })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    await user.click(within(list).getAllByRole('button', { name: /go to/i })[1] as HTMLElement)

    expect(screen.getByLabelText(/scrub the clip/i)).toHaveValue('12000')
  })

  test('text is written onto the clip and taken off it again', async () => {
    const user = userEvent.setup()
    await openEditor()

    const panel = screen.getByRole('region', { name: /^text$/i })
    await user.type(within(panel).getByLabelText(/new text/i), 'Watch this')
    await user.click(within(panel).getByRole('button', { name: /add text/i }))

    const withText = await saved()
    expect(withText.overlays).toHaveLength(1)
    expect(withText.overlays[0]?.type).toBe('text')

    await user.click(within(panel).getByRole('button', { name: /remove text-1/i }))

    expect((await saved((document) => document.overlays.length === 0)).overlays).toHaveLength(0)
  })

  test('sound levels are set for the dialogue and for the beds under it', async () => {
    const user = userEvent.setup()
    await openEditor()

    const panel = screen.getByRole('region', { name: /^sound$/i })
    const dialogue = within(panel).getByLabelText(/dialogue level/i)
    await user.clear(dialogue)
    await user.type(dialogue, '-6')
    await user.tab()

    expect((await saved()).audio.gainDb).toBe(-6)
  })

  test('the toolbar splits away one side of the selected item', async () => {
    const user = userEvent.setup()
    await openEditor()

    fireEvent.change(screen.getByLabelText(/scrub the clip/i), { target: { value: '12000' } })
    await user.click(screen.getByRole('button', { name: /split away the right/i }))

    const document = await saved()
    expect(document.durationMs).toBe(12_000)
    expect(timelineItems(document)).toHaveLength(1)
  })

  test('the toolbar duplicates the selected item', async () => {
    const user = userEvent.setup()
    await openEditor()

    await user.click(screen.getByRole('button', { name: /^duplicate$/i }))

    expect(timelineItems(await saved())).toHaveLength(2)
  })

  test('a ripple delete pulls the rest of a sound lane back over the gap', async () => {
    const user = userEvent.setup()
    const base = withMusic()
    const music = base.tracks[1]
    await openEditor({
      ...base,
      tracks: [
        base.tracks[0] as CompositionV1['tracks'][number],
        {
          ...(music as CompositionV1['tracks'][number]),
          items: [
            ...(music?.items ?? []),
            {
              ...(music?.items[0] as CompositionV1['tracks'][number]['items'][number]),
              id: 'bed-2',
              timelineStartMs: 20_000,
            },
          ],
        },
      ],
    })

    await user.click(screen.getByRole('checkbox', { name: /ripple/i }))
    await user.click(screen.getByRole('button', { name: 'Select bed-1' }))
    await user.click(screen.getByRole('button', { name: /^delete$/i }))

    expect(trackItems(await saved(), 'music-1')[0]?.timelineStartMs).toBe(14_000)
  })
})
