import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { JobCenter } from '@/features/jobs/job-center'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'
import { WorkspaceSwitcher } from '@/features/workspaces/workspace-switcher'

import { renderWithApi, stubApi } from './support/api'
import { FakeEventSource } from './support/events'
import { currentUser, workspace } from './support/fixtures'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const SUMMARY = 'GET /api/v1/dashboard/summary'
const JOB_ID = '88888888-8888-4888-8888-888888888888'
const PROJECT_ID = '44444444-4444-4444-8444-444444444444'

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}))

function jobEvent(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    jobId: JOB_ID,
    projectId: PROJECT_ID,
    kind: 'ingest',
    status: 'running',
    stage: 'transcribing',
    progress: 0.4,
    errorCode: null,
    attempt: 1,
    sequence: 2,
    ...overrides,
  }
}

function signedInApi(workspaces = [workspace()]) {
  return stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces } },
    [SUMMARY]: {
      body: {
        workspace: { id: workspaces[0]?.id, name: workspaces[0]?.name, role: 'owner' },
        projects: { activeCount: 0, recent: [] },
        jobs: { active: [] },
        usage: [],
        topCandidates: [],
      },
    },
  })
}

beforeEach(() => {
  FakeEventSource.instances = []
  window.sessionStorage.clear()
  vi.stubGlobal('EventSource', FakeEventSource)
})

describe('the global job center', () => {
  test('subscribes once to the Workspace it is showing, carrying the Session cookie', async () => {
    signedInApi()

    renderWithApi(
      <WorkspaceProvider>
        <JobCenter />
      </WorkspaceProvider>,
    )

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const stream = FakeEventSource.instances[0]
    expect(stream?.url).toBe(`/api/v1/jobs/events?workspace_id=${workspace().id}`)
    expect(stream?.withCredentials).toBe(true)
  })

  test('announces running work and deep-links it to the project it belongs to', async () => {
    signedInApi()

    renderWithApi(
      <WorkspaceProvider>
        <JobCenter />
      </WorkspaceProvider>,
    )

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    act(() => FakeEventSource.instances[0]?.emit('progress', jobEvent()))

    expect(await screen.findByText(/transcribing/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open project/i })).toHaveAttribute(
      'href',
      `/dashboard/projects/${PROJECT_ID}`,
    )
  })

  test('keeps a finished job in this Workspace history instead of dropping it', async () => {
    signedInApi()

    renderWithApi(
      <WorkspaceProvider>
        <JobCenter />
      </WorkspaceProvider>,
    )

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    act(() => FakeEventSource.instances[0]?.emit('progress', jobEvent()))
    act(() => FakeEventSource.instances[0]?.emit('succeeded', jobEvent({ status: 'succeeded', progress: 1 })))

    expect(await screen.findByText(/finished/i)).toBeInTheDocument()
  })

  test('survives a move to another page without losing what it already knows', async () => {
    signedInApi()

    const view = renderWithApi(
      <WorkspaceProvider>
        <JobCenter />
        <p>Overview</p>
      </WorkspaceProvider>,
    )

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    act(() => FakeEventSource.instances[0]?.emit('progress', jobEvent()))
    await screen.findByText(/transcribing/i)

    view.rerender(
      <WorkspaceProvider>
        <JobCenter />
        <p>Projects</p>
      </WorkspaceProvider>,
    )

    expect(screen.getByText('Projects')).toBeInTheDocument()
    expect(screen.getByText(/transcribing/i)).toBeInTheDocument()
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  test('discards one Workspace jobs the moment the member switches to another', async () => {
    const user = userEvent.setup()
    const personal = workspace()
    const team = workspace({
      id: '55555555-5555-4555-8555-555555555555',
      name: 'Studio Nine',
      kind: 'team',
      role: 'editor',
    })
    signedInApi([personal, team])

    renderWithApi(
      <WorkspaceProvider>
        <WorkspaceSwitcher />
        <JobCenter />
      </WorkspaceProvider>,
    )

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    act(() => FakeEventSource.instances[0]?.emit('progress', jobEvent()))
    await screen.findByText(/transcribing/i)

    await user.selectOptions(screen.getByRole('combobox', { name: /workspace/i }), team.id)

    await waitFor(() => {
      expect(screen.queryByText(/transcribing/i)).not.toBeInTheDocument()
    })
    expect(FakeEventSource.instances[0]?.closed).toBe(true)
    expect(FakeEventSource.instances[1]?.url).toBe(`/api/v1/jobs/events?workspace_id=${team.id}`)
  })
})
