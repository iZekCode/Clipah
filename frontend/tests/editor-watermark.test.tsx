/**
 * The clip's watermark in the editor: drawn over the preview where the export burns it,
 * and chosen from Clipah's mark, a Project picture, or a line of text.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { EditorScreen } from '@/features/editor/EditorScreen'
import { CLIPAH_LOGO_URL, WatermarkMark } from '@/features/editor/Player'
import { editorReducer, initialEditorState } from '@/features/editor/store'
import { CLIPAH_WATERMARK } from '@/features/editor/WatermarkPanel'
import type { CompositionV1 } from '@/lib/api/generated/model'

import { renderWithApi, stubApi, type StubbedApi } from './support/api'
import { composition, currentUser, edit, project, workspace } from './support/fixtures'

const EDIT_ID = edit().id
const PROJECT_ID = project().id
const LOGO_ID = '99999999-0000-4000-8000-00000000000a'
const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const SHOW_EDIT = `GET /api/v1/edits/${EDIT_ID}`
const SAVE_EDIT = `PUT /api/v1/edits/${EDIT_ID}`
const PROXY = `GET /api/v1/projects/${PROJECT_ID}/proxy`
const ASSETS = `GET /api/v1/projects/${PROJECT_ID}/assets`
const UPLOAD = `POST /api/v1/projects/${PROJECT_ID}/pictures`
const UPLOADED = {
  id: '99999999-0000-4000-8000-00000000000b',
  kind: 'picture',
  contentType: 'image/png',
  sizeBytes: 1_024,
  durationMs: null,
  width: 800,
  height: 300,
  createdAt: '2026-02-01T00:01:00+00:00',
}

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/editor',
}))

describe('the watermark drawn over the preview', () => {
  test("Clipah's mark sits in its cell at its share of the frame width", () => {
    render(<WatermarkMark watermark={CLIPAH_WATERMARK} media={{}} />)

    const mark = screen.getByTestId('editor-watermark')
    expect(mark).toHaveAttribute('src', CLIPAH_LOGO_URL)
    expect(mark.style.width).toBe('calc(20cqw)')
    expect(mark.style.right).toBe('calc(4cqw)')
    expect(mark.style.bottom).toBe('calc(4cqw)')
  })

  test('a Project picture waits for its signed link, then draws', () => {
    const mark = { ...CLIPAH_WATERMARK, kind: 'image' as const, assetId: LOGO_ID }
    const { rerender } = render(<WatermarkMark watermark={mark} media={{}} />)
    expect(screen.queryByTestId('editor-watermark')).toBeNull()

    rerender(<WatermarkMark watermark={mark} media={{ [LOGO_ID]: 'https://storage.test/logo.png' }} />)
    expect(screen.getByTestId('editor-watermark')).toHaveAttribute(
      'src',
      'https://storage.test/logo.png',
    )
  })

  test('text is drawn as text, never as markup', () => {
    const text = '<b>@clipah</b>'
    render(
      <WatermarkMark
        watermark={{ ...CLIPAH_WATERMARK, kind: 'text', text, position: 'center' }}
        media={{}}
      />,
    )

    expect(screen.getByTestId('editor-watermark')).toHaveTextContent(text)
    expect(document.querySelector('b')).toBeNull()
  })

  test('removing the mark is one undoable action', () => {
    const start = initialEditorState(composition({ watermark: CLIPAH_WATERMARK }))
    const removed = editorReducer(start, { type: 'watermark', watermark: null })

    expect(removed.composition.watermark).toBeNull()
    expect(editorReducer(removed, { type: 'undo' }).composition.watermark).toEqual(
      CLIPAH_WATERMARK,
    )
  })
})

describe('the watermark panel', () => {
  let api: StubbedApi

  /** Open the editor on the Watermark tool, over a Project holding one picture unless told. */
  async function openWatermark(document: CompositionV1, pictures = true): Promise<void> {
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
      [UPLOAD]: { status: 201, body: UPLOADED },
      [`GET /api/v1/assets/${UPLOADED.id}/preview-url`]: {
        body: { url: 'https://storage.test/mine.png', expiresAt: '2026-02-01T00:05:00+00:00' },
      },
      [ASSETS]: {
        body: {
          assets: pictures ? [
            {
              id: LOGO_ID,
              kind: 'broll',
              contentType: 'image/png',
              sizeBytes: 2_048,
              durationMs: null,
              width: 512,
              height: 512,
              createdAt: '2026-02-01T00:00:00+00:00',
            },
          ] : [],
        },
      },
      [`GET /api/v1/assets/${LOGO_ID}/preview-url`]: {
        body: { url: 'https://storage.test/logo.png', expiresAt: '2026-02-01T00:05:00+00:00' },
      },
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
    await userEvent.click(screen.getByRole('tab', { name: /watermark/i }))
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

  test('a Project logo moves to the top left and is saved with the clip', async () => {
    const user = userEvent.setup()
    await openWatermark(composition({ watermark: CLIPAH_WATERMARK }))
    const panel = screen.getByRole('region', { name: 'Watermark' })

    await user.click(within(within(panel).getByRole('group', { name: 'Watermark kind' })).getByRole('button', { name: 'Image' }))
    await user.click(within(panel).getByRole('radio', { name: 'Top left' }))

    const document = await saved((saving) => saving.watermark?.position === 'topLeft')
    expect(document.watermark).toMatchObject({ kind: 'image', assetId: LOGO_ID, text: null })
    expect(await screen.findByTestId('editor-watermark')).toHaveAttribute(
      'src',
      'https://storage.test/logo.png',
    )
  })

  test('a Project without pictures asks for one, and the upload becomes the mark', async () => {
    const user = userEvent.setup()
    await openWatermark(composition({ watermark: CLIPAH_WATERMARK }), false)
    const panel = screen.getByRole('region', { name: 'Watermark' })
    const picker = within(panel).getByLabelText('Upload a watermark picture') as HTMLInputElement
    const opened = vi.spyOn(picker, 'click')

    await user.click(within(within(panel).getByRole('group', { name: 'Watermark kind' })).getByRole('button', { name: 'Image' }))
    expect(opened).toHaveBeenCalled()
    await user.upload(picker, new File([new Uint8Array([137, 80, 78, 71])], 'logo.png', { type: 'image/png' }))

    const document = await saved((saving) => saving.watermark?.assetId === UPLOADED.id)
    expect(document.watermark).toMatchObject({ kind: 'image', size: 0.16, text: null })
    const upload = api.calls.find((call) => call.method === 'POST' && call.path.endsWith('/pictures'))
    expect(upload).toBeDefined()
    expect(await screen.findByTestId('editor-watermark')).toHaveAttribute(
      'src',
      'https://storage.test/mine.png',
    )
  })

  test('a line of text replaces the logo', async () => {
    const user = userEvent.setup()
    await openWatermark(composition({ watermark: CLIPAH_WATERMARK }))
    const panel = screen.getByRole('region', { name: 'Watermark' })

    await user.click(within(within(panel).getByRole('group', { name: 'Watermark kind' })).getByRole('button', { name: 'Text' }))
    const field = within(panel).getByRole('textbox', { name: 'Watermark text' })
    await user.clear(field)
    await user.type(field, '@studio')

    const document = await saved((saving) => saving.watermark?.text === '@studio')
    expect(document.watermark).toMatchObject({ kind: 'text', assetId: null })
  })

  test('switching the mark off exports the clip without one', async () => {
    const user = userEvent.setup()
    await openWatermark(composition({ watermark: CLIPAH_WATERMARK }))

    await user.click(screen.getByRole('switch', { name: 'Show a watermark' }))

    const document = await saved((saving) => saving.watermark === null)
    expect(document.watermark).toBeNull()
    expect(screen.queryByTestId('editor-watermark')).toBeNull()
  })
})
