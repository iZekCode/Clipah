/**
 * The basic non-destructive editor: its state, its autosave, and its screen.
 *
 * The composition document is the only edit state. Everything a member does is a change
 * to that document, every change is reversible, and every accepted change becomes a new
 * Revision on the backend. Source and proxy media are never touched.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { ClipCard } from '@/features/clips/ClipCard'
import { EditorScreen } from '@/features/editor/EditorScreen'
import {
  AUTOSAVE_DEBOUNCE_MS,
  Autosave,
  memoryDraftStore,
  type SaveResult,
  type SaveStatus,
} from '@/features/editor/autosave'
import {
  ASPECT_CANVAS,
  MIN_ITEM_MS,
  canUndo,
  canonicalJson,
  editorReducer,
  initialEditorState,
  isDirty,
  timelineItems,
  type EditorAction,
  type EditorState,
} from '@/features/editor/store'
import { ApiError } from '@/lib/api/client'
import type { CompositionV1 } from '@/lib/api/generated/model'

import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi, type StubbedApi } from './support/api'
import { candidate, composition, currentUser, edit, workspace } from './support/fixtures'

const push = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/dashboard/projects',
}))

const EDIT_ID = edit().id
const PROJECT_ID = edit().projectId
const WORKSPACE_ID = workspace().id
const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const SHOW_EDIT = `GET /api/v1/edits/${EDIT_ID}`
const SAVE_EDIT = `PUT /api/v1/edits/${EDIT_ID}`
const PROXY = `GET /api/v1/projects/${PROJECT_ID}/proxy`

/** Apply a sequence of actions to a fresh state, the way the screen would. */
function reduce(...actions: EditorAction[]): EditorState {
  return actions.reduce(editorReducer, initialEditorState(composition()))
}

/** The one video item every fixture composition starts with. */
function scene(state: EditorState) {
  const item = state.composition.tracks[0]?.items[0]
  if (item === undefined) {
    throw new Error('the fixture composition always has one video item')
  }
  return item
}

describe('editor state', () => {
  test('a trim never mutates the composition it was given', () => {
    const original = composition()
    const state = initialEditorState(original)
    const snapshot = canonicalJson(original)

    editorReducer(state, { type: 'trim', itemId: 'scene-1', sourceInMs: 5_000, sourceOutMs: 25_000 })

    expect(canonicalJson(original)).toBe(snapshot)
    expect(canonicalJson(state.composition)).toBe(snapshot)
  })

  test('a trim shortens the clip and moves nothing in the source media', () => {
    const state = reduce({
      type: 'trim',
      itemId: 'scene-1',
      sourceInMs: 6_000,
      sourceOutMs: 26_000,
    })

    expect(scene(state).sourceInMs).toBe(6_000)
    expect(scene(state).sourceOutMs).toBe(26_000)
    expect(scene(state).sourceAssetId).toBe(composition().sourceAssetId)
    expect(state.composition.durationMs).toBe(20_000)
    expect(state.composition.sourceRange).toEqual(composition().sourceRange)
  })

  test('a trim is clamped to the source the clip was cut from', () => {
    const state = reduce({
      type: 'trim',
      itemId: 'scene-1',
      sourceInMs: -5_000,
      sourceOutMs: 99_000,
    })

    expect(scene(state).sourceInMs).toBe(composition().sourceRange.inMs)
    expect(scene(state).sourceOutMs).toBe(composition().sourceRange.outMs)
  })

  test('a trim may not shorten an item past the shortest one the editor allows', () => {
    const state = reduce({
      type: 'trim',
      itemId: 'scene-1',
      sourceInMs: 10_000,
      sourceOutMs: 10_100,
    })

    expect(scene(state).sourceOutMs - scene(state).sourceInMs).toBe(MIN_ITEM_MS)
  })

  test('trimming the head carries the captions with it', () => {
    const state = reduce({
      type: 'trim',
      itemId: 'scene-1',
      sourceInMs: 3_000,
      sourceOutMs: 31_000,
    })

    const words = state.composition.captions.words
    expect(words.map((word) => word.id)).toEqual(['w000003', 'w000004'])
    expect(words[0]?.startMs).toBe(10_000)
    expect(words.every((word) => word.endMs <= state.composition.durationMs)).toBe(true)
  })

  test('trimming the tail drops only the words past the new end', () => {
    const state = reduce({
      type: 'trim',
      itemId: 'scene-1',
      sourceInMs: 1_000,
      sourceOutMs: 14_000,
    })

    expect(state.composition.captions.words.map((word) => word.id)).toEqual([
      'w000001',
      'w000002',
      'w000003',
    ])
    expect(state.composition.durationMs).toBe(13_000)
  })

  test('a split produces two items that together play what one item played', () => {
    const state = reduce({ type: 'split', itemId: 'scene-1', atMs: 12_000 })

    const items = timelineItems(state.composition)
    expect(items).toHaveLength(2)
    expect(items[0]?.startMs).toBe(0)
    expect(items[0]?.endMs).toBe(12_000)
    expect(items[1]?.startMs).toBe(12_000)
    expect(items[1]?.endMs).toBe(30_000)
    expect(state.composition.durationMs).toBe(30_000)
    expect(state.composition.captions.words).toHaveLength(4)
  })

  test('a split outside the item changes nothing', () => {
    const state = reduce({ type: 'split', itemId: 'scene-1', atMs: 30_000 })

    expect(timelineItems(state.composition)).toHaveLength(1)
    expect(isDirty(state)).toBe(false)
  })

  test('deleting one part of a split clip removes its captions and closes the gap', () => {
    const state = reduce(
      { type: 'split', itemId: 'scene-1', atMs: 12_000 },
      { type: 'deleteItem', itemId: 'scene-1' },
    )

    const items = timelineItems(state.composition)
    expect(items).toHaveLength(1)
    expect(items[0]?.startMs).toBe(0)
    expect(state.composition.durationMs).toBe(18_000)
    expect(state.composition.captions.words.map((word) => word.id)).toEqual([
      'w000003',
      'w000004',
    ])
    expect(state.composition.captions.words[0]?.startMs).toBe(0)
  })

  test('the last remaining item cannot be deleted', () => {
    const state = reduce({ type: 'deleteItem', itemId: 'scene-1' })

    expect(timelineItems(state.composition)).toHaveLength(1)
    expect(isDirty(state)).toBe(false)
  })

  test('an aspect preset sets the canvas and a centred crop of the source', () => {
    const state = reduce({ type: 'aspect', aspect: '16:9', sourceAspect: 9 / 16 })

    expect(state.composition.canvas.width).toBe(ASPECT_CANVAS['16:9'].width)
    expect(state.composition.canvas.height).toBe(ASPECT_CANVAS['16:9'].height)
    const height = 9 / 16 / (16 / 9)
    expect(scene(state).crop).toEqual({ x: 0, y: (1 - height) / 2, width: 1, height })
  })

  test('a source already matching the preset needs no crop at all', () => {
    const state = reduce({ type: 'aspect', aspect: '9:16', sourceAspect: 1080 / 1920 })

    expect(scene(state).crop).toBeNull()
  })

  test('a crop is stored on the item it frames', () => {
    const crop = { x: 0.1, y: 0, width: 0.8, height: 1 }
    const state = reduce({ type: 'crop', itemId: 'scene-1', crop })

    expect(scene(state).crop).toEqual(crop)
  })

  test('editing a caption word changes its text and none of its timings', () => {
    const state = reduce({ type: 'captionText', wordId: 'w000002', text: 'karya' })

    const words = state.composition.captions.words
    expect(words[1]?.text).toBe('karya')
    expect(words[1]?.startMs).toBe(composition().captions.words[1]?.startMs)
    expect(words[1]?.endMs).toBe(composition().captions.words[1]?.endMs)
  })

  test('an empty caption word is refused rather than stored', () => {
    const state = reduce({ type: 'captionText', wordId: 'w000002', text: '   ' })

    expect(state.composition.captions.words[1]?.text).toBe('cara')
    expect(isDirty(state)).toBe(false)
  })

  test('caption style changes apply to the whole caption layer', () => {
    const state = reduce({
      type: 'captionStyle',
      patch: { fontFamily: 'Anton', fontSize: 72, color: '#FFD166' },
    })

    expect(state.composition.captions.style.fontFamily).toBe('Anton')
    expect(state.composition.captions.style.fontSize).toBe(72)
    expect(state.composition.captions.style.color).toBe('#FFD166')
    expect(state.composition.captions.style.weight).toBe(700)
  })

  test('undo and redo walk the history one change at a time', () => {
    const trimmed = reduce(
      { type: 'trim', itemId: 'scene-1', sourceInMs: 1_000, sourceOutMs: 21_000 },
      { type: 'captionStyle', patch: { fontSize: 48 } },
    )

    const undoneOnce = editorReducer(trimmed, { type: 'undo' })
    expect(undoneOnce.composition.captions.style.fontSize).toBe(64)
    expect(undoneOnce.composition.durationMs).toBe(20_000)

    const undoneTwice = editorReducer(undoneOnce, { type: 'undo' })
    expect(undoneTwice.composition.durationMs).toBe(30_000)
    expect(canUndo(undoneTwice)).toBe(false)
    expect(isDirty(undoneTwice)).toBe(false)

    const redone = editorReducer(editorReducer(undoneTwice, { type: 'redo' }), { type: 'redo' })
    expect(canonicalJson(redone.composition)).toBe(canonicalJson(trimmed.composition))
  })

  test('a new change after an undo abandons the redo branch', () => {
    const state = reduce(
      { type: 'captionStyle', patch: { fontSize: 48 } },
      { type: 'undo' },
      { type: 'captionStyle', patch: { fontSize: 40 } },
    )

    expect(editorReducer(state, { type: 'redo' }).composition.captions.style.fontSize).toBe(40)
  })

  test('a saved composition is clean until it changes again', () => {
    const edited = reduce({ type: 'captionStyle', patch: { fontSize: 48 } })
    expect(isDirty(edited)).toBe(true)

    const saved = editorReducer(edited, { type: 'markSaved', composition: edited.composition })
    expect(isDirty(saved)).toBe(false)

    expect(isDirty(editorReducer(saved, { type: 'captionStyle', patch: { fontSize: 50 } }))).toBe(
      true,
    )
  })

  test('taking the revision from the backend replaces the document and its history', () => {
    const theirs = composition({ durationMs: 12_000 })
    const state = reduce(
      { type: 'captionStyle', patch: { fontSize: 48 } },
      { type: 'replace', composition: theirs },
    )

    expect(state.composition.durationMs).toBe(12_000)
    expect(canUndo(state)).toBe(false)
    expect(isDirty(state)).toBe(false)
  })
})

describe('autosave', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  /** One autosave under a test's control, with everything it touches injected. */
  function harness(
    save: (composition: CompositionV1, expectedRevision: number) => Promise<SaveResult>,
    options: { online?: boolean } = {},
  ) {
    const statuses: SaveStatus[] = []
    const conflicts: number[] = []
    const store = memoryDraftStore()
    let online = options.online ?? true
    const autosave = new Autosave({
      editId: EDIT_ID,
      revision: 1,
      save,
      store,
      isOnline: () => online,
      onStatus: (status) => statuses.push(status),
      onSaved: () => {},
      onConflict: (revision) => conflicts.push(revision ?? -1),
    })
    return {
      autosave,
      statuses,
      conflicts,
      store,
      goOffline: () => {
        online = false
      },
      goOnline: () => {
        online = true
      },
    }
  }

  /** A save that resolves when the test says so. */
  function deferredSave() {
    const calls: Array<{ composition: CompositionV1; revision: number }> = []
    let release: (result: SaveResult) => void = () => {}
    const save = (document: CompositionV1, revision: number) => {
      calls.push({ composition: document, revision })
      return new Promise<SaveResult>((resolve) => {
        release = resolve
      })
    }
    return { calls, save, release: (result: SaveResult) => release(result) }
  }

  test('a burst of changes becomes one save after the debounce window', async () => {
    const saved: number[] = []
    const { autosave } = harness(async (document, revision) => {
      saved.push(revision)
      return { currentRevision: revision + 1, composition: document }
    })

    autosave.queue(composition({ durationMs: 29_000 }))
    autosave.queue(composition({ durationMs: 28_000 }))
    await act(async () => {
      vi.advanceTimersByTime(AUTOSAVE_DEBOUNCE_MS - 1)
    })
    expect(saved).toEqual([])

    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    expect(saved).toEqual([1])
  })

  test('only one save is in flight, and the newest document follows it', async () => {
    const { calls, save, release } = deferredSave()
    const { autosave } = harness(save)

    autosave.queue(composition({ durationMs: 29_000 }))
    await act(async () => {
      vi.advanceTimersByTime(AUTOSAVE_DEBOUNCE_MS)
    })
    autosave.queue(composition({ durationMs: 28_000 }))
    await act(async () => {
      vi.advanceTimersByTime(AUTOSAVE_DEBOUNCE_MS)
    })

    expect(calls).toHaveLength(1)

    await act(async () => {
      release({ currentRevision: 2, composition: composition({ durationMs: 29_000 }) })
      await Promise.resolve()
      vi.advanceTimersByTime(AUTOSAVE_DEBOUNCE_MS)
    })

    expect(calls).toHaveLength(2)
    expect(calls[1]?.composition.durationMs).toBe(28_000)
    expect(calls[1]?.revision).toBe(2)
  })

  test('an offline change is kept and sent when the connection returns', async () => {
    const sent: CompositionV1[] = []
    const harnessed = harness(async (document, revision) => {
      sent.push(document)
      return { currentRevision: revision + 1, composition: document }
    })
    harnessed.goOffline()

    harnessed.autosave.queue(composition({ durationMs: 27_000 }))
    await act(async () => {
      vi.advanceTimersByTime(AUTOSAVE_DEBOUNCE_MS)
    })

    expect(sent).toEqual([])
    expect(harnessed.statuses.at(-1)).toBe('offline')
    expect(harnessed.store.load(EDIT_ID)?.composition.durationMs).toBe(27_000)

    harnessed.goOnline()
    await act(async () => {
      await harnessed.autosave.retryPending()
    })

    expect(sent).toHaveLength(1)
    expect(harnessed.statuses.at(-1)).toBe('saved')
    expect(harnessed.store.load(EDIT_ID)).toBeNull()
  })

  test('a stale revision stops autosaving and reports the conflict', async () => {
    const attempts: number[] = []
    const harnessed = harness(async (_document, revision) => {
      attempts.push(revision)
      throw new ApiError({
        status: 409,
        code: 'EDIT_REVISION_CONFLICT',
        message: 'This clip changed since you opened it.',
        requestId: 'request-1234',
        currentRevision: 4,
      })
    })

    harnessed.autosave.queue(composition({ durationMs: 26_000 }))
    await act(async () => {
      vi.advanceTimersByTime(AUTOSAVE_DEBOUNCE_MS)
    })

    expect(harnessed.statuses.at(-1)).toBe('conflict')
    expect(harnessed.conflicts).toEqual([4])
    expect(harnessed.store.load(EDIT_ID)?.composition.durationMs).toBe(26_000)

    harnessed.autosave.queue(composition({ durationMs: 25_000 }))
    await act(async () => {
      vi.advanceTimersByTime(AUTOSAVE_DEBOUNCE_MS * 4)
    })

    expect(attempts).toEqual([1])
  })

  test('resuming after a conflict saves against the revision the backend now holds', async () => {
    const attempts: number[] = []
    let conflict = true
    const harnessed = harness(async (document, revision) => {
      attempts.push(revision)
      if (conflict) {
        throw new ApiError({
          status: 409,
          code: 'EDIT_REVISION_CONFLICT',
          message: 'This clip changed since you opened it.',
          requestId: 'request-1234',
          currentRevision: 4,
        })
      }
      return { currentRevision: revision + 1, composition: document }
    })

    harnessed.autosave.queue(composition({ durationMs: 26_000 }))
    await act(async () => {
      vi.advanceTimersByTime(AUTOSAVE_DEBOUNCE_MS)
    })
    conflict = false
    await act(async () => {
      harnessed.autosave.resume(4)
      await harnessed.autosave.flush()
    })

    expect(attempts).toEqual([1, 4])
    expect(harnessed.statuses.at(-1)).toBe('saved')
  })

  test('a draft outlives a reload and is offered against the revision it was written on', () => {
    const store = memoryDraftStore()
    store.save(EDIT_ID, { composition: composition({ durationMs: 24_000 }), baseRevision: 3 })

    const restored = store.load(EDIT_ID)

    expect(restored?.baseRevision).toBe(3)
    expect(restored?.composition.durationMs).toBe(24_000)
  })

  test('an explicit save does not wait for the debounce', async () => {
    const saved: CompositionV1[] = []
    const { autosave } = harness(async (document, revision) => {
      saved.push(document)
      return { currentRevision: revision + 1, composition: document }
    })

    autosave.queue(composition({ durationMs: 23_000 }))
    await act(async () => {
      await autosave.flush()
    })

    expect(saved).toHaveLength(1)
  })
})

describe('editor screen', () => {
  let api: StubbedApi

  beforeEach(() => {
    api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [SHOW_EDIT]: { body: edit() },
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
      [SAVE_EDIT]: (request) => ({
        body: {
          ...edit(),
          currentRevision: 2,
          composition: (request.body as { composition: CompositionV1 }).composition,
        },
      }),
    })
  })

  /** Render the editor and wait for the Edit and its proxy to arrive. */
  async function openEditor(): Promise<void> {
    renderWithApi(<EditorScreen editId={EDIT_ID} />)
    await screen.findByRole('region', { name: /timeline/i })
  }

  test('the editor plays the proxy and never the source media', async () => {
    await openEditor()

    const video = await screen.findByTestId('editor-video')
    expect(video).toHaveAttribute('src', expect.stringContaining('proxy.mp4'))
    expect(api.calls.some((call) => call.path.endsWith('/proxy'))).toBe(true)
  })

  test('the timeline shows every item and the captions panel every word', async () => {
    await openEditor()

    const timeline = screen.getByRole('region', { name: /timeline/i })
    expect(within(timeline).getAllByRole('button', { name: /^select scene/i })).toHaveLength(1)

    const captions = screen.getByRole('region', { name: /captions/i })
    expect(within(captions).getAllByRole('textbox', { name: /word at/i })).toHaveLength(4)
  })

  test('trimming through the inspector saves one new revision', async () => {
    const user = userEvent.setup()
    await openEditor()

    const end = screen.getByLabelText(/clip ends at/i)
    await user.clear(end)
    await user.type(end, '21000')
    await user.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => {
      expect(api.calls.filter((call) => call.method === 'PUT')).toHaveLength(1)
    })
    const saved = api.calls.find((call) => call.method === 'PUT')?.body as {
      expectedRevision: number
      composition: CompositionV1
    }
    expect(saved.expectedRevision).toBe(1)
    expect(saved.composition.durationMs).toBe(20_000)
    expect(await screen.findByText(/saved/i)).toBeVisible()
  })

  test('an aspect preset reframes the clip without touching the source', async () => {
    const user = userEvent.setup()
    await openEditor()

    await user.click(screen.getByRole('button', { name: '16:9' }))
    await user.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => {
      expect(api.calls.some((call) => call.method === 'PUT')).toBe(true)
    })
    const saved = api.calls.find((call) => call.method === 'PUT')?.body as {
      composition: CompositionV1
    }
    expect(saved.composition.canvas).toEqual({
      width: 1920,
      height: 1080,
      background: '#000000',
    })
    expect(saved.composition.sourceAssetId).toBe(composition().sourceAssetId)
  })

  test('editing a caption word and undoing it leaves the original text', async () => {
    const user = userEvent.setup()
    await openEditor()

    const word = screen.getByDisplayValue('cara')
    await user.clear(word)
    await user.type(word, 'karya')
    expect(await screen.findByDisplayValue('karya')).toBeVisible()

    await user.click(screen.getByRole('button', { name: /undo/i }))

    expect(await screen.findByDisplayValue('cara')).toBeVisible()
  })

  test('keyboard shortcuts drive the editor but leave text fields alone', async () => {
    const user = userEvent.setup()
    await openEditor()

    await user.click(screen.getByRole('button', { name: /^16:9$/ }))
    await user.keyboard('{Meta>}z{/Meta}')

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^16:9$/ })).toHaveAttribute(
        'aria-pressed',
        'false',
      )
    })

    const timeline = screen.getByRole('region', { name: /timeline/i })
    fireEvent.change(screen.getByLabelText(/scrub the clip/i), { target: { value: '12000' } })
    const word = screen.getByDisplayValue('cara')
    await user.click(word)
    await user.clear(word)
    await user.type(word, 'sesi')

    expect(screen.getByDisplayValue('sesi')).toBeVisible()
    expect(within(timeline).getAllByRole('button', { name: /^select scene/i })).toHaveLength(1)
    expect(screen.getByRole('button', { name: /^16:9$/ })).toHaveAttribute('aria-pressed', 'false')
  })

  test('a conflicting save offers the member both documents', async () => {
    const user = userEvent.setup()
    api.set(SAVE_EDIT, {
      status: 409,
      body: {
        error: {
          code: 'EDIT_REVISION_CONFLICT',
          message: 'This clip changed since you opened it.',
          requestId: 'request-1234',
        },
      },
      headers: { 'X-Clipah-Current-Revision': '4' },
    })
    await openEditor()

    await user.click(screen.getByRole('button', { name: '1:1' }))
    await user.click(screen.getByRole('button', { name: /^save$/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/changed since you opened it/i)
    expect(screen.getByRole('button', { name: /keep my version/i })).toBeVisible()
    expect(screen.getByRole('button', { name: /take the newer version/i })).toBeVisible()
  })

  test('taking the newer version reloads the edit and clears the conflict', async () => {
    const user = userEvent.setup()
    api.set(SAVE_EDIT, {
      status: 409,
      body: {
        error: {
          code: 'EDIT_REVISION_CONFLICT',
          message: 'This clip changed since you opened it.',
          requestId: 'request-1234',
        },
      },
      headers: { 'X-Clipah-Current-Revision': '4' },
    })
    const theirs = composition({ durationMs: 18_000 })
    const scene = theirs.tracks[0]?.items[0]
    if (scene !== undefined) {
      scene.sourceOutMs = 19_000
    }
    api.set(SHOW_EDIT, { body: { ...edit(), currentRevision: 4, composition: theirs } })
    await openEditor()

    await user.click(screen.getByRole('button', { name: '1:1' }))
    await user.click(screen.getByRole('button', { name: /^save$/i }))
    await screen.findByRole('alert')
    await user.click(screen.getByRole('button', { name: /take the newer version/i }))

    await waitFor(() => {
      expect(screen.queryByRole('alert')).toBeNull()
    })
    expect(screen.getByLabelText(/clip ends at/i)).toHaveValue(19_000)
  })

  test('opening the editor from a clip creates one Edit and goes to it', async () => {
    const user = userEvent.setup()
    const created = `POST /api/v1/projects/${PROJECT_ID}/candidates/${candidate().id}/edits`
    api.set(created, { status: 201, body: edit() })
    renderWithApi(
      <WorkspaceProvider>
        <ClipCard candidate={candidate()} />
      </WorkspaceProvider>,
    )

    await user.click(await screen.findByRole('button', { name: /edit this clip/i }))

    await waitFor(() => {
      expect(push).toHaveBeenCalledWith(`/editor/${EDIT_ID}?workspace_id=${WORKSPACE_ID}`)
    })
    expect(api.calls.filter((call) => call.method === 'POST')).toHaveLength(1)
  })

  test('an edit that is not there says so without guessing why', async () => {
    api.set(SHOW_EDIT, { status: 404 })
    renderWithApi(<EditorScreen editId={EDIT_ID} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/not found/i)
    expect(screen.queryByRole('region', { name: /timeline/i })).toBeNull()
  })
})
