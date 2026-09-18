import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { MediaCard } from '@/components/media-card'
import { Poster } from '@/components/media/poster'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi } from './support/api'
import { currentUser, workspace } from './support/fixtures'

const PROJECT_ID = '44444444-4444-4444-8444-444444444444'
const STORYBOARD = `GET /api/v1/projects/${PROJECT_ID}/storyboard`
const THUMBNAIL = `GET /api/v1/projects/${PROJECT_ID}/thumbnail`

const manifest = {
  version: 1,
  intervalMs: 2_000,
  tileWidth: 160,
  tileHeight: 90,
  columns: 10,
  rows: 10,
  durationMs: 205_000,
  expiresAt: '2026-09-17T00:05:00+00:00',
  sheets: [{ index: 0, startMs: 0, tileCount: 100, url: 'https://media.test/sheet-0.jpg' }],
}

const BOUNDS = {
  left: 0,
  width: 200,
  top: 0,
  height: 356,
  right: 200,
  bottom: 356,
  x: 0,
  y: 0,
  toJSON: () => ({}),
}

/** jsdom has no `PointerEvent`; a mouse event of the pointer type carries the `clientX` read. */
function pointAt(element: Element, clientX: number): void {
  fireEvent(element, new MouseEvent('pointermove', { bubbles: true, clientX }))
}

function signedIn(extra: Record<string, unknown> = {}) {
  return stubApi({
    'GET /api/v1/me': { body: currentUser() },
    'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
    ...(extra as Record<string, { body?: unknown; status?: number }>),
  })
}

function renderPoster(props: Partial<Parameters<typeof Poster>[0]> = {}) {
  return renderWithApi(
    <WorkspaceProvider>
      <div style={{ width: 200 }}>
        <Poster
          projectId={PROJECT_ID}
          startMs={10_000}
          endMs={40_000}
          aspect="portrait"
          {...props}
        />
      </div>
    </WorkspaceProvider>,
  )
}

beforeEach(() => {
  window.sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Poster', () => {
  test('draws the frame one second into the clip from the storyboard sheet', async () => {
    signedIn({ [STORYBOARD]: { body: manifest } })
    renderPoster()

    const sprite = await screen.findByTestId('sprite')

    expect(sprite.style.backgroundImage).toBe('url("https://media.test/sheet-0.jpg")')
    expect(sprite.style.backgroundPosition).toBe('55.5556% 0%')
  })

  test('scrubs across the clip on hover without asking for anything new', async () => {
    const api = signedIn({ [STORYBOARD]: { body: manifest } })
    renderPoster()
    const sprite = await screen.findByTestId('sprite')
    const poster = screen.getByTestId('poster')
    vi.spyOn(poster, 'getBoundingClientRect').mockReturnValue(BOUNDS)

    pointAt(poster, 200)

    expect(sprite.style.backgroundPosition).toBe('0% 22.2222%')
    fireEvent.pointerLeave(poster)
    expect(sprite.style.backgroundPosition).toBe('55.5556% 0%')
    expect(api.calls.filter((call) => call.path.endsWith('/storyboard'))).toHaveLength(1)
  })

  test('holds still for a member who asked for reduced motion', async () => {
    vi.stubGlobal('matchMedia', () => ({
      matches: true,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }))
    signedIn({ [STORYBOARD]: { body: manifest } })
    renderPoster()
    const sprite = await screen.findByTestId('sprite')
    const poster = screen.getByTestId('poster')
    vi.spyOn(poster, 'getBoundingClientRect').mockReturnValue(BOUNDS)

    pointAt(poster, 200)

    expect(sprite.style.backgroundPosition).toBe('55.5556% 0%')
  })

  test('without a storyboard it shows the project thumbnail', async () => {
    signedIn({
      [THUMBNAIL]: {
        body: {
          url: 'https://media.test/thumb.jpg',
          expiresAt: '2026-09-17T00:05:00+00:00',
          contentType: 'image/jpeg',
        },
      },
    })
    const { container } = renderPoster()

    await waitFor(() =>
      expect(container.querySelector('img')).toHaveAttribute('src', 'https://media.test/thumb.jpg'),
    )
  })

  test('with no media at all it draws a designed frame with the clip range, never an apology', async () => {
    signedIn()
    renderPoster()

    expect(await screen.findByText('0:10 – 0:40')).toBeInTheDocument()
    expect(screen.queryByText(/no preview/i)).not.toBeInTheDocument()
  })

  test('a project still waiting for media asks for nothing', () => {
    const api = signedIn()
    renderPoster({ hasMedia: false })

    expect(api.calls.filter((call) => call.path.includes(PROJECT_ID))).toHaveLength(0)
  })

  test('keyboard focus on its card shows the middle of the clip', async () => {
    signedIn({ [STORYBOARD]: { body: manifest } })
    renderWithApi(
      <WorkspaceProvider>
        <MediaCard
          href="/dashboard/clips/one"
          title="The surprising opening"
          aspect="portrait"
          thumbnail={
            <Poster projectId={PROJECT_ID} startMs={10_000} endMs={40_000} aspect="portrait" />
          }
        />
      </WorkspaceProvider>,
    )
    const sprite = await screen.findByTestId('sprite')

    screen.getByRole('link', { name: 'The surprising opening' }).focus()

    await waitFor(() => expect(sprite.style.backgroundPosition).toBe('22.2222% 11.1111%'))
  })

  test('overlays rank, length, and hook as decoration the card already names', async () => {
    signedIn({ [STORYBOARD]: { body: manifest } })
    renderPoster({ rank: 1, durationMs: 30_000, hook: 'The surprising opening' })

    const poster = await screen.findByTestId('poster')
    expect(poster).toHaveAttribute('aria-hidden', 'true')
    expect(poster).toHaveTextContent('#1')
    expect(poster).toHaveTextContent('0:30')
    expect(poster).toHaveTextContent('The surprising opening')
  })
})
