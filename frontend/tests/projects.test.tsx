import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { RequireSession } from '@/features/auth/require-session'
import { ProjectDetail } from '@/features/projects/project-detail'
import { ProjectsPanel } from '@/features/projects/projects-panel'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'
import { WorkspaceSwitcher } from '@/features/workspaces/workspace-switcher'
import { DashboardShell } from '@/components/dashboard-shell'

import { renderWithApi, stubApi, errorBody } from './support/api'
import { currentUser, project, workspace } from './support/fixtures'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const PROJECTS = 'GET /api/v1/projects'
const pathname = vi.hoisted(() => ({ current: '/dashboard' }))

vi.mock('next/navigation', () => ({
  usePathname: () => pathname.current,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}))

beforeEach(() => {
  pathname.current = '/dashboard'
  window.sessionStorage.clear()
})

describe('the signed-in shell', () => {
  test('shows the sign-in invitation instead of Workspace data when nobody is signed in', async () => {
    stubApi({ [ME]: { status: 401 } })

    renderWithApi(
      <RequireSession>
        <p>Private Workspace data</p>
      </RequireSession>,
    )

    expect(await screen.findByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/signin')
    expect(screen.queryByText('Private Workspace data')).not.toBeInTheDocument()
  })

  test('says it is still checking the session before deciding anything', () => {
    stubApi({ [ME]: { body: currentUser() } })

    renderWithApi(
      <RequireSession>
        <p>Private Workspace data</p>
      </RequireSession>,
    )

    expect(screen.getByRole('status')).toHaveTextContent(/checking/i)
  })

  test('renders the Workspace once the session and its memberships arrive', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
    })

    renderWithApi(
      <RequireSession>
        <WorkspaceProvider>
          <p>Private Workspace data</p>
        </WorkspaceProvider>
      </RequireSession>,
    )

    expect(await screen.findByText('Private Workspace data')).toBeInTheDocument()
  })

  test('marks the navigation entry for the route being viewed', () => {
    pathname.current = '/dashboard/projects'

    renderWithApi(
      <DashboardShell user={currentUser()} workspaceSwitcher={null} jobCenter={null}>
        <p>Body</p>
      </DashboardShell>,
    )

    expect(screen.getByRole('link', { name: 'Projects' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current')
  })

  test('keeps its navigation reachable on a narrow screen behind one labelled control', async () => {
    const user = userEvent.setup()
    renderWithApi(
      <DashboardShell user={currentUser()} workspaceSwitcher={null} jobCenter={null}>
        <p>Body</p>
      </DashboardShell>,
    )

    const toggle = screen.getByRole('button', { name: /navigation/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    await user.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('navigation', { name: /workspace/i })).toBeVisible()
  })
})

describe('the Workspace switcher', () => {
  test('starts in the personal Workspace when the User has just been bootstrapped', async () => {
    const personal = workspace({ name: 'Rin Creator', kind: 'personal' })
    const team = workspace({
      id: '55555555-5555-4555-8555-555555555555',
      name: 'Studio Nine',
      kind: 'team',
      role: 'editor',
    })
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [team, personal] } },
      [PROJECTS]: { body: { projects: [], nextCursor: null } },
    })

    renderWithApi(
      <WorkspaceProvider>
        <WorkspaceSwitcher />
      </WorkspaceProvider>,
    )

    expect(await screen.findByRole('combobox', { name: /workspace/i })).toHaveValue(personal.id)
  })

  test('scopes every following read to the Workspace the member switched to', async () => {
    const user = userEvent.setup()
    const personal = workspace()
    const team = workspace({
      id: '55555555-5555-4555-8555-555555555555',
      name: 'Studio Nine',
      kind: 'team',
      role: 'editor',
    })
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [personal, team] } },
      [PROJECTS]: { body: { projects: [], nextCursor: null } },
    })

    renderWithApi(
      <WorkspaceProvider>
        <WorkspaceSwitcher />
        <ProjectsPanel />
      </WorkspaceProvider>,
    )

    await screen.findByRole('combobox', { name: /workspace/i })
    await user.selectOptions(screen.getByRole('combobox', { name: /workspace/i }), team.id)

    await waitFor(() => {
      const reads = api.calls.filter((call) => call.path === '/api/v1/projects')
      expect(reads.at(-1)?.params.get('workspace_id')).toBe(team.id)
    })
  })
})

describe('the project list', () => {
  test('invites the first project when the Workspace has none', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECTS]: { body: { projects: [], nextCursor: null } },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ProjectsPanel />
      </WorkspaceProvider>,
    )

    expect(await screen.findByText(/no projects yet/i)).toBeInTheDocument()
  })

  test('names every project and the processing state it is actually in', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECTS]: {
        body: {
          projects: [
            project({ name: 'Episode 12', status: 'transcribing' }),
            project({
              id: '66666666-6666-4666-8666-666666666666',
              name: 'Episode 13',
              status: 'ready',
            }),
          ],
          nextCursor: null,
        },
      },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ProjectsPanel />
      </WorkspaceProvider>,
    )

    const list = await screen.findByRole('list', { name: /projects/i })
    expect(within(list).getByRole('link', { name: 'Episode 12' })).toHaveAttribute(
      'href',
      `/dashboard/projects/${project().id}`,
    )
    expect(within(list).getByText('Transcribing')).toBeInTheDocument()
    expect(within(list).getByText('Ready to review')).toBeInTheDocument()
  })

  test('asks the backend for the next page instead of holding the whole list', async () => {
    const user = userEvent.setup()
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECTS]: ({ params }) =>
        params.get('cursor') === null
          ? { body: { projects: [project()], nextCursor: 'cursor-2' } }
          : {
              body: {
                projects: [
                  project({ id: '77777777-7777-4777-8777-777777777777', name: 'Episode 14' }),
                ],
                nextCursor: null,
              },
            },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ProjectsPanel />
      </WorkspaceProvider>,
    )

    await user.click(await screen.findByRole('button', { name: /load more/i }))

    expect(await screen.findByRole('link', { name: 'Episode 14' })).toBeInTheDocument()
    expect(api.calls.some((call) => call.params.get('cursor') === 'cursor-2')).toBe(true)
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })

  test('shows the backend message and its request identifier when the read fails', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECTS]: { status: 500 },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ProjectsPanel />
      </WorkspaceProvider>,
    )

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Something went wrong. Please try again.')
    expect(alert).toHaveTextContent('request-1234')
  })

  test('renames a project in place before the backend has answered', async () => {
    const user = userEvent.setup()
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECTS]: { body: { projects: [project()], nextCursor: null } },
      [`PATCH /api/v1/projects/${project().id}`]: { body: project({ name: 'Episode 12 final' }) },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ProjectsPanel />
      </WorkspaceProvider>,
    )

    await user.click(await screen.findByRole('button', { name: /rename episode 12/i }))
    const field = screen.getByRole('textbox', { name: /project name/i })
    await user.clear(field)
    await user.type(field, 'Episode 12 final')
    await user.click(screen.getByRole('button', { name: /save/i }))

    expect(await screen.findByRole('link', { name: 'Episode 12 final' })).toBeInTheDocument()
    expect(
      api.calls.some(
        (call) =>
          call.method === 'PATCH' && (call.body as { name?: string }).name === 'Episode 12 final',
      ),
    ).toBe(true)
  })

  test('puts a deleted project back within its recovery window', async () => {
    const user = userEvent.setup()
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECTS]: { body: { projects: [project()], nextCursor: null } },
      [`DELETE /api/v1/projects/${project().id}`]: { status: 204 },
      [`POST /api/v1/projects/${project().id}/restore`]: { body: project() },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ProjectsPanel />
      </WorkspaceProvider>,
    )

    await user.click(await screen.findByRole('button', { name: /delete episode 12/i }))
    await user.click(await screen.findByRole('button', { name: /restore episode 12/i }))

    await waitFor(() => {
      expect(
        api.calls.some((call) => call.path === `/api/v1/projects/${project().id}/restore`),
      ).toBe(true)
    })
  })

  test('offers no writing controls to a member who may only read', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace({ role: 'viewer' })] } },
      [PROJECTS]: { body: { projects: [project()], nextCursor: null } },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ProjectsPanel />
      </WorkspaceProvider>,
    )

    expect(await screen.findByRole('link', { name: 'Episode 12' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /rename/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /create project/i })).not.toBeInTheDocument()
  })
})

describe('one project', () => {
  test("answers another Workspace's project exactly like a project that never existed", async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [`GET /api/v1/projects/${project().id}`]: { status: 404, body: errorBody(404) },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ProjectDetail projectId={project().id} />
      </WorkspaceProvider>,
    )

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('That resource was not found.')
    expect(alert).not.toHaveTextContent(/permission|member|workspace/i)
  })
})

describe('starting a project', () => {
  test('the create request carries an idempotency key', async () => {
    // The backend requires one on this route, so a browser that omits it cannot create a
    // Project at all — a refusal no stubbed test would ever see.
    const user = userEvent.setup()
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECTS]: { body: { projects: [], nextCursor: null } },
      'POST /api/v1/projects': { status: 201, body: project({ name: 'Episode 12' }) },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ProjectsPanel />
      </WorkspaceProvider>,
    )
    await user.click(await screen.findByRole('button', { name: /create project/i }))
    await user.type(screen.getByRole('textbox', { name: /new project name/i }), 'Episode 12')
    await user.click(screen.getByRole('button', { name: /start project/i }))

    await waitFor(() => {
      expect(api.calls.some((call) => call.method === 'POST')).toBe(true)
    })
    const created = api.calls.find((call) => call.method === 'POST')
    expect(created?.headers.get('Idempotency-Key')).toMatch(/.+/)
  })

  test('two separate submissions are two different pieces of work', async () => {
    const user = userEvent.setup()
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECTS]: { body: { projects: [], nextCursor: null } },
      'POST /api/v1/projects': { status: 201, body: project() },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ProjectsPanel />
      </WorkspaceProvider>,
    )
    for (const name of ['Episode 12', 'Episode 13']) {
      await user.click(await screen.findByRole('button', { name: /create project/i }))
      await user.type(screen.getByRole('textbox', { name: /new project name/i }), name)
      await user.click(screen.getByRole('button', { name: /start project/i }))
      await waitFor(() => {
        expect(api.calls.filter((call) => call.method === 'POST').length).toBeGreaterThan(0)
      })
    }

    const keys = api.calls
      .filter((call) => call.method === 'POST')
      .map((call) => call.headers.get('Idempotency-Key'))
    expect(keys).toHaveLength(2)
    expect(new Set(keys).size).toBe(2)
  })
})
