import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { EditorScreen } from '@/features/editor/EditorScreen'
import type { CompositionV1 } from '@/lib/api/generated/model'

import { renderWithApi, stubApi } from './support/api'
import { currentUser, edit, workspace } from './support/fixtures'

const EDIT_ID = edit().id
const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const SHOW_EDIT = `GET /api/v1/edits/${EDIT_ID}`
const SAVE_EDIT = `PUT /api/v1/edits/${EDIT_ID}`
const PROXY = `GET /api/v1/projects/${edit().projectId}/proxy`

vi.mock('next/navigation', () => ({
  usePathname: () => `/editor/${EDIT_ID}`,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}))

beforeEach(() => {
  window.sessionStorage.clear()
  stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [SHOW_EDIT]: { body: edit() },
    [PROXY]: {
      body: {
        url: 'https://storage.test/proxy.mp4',
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

async function openEditor() {
  renderWithApi(<EditorScreen editId={EDIT_ID} />)
  await screen.findByRole('region', { name: /^timeline$/i })
}

describe('the studio layout', () => {
  test('has a tool rail, a stage with its canvas shape and transport, properties, and the lanes', async () => {
    await openEditor()

    expect(
      within(screen.getByRole('navigation', { name: 'Editing tools' }))
        .getAllByRole('tab')
        .map((tab) => tab.textContent),
    ).toEqual(['Captions', 'Style', 'Layout', 'Media', 'Audio', 'Text', 'Review'])
    const stage = screen.getByRole('region', { name: 'Stage' })
    expect(within(stage).getByRole('group', { name: 'Canvas shape' })).toBeInTheDocument()
    expect(within(stage).getByRole('group', { name: 'Transport' })).toBeInTheDocument()
    expect(
      within(screen.getByRole('complementary', { name: 'Properties' })).getByRole('region', {
        name: 'Inspector',
      }),
    ).toBeInTheDocument()
    expect(screen.getByRole('separator', { name: 'Resize the timeline' })).toHaveAttribute(
      'aria-valuenow',
    )
  })

  test('the transport plays, steps, and reads the playhead as a timecode', async () => {
    const user = userEvent.setup()
    await openEditor()
    const transport = screen.getByRole('group', { name: 'Transport' })

    await user.click(within(transport).getByRole('button', { name: 'Next frame' }))
    expect(within(transport).getByText('0:00.03 / 0:30.00')).toBeInTheDocument()
    await user.click(within(transport).getByRole('button', { name: 'Jump to end' }))
    expect(within(transport).getByText('0:30.00 / 0:30.00')).toBeInTheDocument()
    expect(within(transport).getByRole('button', { name: 'Play' })).toBeInTheDocument()
  })

  test('arrow keys step frames and seconds, and M leaves a marker', async () => {
    const user = userEvent.setup()
    await openEditor()
    const transport = screen.getByRole('group', { name: 'Transport' })

    await user.keyboard('{Shift>}{ArrowRight}{/Shift}{ArrowRight}')
    expect(within(transport).getByText(/^0:01\.03 \//)).toBeInTheDocument()

    await user.keyboard('m')
    expect(await screen.findByRole('button', { name: /^0:01 Marker/ })).toBeInTheDocument()
  })

  test('? opens the shortcut sheet', async () => {
    const user = userEvent.setup()
    await openEditor()

    await user.keyboard('?')

    const sheet = await screen.findByRole('dialog', { name: 'Editor shortcuts' })
    for (const key of ['Space', 'S', '⌘Z', '⇧⌘Z', '⌘S', '← →', '⇧← ⇧→', 'M', '+ −']) {
      expect(within(sheet).getByText(key)).toBeInTheDocument()
    }
  })

  test('the timeline dock resizes from the keyboard', async () => {
    await openEditor()
    const handle = screen.getByRole('separator', { name: 'Resize the timeline' })
    const before = Number(handle.getAttribute('aria-valuenow'))

    fireEvent.keyDown(handle, { key: 'ArrowUp' })

    await waitFor(() =>
      expect(Number(handle.getAttribute('aria-valuenow'))).toBe(before + 24),
    )
  })
})
