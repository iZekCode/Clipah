import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { ProjectDetail } from '@/features/projects/project-detail'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi } from './support/api'
import { expectAccessible } from './support/axe'
import { FakeEventSource } from './support/events'
import { candidate, currentUser, project, workspace } from './support/fixtures'

const PROJECT_ID = project().id
const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const PROJECT = `GET /api/v1/projects/${PROJECT_ID}`
const CANDIDATES = `GET /api/v1/projects/${PROJECT_ID}/candidates`
const TRANSCRIPT = `GET /api/v1/projects/${PROJECT_ID}/transcript`
const PROXY = `GET /api/v1/projects/${PROJECT_ID}/proxy`
const CLIPS = 'GET /api/v1/clips'

vi.mock('next/navigation', () => ({
  usePathname: () => `/dashboard/projects/${PROJECT_ID}`,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

beforeEach(() => {
  window.sessionStorage.clear()
  window.history.replaceState(null, '', `/dashboard/projects/${PROJECT_ID}`)
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})

function readyApi() {
  return stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [PROJECT]: { body: project({ status: 'ready' }) },
    [CANDIDATES]: {
      body: {
        candidates: [
          candidate({ startMs: 1_000, endMs: 3_000, durationMs: 2_000 }),
          candidate({
            id: '55555555-5555-4555-8555-555555555552',
            rank: 2,
            hook: 'Second',
            startMs: 10_000,
            endMs: 12_000,
            durationMs: 2_000,
          }),
        ],
        nextCursor: null,
      },
    },
    [TRANSCRIPT]: {
      body: {
        language: 'en',
        durationMs: 20_000,
        words: [
          {
            id: 'w1',
            text: 'Opening',
            punctuation: '.',
            startMs: 1_000,
            endMs: 1_500,
            speaker: 'SPEAKER_00',
          },
          {
            id: 'w2',
            text: 'Middle',
            punctuation: '.',
            startMs: 5_000,
            endMs: 5_500,
            speaker: 'SPEAKER_00',
          },
          {
            id: 'w3',
            text: 'Second',
            punctuation: '.',
            startMs: 10_500,
            endMs: 11_000,
            speaker: 'SPEAKER_01',
          },
        ],
      },
    },
    [PROXY]: {
      body: {
        url: 'https://objects.test/proxy.mp4',
        expiresAt: '2026-02-01T00:05:00+00:00',
        contentType: 'video/mp4',
        durationMs: 20_000,
        width: 1280,
        height: 720,
      },
    },
    [CLIPS]: { body: { clips: [], nextCursor: null } },
  })
}

function renderProject() {
  return renderWithApi(
    <WorkspaceProvider>
      <ProjectDetail projectId={PROJECT_ID} />
    </WorkspaceProvider>,
  )
}

describe('the Project page', () => {
  test('a new project leads with adding media, with no next-step card', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECT]: { body: project({ status: 'created' }) },
    })
    renderProject()

    expect(await screen.findByRole('button', { name: 'Add media' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: /upload a video/i })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: /next step/i })).not.toBeInTheDocument()
  })

  test('a processing project shows where it is before any live update arrives', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECT]: { body: project({ status: 'ingesting', sourceKind: 'public_url' }) },
      [TRANSCRIPT]: { status: 404 },
      [PROXY]: { status: 404 },
    })
    renderProject()

    const bar = await screen.findByRole('list', { name: 'Processing stages' })
    expect(bar).toHaveTextContent('Importing (done)')
    expect(bar).toHaveTextContent('Preparing video (in progress)')
    // The badge says it too: the bar and the status agree.
    expect(screen.getAllByText('Preparing video').length).toBeGreaterThanOrEqual(2)
  })

  test('an upload that stopped part-way can be resumed from the project', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECT]: { body: project({ status: 'uploading', sourceKind: 'upload' }) },
      [TRANSCRIPT]: { status: 404 },
      [PROXY]: { status: 404 },
    })
    renderProject()

    expect(await screen.findByRole('region', { name: /upload a video/i })).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Processing stages' })).toHaveTextContent(
      'Uploading (in progress)',
    )
  })

  test('a failed project offers to retry the stage that failed', async () => {
    const user = userEvent.setup()
    let status: 'failed' | 'transcribing' = 'failed'
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECT]: () => ({ body: project({ status, sourceKind: 'upload' }) }),
      [TRANSCRIPT]: { status: 404 },
      [PROXY]: { status: 404 },
      [`POST /api/v1/projects/${PROJECT_ID}/retry`]: () => {
        status = 'transcribing'
        return {
          status: 202,
          body: { jobId: '99999999-9999-4999-8999-999999999999', kind: 'transcribe', status: 'queued' },
        }
      },
    })
    renderProject()

    await user.click(await screen.findByRole('button', { name: 'Retry' }))

    await waitFor(() =>
      expect(api.calls.some((call) => call.method === 'POST' && call.path.endsWith('/retry'))).toBe(
        true,
      ),
    )
    // The Project is read again and shows the stage it went back to.
    await waitFor(() =>
      expect(screen.getByRole('list', { name: 'Processing stages' })).toHaveTextContent(
        'Transcribing (in progress)',
      ),
    )
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })

  test('a ready project is its moments beside the source and transcript', async () => {
    readyApi()
    const { container } = renderProject()

    expect(await screen.findByRole('link', { name: 'Review moments' })).toHaveAttribute(
      'href',
      `/dashboard/projects/${PROJECT_ID}/review`,
    )
    expect(await screen.findByRole('list', { name: /ranked clips/i })).toBeInTheDocument()
    await expectAccessible(container)
    const transcript = await screen.findByRole('list', { name: 'Transcript' })
    expect(within(transcript).getAllByRole('listitem').map((line) => line.textContent)).toEqual([
      expect.stringContaining('Opening.'),
      expect.stringContaining('Middle.'),
      expect.stringContaining('Second.'),
    ])
  })

  test('showing a moment in source marks its lines and seeks the player to its start', async () => {
    const user = userEvent.setup()
    readyApi()
    renderProject()

    const list = await screen.findByRole('list', { name: /ranked clips/i })
    await screen.findByRole('list', { name: 'Transcript' })
    const second = within(list)
      .getAllByRole('listitem')
      .find((item) => item.textContent?.includes('Second'))!
    await user.click(within(second).getByRole('button', { name: 'Show in source' }))

    const transcript = screen.getByRole('list', { name: 'Transcript' })
    await waitFor(() =>
      expect(within(transcript).getByText('Second.').closest('li')).toHaveAttribute(
        'aria-current',
        'true',
      ),
    )
    const video = screen.getByTestId('transcript-moment-video') as HTMLVideoElement
    expect(video.currentTime).toBe(10)
  })

  test('marks the line being spoken as the source plays, and moves with it', async () => {
    readyApi()
    renderProject()

    const transcript = await screen.findByRole('list', { name: 'Transcript' })
    await within(transcript).findByText('Middle.')
    const video = screen.getByTestId('transcript-moment-video') as HTMLVideoElement
    const speaking = () => transcript.querySelector('[data-speaking="true"]')?.textContent ?? null

    expect(speaking()).toBeNull()
    video.currentTime = 6
    fireEvent.timeUpdate(video)
    await waitFor(() => expect(speaking()).toContain('Middle.'))
    video.currentTime = 10.6
    fireEvent.timeUpdate(video)
    await waitFor(() => expect(speaking()).toContain('Second.'))
  })

  test('tabs keep Moments, Edits, Exports, and Activity in the address', async () => {
    const user = userEvent.setup()
    readyApi()
    renderProject()

    await user.click(await screen.findByRole('tab', { name: 'Activity' }))

    expect(window.location.search).toBe('?tab=activity')
  })
})
