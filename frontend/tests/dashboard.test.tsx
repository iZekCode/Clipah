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

describe('the Workspace overview', () => {
  test('puts starting a project first, then recent work and moments to review', async () => {
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

    const recent = await screen.findByRole('list', { name: /recent projects/i })
    expect(screen.getByRole('button', { name: /new project/i })).toBeInTheDocument()
    expect(within(recent).getByRole('link', { name: 'Episode 12' })).toHaveAttribute(
      'href',
      '/dashboard/projects/44444444-4444-4444-8444-444444444444',
    )
    expect(within(recent).getByText('Ready to review')).toBeInTheDocument()
    expect(screen.getByText('The surprising opening')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /review in episode 12/i })).toHaveAttribute(
      'href',
      '/dashboard/clips/99999999-9999-4999-8999-999999999999',
    )
  })

  test('leaves detailed budget numbers to Settings', async () => {
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

    await screen.findByRole('list', { name: /recent projects/i })
    expect(screen.queryByText('4 of 30')).not.toBeInTheDocument()
  })

  test('invites a first video when the Workspace has no projects', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [SUMMARY]: { body: summary({ projects: { activeCount: 0, recent: [] }, topCandidates: [] }) },
    })

    renderWithApi(
      <WorkspaceProvider>
        <WorkspaceOverview />
      </WorkspaceProvider>,
    )

    expect(await screen.findByText(/start with your first video/i)).toBeInTheDocument()
    expect(screen.getByText(/nothing is waiting for review/i)).toBeInTheDocument()
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
