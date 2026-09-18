import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { ReviewMode } from '@/features/review/ReviewMode'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi } from './support/api'
import { expectAccessible } from './support/axe'
import { candidate, currentUser, project, workspace } from './support/fixtures'

const PROJECT_ID = project().id
const FIRST = candidate({
  startMs: 12_000,
  endMs: 20_000,
  durationMs: 8_000,
  contextWarnings: ['Opens mid-thought.'],
})
const SECOND = candidate({
  id: '55555555-5555-4555-8555-555555555552',
  rank: 2,
  hook: 'Second moment',
  startMs: 30_000,
  endMs: 40_000,
  durationMs: 10_000,
  contextWarnings: [],
})
const push = vi.hoisted(() => vi.fn())

vi.mock('next/navigation', () => ({
  usePathname: () => `/dashboard/projects/${PROJECT_ID}/review`,
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
}))

function reviewApi() {
  return stubApi({
    'GET /api/v1/me': { body: currentUser() },
    'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
    [`GET /api/v1/projects/${PROJECT_ID}/candidates`]: {
      body: { candidates: [SECOND, FIRST], nextCursor: null },
    },
    [`GET /api/v1/projects/${PROJECT_ID}/proxy`]: {
      body: {
        url: 'https://objects.test/proxy.mp4',
        expiresAt: '2026-02-01T00:05:00+00:00',
        contentType: 'video/mp4',
        durationMs: 60_000,
        width: 1280,
        height: 720,
      },
    },
    [`GET /api/v1/projects/${PROJECT_ID}/transcript`]: {
      body: {
        language: 'en',
        durationMs: 60_000,
        words: [
          {
            id: 'w1',
            text: 'Before',
            punctuation: '.',
            startMs: 8_000,
            endMs: 8_500,
            speaker: 'SPEAKER_00',
          },
          {
            id: 'w2',
            text: 'Inside',
            punctuation: '.',
            startMs: 13_000,
            endMs: 13_500,
            speaker: 'SPEAKER_00',
          },
          {
            id: 'w3',
            text: 'After',
            punctuation: '.',
            startMs: 22_000,
            endMs: 22_500,
            speaker: 'SPEAKER_00',
          },
        ],
      },
    },
    [`POST /api/v1/projects/${PROJECT_ID}/candidates/${FIRST.id}/edits`]: {
      status: 201,
      body: { id: 'edit-1' },
    },
  })
}

function renderReview() {
  return renderWithApi(
    <WorkspaceProvider>
      <ReviewMode projectId={PROJECT_ID} />
    </WorkspaceProvider>,
  )
}

beforeEach(() => {
  push.mockReset()
  window.sessionStorage.clear()
  window.history.replaceState(null, '', `/dashboard/projects/${PROJECT_ID}/review`)
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
})

describe('review mode', () => {
  test('opens on the best-ranked moment with its hook, warnings, and transcript in context', async () => {
    reviewApi()
    const { container } = renderReview()

    const detail = await screen.findByRole('region', { name: 'Moment' })
    await expectAccessible(container)
    expect(within(detail).getByRole('heading', { level: 1 })).toHaveTextContent(FIRST.hook)
    expect(within(detail).getByRole('group', { name: /context warnings/i })).toHaveTextContent(
      'Opens mid-thought.',
    )
    const excerpt = await within(detail).findByRole('group', { name: 'Transcript' })
    expect(await within(excerpt).findByText('Before.')).toHaveClass('text-subtle-foreground')
    expect(within(excerpt).getByText('Inside.')).toHaveClass('text-foreground')
    expect(within(excerpt).getByText('After.')).toHaveClass('text-subtle-foreground')
  })

  test('opens the moment the address names', async () => {
    window.history.replaceState(
      null,
      '',
      `/dashboard/projects/${PROJECT_ID}/review?moment=${SECOND.id}`,
    )
    reviewApi()
    renderReview()

    expect(
      await screen.findByRole('heading', { level: 1, name: SECOND.hook }),
    ).toBeInTheDocument()
  })

  test('J and K move between moments and keep the address in step', async () => {
    const user = userEvent.setup()
    reviewApi()
    renderReview()
    await screen.findByRole('heading', { level: 1, name: FIRST.hook })

    await user.keyboard('j')
    expect(
      await screen.findByRole('heading', { level: 1, name: SECOND.hook }),
    ).toBeInTheDocument()
    expect(window.location.search).toBe(`?moment=${SECOND.id}`)

    await user.keyboard('k')
    expect(await screen.findByRole('heading', { level: 1, name: FIRST.hook })).toBeInTheDocument()
  })

  test('plays exactly the moment and stops at its end', async () => {
    reviewApi()
    renderReview()

    const video = (await screen.findByTestId('review-video')) as HTMLVideoElement
    fireEvent.loadedMetadata(video)
    expect(video.currentTime).toBe(12)
    video.currentTime = 20
    fireEvent.timeUpdate(video)

    await waitFor(() => expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled())
    expect(video.currentTime).toBe(12)
  })

  test('E opens the moment in the editor', async () => {
    const user = userEvent.setup()
    const api = reviewApi()
    renderReview()
    await screen.findByRole('heading', { level: 1, name: FIRST.hook })

    await user.keyboard('e')

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(expect.stringMatching(/^\/editor\/edit-1/)),
    )
    expect(api.calls.filter((call) => call.method === 'POST')).toHaveLength(1)
  })

  test('Escape goes back to the project and ? lists every key', async () => {
    const user = userEvent.setup()
    reviewApi()
    renderReview()
    await screen.findByRole('heading', { level: 1, name: FIRST.hook })

    await user.keyboard('?')
    const help = await screen.findByRole('dialog', { name: 'Review shortcuts' })
    for (const key of ['Space', 'J', 'K', 'E', 'Esc']) {
      expect(within(help).getByText(key)).toBeInTheDocument()
    }
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.keyboard('{Escape}')
    expect(push).toHaveBeenCalledWith(`/dashboard/projects/${PROJECT_ID}`)
  })

  test('keys do nothing while a text field has focus', async () => {
    const user = userEvent.setup()
    reviewApi()
    renderReview()
    await screen.findByRole('heading', { level: 1, name: FIRST.hook })
    const field = document.createElement('input')
    document.body.append(field)
    field.focus()

    await user.keyboard('j')

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(FIRST.hook)
    field.remove()
  })

  test('phones get previous and next buttons instead of the list', async () => {
    const user = userEvent.setup()
    reviewApi()
    renderReview()
    await screen.findByRole('heading', { level: 1, name: FIRST.hook })

    await user.click(screen.getByRole('button', { name: 'Next moment' }))

    expect(
      await screen.findByRole('heading', { level: 1, name: SECOND.hook }),
    ).toBeInTheDocument()
  })
})
