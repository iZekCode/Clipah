import { screen } from '@testing-library/react'
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

describe('the Workspace overview', () => {
  test('reads its cards from the single backend summary', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [SUMMARY]: { body: summary() },
    })

    renderWithApi(
      <WorkspaceProvider>
        <WorkspaceOverview />
      </WorkspaceProvider>,
    )

    expect(await screen.findByRole('group', { name: /active projects/i })).toHaveTextContent('3')
    expect(screen.getByRole('group', { name: /analyses/i })).toHaveTextContent('4 of 30')
    expect(screen.getByRole('link', { name: 'Episode 12' })).toHaveAttribute(
      'href',
      '/dashboard/projects/44444444-4444-4444-8444-444444444444',
    )
    expect(screen.getByText('The surprising opening')).toBeInTheDocument()
  })

  test('asks the backend only for the Workspace the member is looking at', async () => {
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [SUMMARY]: { body: summary() },
    })

    renderWithApi(
      <WorkspaceProvider>
        <WorkspaceOverview />
      </WorkspaceProvider>,
    )

    await screen.findByRole('group', { name: /active projects/i })
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

    renderWithApi(
      <WorkspaceProvider>
        <WorkspaceOverview />
      </WorkspaceProvider>,
    )

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
