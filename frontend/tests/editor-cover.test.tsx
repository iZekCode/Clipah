/**
 * The clip's cover in the editor: its design drawn over the frame, and its picture drawn,
 * shown, and downloaded once the clip is saved.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, test, vi } from 'vitest'

import { CoverOverlay } from '@/features/editor/CoverOverlay'
import { EditorScreen } from '@/features/editor/EditorScreen'
import { editorReducer, initialEditorState, type CompositionCover } from '@/features/editor/store'
import type { CompositionV1, CoverResponse } from '@/lib/api/generated/model'

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
const COVER = `GET /api/v1/edits/${EDIT_ID}/cover`
const DRAW = `POST /api/v1/edits/${EDIT_ID}/cover`

const BOLD: CompositionCover = { atMs: 1_000, preset: 'bold', title: 'Terus titik baliknya' }

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/editor',
}))

afterEach(() => {
  vi.unstubAllGlobals()
})

/** One cover state, as the API answers it. */
function coverState(overrides: Partial<CoverResponse> = {}): CoverResponse {
  return {
    status: 'missing',
    revision: 1,
    jobId: null,
    errorCode: null,
    url: null,
    expiresAt: null,
    ...overrides,
  }
}

describe('the cover drawn over the frame', () => {
  test('each titled design writes the title in its own place', () => {
    const { rerender } = render(<CoverOverlay cover={BOLD} />)
    expect(screen.getByTestId('cover-overlay')).toHaveAttribute('data-preset', 'bold')
    expect(screen.getByTestId('cover-title')).toHaveTextContent('Terus titik baliknya')

    rerender(<CoverOverlay cover={{ ...BOLD, preset: 'topTitle' }} />)
    expect(screen.getByTestId('cover-overlay')).toHaveAttribute('data-preset', 'topTitle')

    rerender(<CoverOverlay cover={{ ...BOLD, preset: 'minimal' }} />)
    expect(screen.queryByTestId('cover-overlay')).toBeNull()
  })

  test('a title is drawn as text, never as markup', () => {
    render(<CoverOverlay cover={{ ...BOLD, preset: 'clean', title: '<b>hook</b>' }} />)

    expect(screen.getByTestId('cover-title')).toHaveTextContent('<b>hook</b>')
    expect(document.querySelector('b')).toBeNull()
  })

  test('removing the cover is one undoable action', () => {
    const start = initialEditorState(composition({ cover: BOLD }))
    const removed = editorReducer(start, { type: 'cover', cover: null })

    expect(removed.composition.cover).toBeNull()
    expect(editorReducer(removed, { type: 'undo' }).composition.cover).toEqual(BOLD)
  })
})

describe('the cover panel', () => {
  let api: StubbedApi

  /** Open the editor on the Cover tool, with the cover picture in one state. */
  async function openCover(document: CompositionV1, picture: CoverResponse): Promise<void> {
    api = stubApi({
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
      [COVER]: (request) => ({
        body: request.params.get('download') === 'true'
          ? coverState({ status: 'ready', url: 'https://storage.test/cover.jpg?download=1' })
          : picture,
      }),
      [DRAW]: { status: 202, body: coverState({ status: 'drawing', jobId: 'job-cover-1' }) },
      [SAVE_EDIT]: (request) => ({
        body: {
          ...edit(),
          currentRevision: 2,
          composition: (request.body as { composition: CompositionV1 }).composition,
        },
      }),
    })
    renderWithApi(<EditorScreen editId={EDIT_ID} />)
    await screen.findByRole('region', { name: /^timeline$/i })
    await userEvent.click(screen.getByRole('tab', { name: /^cover$/i }))
  }

  /** Save, then read back the composition the backend was asked to store. */
  async function saved(until: (document: CompositionV1) => boolean): Promise<CompositionV1> {
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    let document: CompositionV1 | null = null
    await waitFor(() => {
      const last = api.calls.filter((call) => call.method === 'PUT').at(-1)
      document = last === undefined ? null : (last.body as { composition: CompositionV1 }).composition
      expect(document !== null && until(document)).toBe(true)
    })
    return document as unknown as CompositionV1
  }

  test('a design and title chosen here are saved with the clip and shown over the frame', async () => {
    const user = userEvent.setup()
    await openCover(composition({ cover: BOLD }), coverState())
    const panel = screen.getByRole('region', { name: 'Cover' })

    await user.click(within(panel).getByRole('radio', { name: /top title/i }))
    const field = within(panel).getByRole('textbox', { name: 'Cover title' })
    await user.clear(field)
    await user.type(field, 'Bangkit lagi')

    expect(screen.getByTestId('cover-overlay')).toHaveAttribute('data-preset', 'topTitle')
    const document = await saved((saving) => saving.cover?.title === 'Bangkit lagi')
    expect(document.cover).toMatchObject({ preset: 'topTitle', atMs: 1_000 })
  })

  test('the picture waits for a save, then is drawn once asked for', async () => {
    const user = userEvent.setup()
    await openCover(composition({ cover: BOLD }), coverState())
    const panel = screen.getByRole('region', { name: 'Cover' })

    await user.click(within(panel).getByRole('radio', { name: /minimal/i }))
    expect(within(panel).getByRole('button', { name: /create cover picture/i })).toBeDisabled()
    await saved((saving) => saving.cover?.preset === 'minimal')

    await user.click(await within(panel).findByRole('button', { name: /create cover picture/i }))

    expect(await within(panel).findByRole('status')).toHaveTextContent(/drawing/i)
    expect(api.calls.filter((call) => call.method === 'POST' && call.path.endsWith('/cover'))).toHaveLength(1)
  })

  test('a drawn picture is shown and downloaded from a link signed at the click', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    const user = userEvent.setup()
    await openCover(
      composition({ cover: BOLD }),
      coverState({ status: 'ready', url: 'https://storage.test/cover.jpg' }),
    )
    const panel = screen.getByRole('region', { name: 'Cover' })

    expect(await within(panel).findByRole('img', { name: 'Cover picture' })).toHaveAttribute(
      'src',
      'https://storage.test/cover.jpg',
    )
    await user.click(within(panel).getByRole('button', { name: /download cover/i }))

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith('https://storage.test/cover.jpg?download=1'),
    )
  })

  test('a clip without a cover can start one, and a cover can be removed', async () => {
    const user = userEvent.setup()
    await openCover(composition({ cover: null }), coverState({ status: 'none' }))
    const panel = screen.getByRole('region', { name: 'Cover' })

    await user.click(within(panel).getByRole('button', { name: /design a cover/i }))
    expect(await saved((saving) => saving.cover != null)).toMatchObject({
      cover: { preset: 'bold' },
    })

    await user.click(within(panel).getByRole('button', { name: /remove cover/i }))
    expect((await saved((saving) => saving.cover === null)).cover).toBeNull()
  })
})
