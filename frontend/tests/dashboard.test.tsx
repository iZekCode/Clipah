import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import DemoPage from '@/app/demo/page'
import { WorkspaceOverview } from '@/features/workspaces/workspace-overview'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi } from './support/api'
import { currentUser, workspace } from './support/fixtures'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const SUMMARY = 'GET /api/v1/dashboard/summary'

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}))

function summary(overrides: Record<string, unknown> = {}) {
  return {
    workspace: { id: workspace().id, name: 'Rin Creator', role: 'owner' },
    projects: {
      activeCount: 3,
      recent: [
        {
          id: '44444444-4444-4444-8444-444444444444',
          name: 'Episode 12',
          status: 'ready',
          sourceKind: 'upload',
          createdAt: '2026-02-01T00:00:00+00:00',
          updatedAt: '2026-02-02T00:00:00+00:00',
        },
      ],
    },
    jobs: { active: [] },
    usage: [{ resource: 'analyses', consumed: 4, limit: 30 }],
    topCandidates: [
      {
        id: '99999999-9999-4999-8999-999999999999',
        projectId: '44444444-4444-4444-8444-444444444444',
        projectName: 'Episode 12',
        rank: 1,
        score: 0.91,
        hook: 'The surprising opening',
        category: 'insight',
        startMs: 1000,
        endMs: 31000,
      },
    ],
    ...overrides,
  }
}

beforeEach(() => {
  window.sessionStorage.clear()
})

const CLIPS = 'GET /api/v1/clips'

function edited(overrides: Record<string, unknown> = {}) {
  return {
    id: '55555555-5555-4555-8555-555555555551',
    projectId: '44444444-4444-4444-8444-444444444444',
    projectName: 'Episode 12',
    rank: 1,
    score: 0.91,
    hook: 'The surprising opening',
    reason: 'A complete and useful moment',
    category: 'insight',
    startMs: 1000,
    endMs: 31000,
    durationMs: 30000,
    stage: 'edited',
    editId: '66666666-6666-4666-8666-666666666661',
    currentRevision: 3,
    exportCount: 0,
    createdAt: '2026-02-01T00:00:00+00:00',
    editUpdatedAt: '2026-02-03T09:30:00+00:00',
    ...overrides,
  }
}

function signedIn(summaryBody = summary(), clips: unknown[] = []) {
  return stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [SUMMARY]: { body: summaryBody },
    [CLIPS]: { body: { clips, nextCursor: null } },
  })
}

function renderHome() {
  renderWithApi(
    <WorkspaceProvider>
      <WorkspaceOverview />
    </WorkspaceProvider>,
  )
}

describe('Home', () => {
  test('leads with the clip edited most recently and one way back into it', async () => {
    const api = signedIn(summary(), [
      edited(),
      edited({
        id: '55555555-5555-4555-8555-555555555552',
        hook: 'Second cut',
        editId: '66666666-6666-4666-8666-666666666662',
      }),
    ])
    renderHome()

    const hero = await screen.findByRole('region', { name: 'Continue editing' })
    expect(within(hero).getByText('The surprising opening')).toBeInTheDocument()
    expect(within(hero).getByText(/revision 3/i)).toBeInTheDocument()
    expect(within(hero).getByRole('link', { name: 'Continue editing' })).toHaveAttribute(
      'href',
      '/editor/66666666-6666-4666-8666-666666666661',
    )
    expect(
      within(screen.getByRole('list', { name: 'More clips in editing' })).getByRole('link', {
        name: 'Second cut',
      }),
    ).toHaveAttribute('href', '/editor/66666666-6666-4666-8666-666666666662')
    const read = api.calls.find((call) => call.path === '/api/v1/clips')
    expect(read?.params.get('stage')).toBe('edited')
    expect(read?.params.get('order')).toBe('recent')
  })

  test('shows what is processing on the real stage bar', async () => {
    signedIn(
      summary({
        jobs: {
          active: [
            {
              id: 'job-1',
              projectId: '44444444-4444-4444-8444-444444444444',
              kind: 'transcribe',
              status: 'running',
              stage: 'transcribing',
              progress: 0,
              updatedAt: '2026-02-02T00:00:00+00:00',
            },
            {
              id: 'job-2',
              projectId: '44444444-4444-4444-8444-444444444444',
              kind: 'preview_media',
              status: 'running',
              stage: 'preview_media',
              progress: 0,
              updatedAt: '2026-02-02T00:00:01+00:00',
            },
          ],
        },
      }),
    )
    renderHome()

    const processing = await screen.findByRole('list', { name: 'Processing now' })
    // One row: preview work is not a pipeline stage. Each row's StageBar holds its own list.
    expect(processing.children).toHaveLength(1)
    expect(within(processing).getByText('Episode 12')).toBeInTheDocument()
    // The state is a screen-reader suffix in its own span, so read the list's text.
    expect(processing).toHaveTextContent(/transcribing \(in progress\)/i)
  })

  test('offers the strongest moments as a reel of posters', async () => {
    signedIn()
    renderHome()

    const reel = await screen.findByRole('list', { name: 'Ready to review' })
    expect(within(reel).getByRole('link', { name: 'The surprising opening' })).toHaveAttribute(
      'href',
      '/dashboard/projects/44444444-4444-4444-8444-444444444444/review?moment=99999999-9999-4999-8999-999999999999',
    )
  })

  test('lists recent projects with their state', async () => {
    signedIn()
    renderHome()

    const recent = await screen.findByRole('list', { name: /recent projects/i })
    expect(within(recent).getByRole('link', { name: 'Episode 12' })).toHaveAttribute(
      'href',
      '/dashboard/projects/44444444-4444-4444-8444-444444444444',
    )
    expect(within(recent).getByText('Ready to review')).toBeInTheDocument()
  })

  test('an empty workspace is the importer itself', async () => {
    signedIn(summary({ projects: { activeCount: 0, recent: [] }, topCandidates: [] }))
    renderHome()

    expect(
      await screen.findByRole('heading', { name: 'Drop a long video to start' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Choose a video' })).toBeInTheDocument()
    expect(screen.queryByText(/nothing is waiting for review/i)).not.toBeInTheDocument()
  })

  test('leaves detailed budget numbers to Settings', async () => {
    signedIn()
    renderHome()

    await screen.findByRole('list', { name: /recent projects/i })
    expect(screen.queryByText('4 of 30')).not.toBeInTheDocument()
  })

  test('asks the backend only for the Workspace the member is looking at', async () => {
    const api = signedIn()
    renderHome()

    await screen.findByRole('list', { name: /recent projects/i })
    const reads = api.calls.filter((call) => call.path === '/api/v1/dashboard/summary')
    expect(reads).toHaveLength(1)
    expect(reads[0]?.params.get('workspace_id')).toBe(workspace().id)
  })

  test('shows the failure instead of an empty Workspace when the summary cannot be read', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [SUMMARY]: { status: 500 },
    })
    renderHome()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Something went wrong. Please try again.',
    )
  })
})

describe('the public demo', () => {
  test('shows sanitized bundled work without calling a private endpoint', () => {
    const api = stubApi({})

    renderWithApi(<DemoPage />)

    expect(screen.getByRole('heading', { name: /demo/i, level: 1 })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem').length).toBeGreaterThan(0)
    expect(api.calls).toHaveLength(0)
  })
})
