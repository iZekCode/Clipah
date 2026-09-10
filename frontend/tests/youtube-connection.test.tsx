/**
 * Connecting a YouTube account with a cookie jar, and what a member is told first.
 *
 * This is the most dangerous thing the product asks anybody to do, so the tests are about
 * the warnings and the refusals rather than about the happy path. The dialog does not
 * exist at all where the server has not switched the capability on; where it does exist,
 * it says plainly what a cookie jar is, what it risks, how long it is kept, and how to
 * end it, and it refuses to send anything until the member has confirmed both that they
 * understand and that the account is theirs.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { YouTubeConnectionDialog } from '@/features/uploads/YouTubeConnectionDialog'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi, type StubbedApi } from './support/api'
import { capabilities, currentUser, workspace } from './support/fixtures'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const CONNECTIONS = 'GET /api/v1/source-connections'
const CREATE = 'POST /api/v1/source-connections'
const REVOKE_PREFIX = '/api/v1/source-connections/'
const CONNECTION_ID = '77777777-7777-4777-8777-777777777771'

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/dashboard/settings/connections',
}))

/** One stored connection as the backend describes it: never its credential. */
function connection(overrides: Record<string, unknown> = {}) {
  return {
    id: CONNECTION_ID,
    provider: 'youtube',
    kind: 'cookie',
    status: 'active',
    label: 'YouTube cookies (7 accepted)',
    domainScope: '.youtube.com,.google.com',
    authorizedByUserId: currentUser().id,
    consentedAt: '2026-03-01T12:00:00+00:00',
    expiresAt: '2026-03-08T12:00:00+00:00',
    revokedAt: null,
    ...overrides,
  }
}

/** Everything the dialog reads, with the capability on unless a test says otherwise. */
function stub(
  {
    enabled = true,
    connections = [],
  }: { enabled?: boolean; connections?: Array<Record<string, unknown>> } = {},
  overrides: Record<string, unknown> = {},
): StubbedApi {
  return stubApi({
    [ME]: {
      body: {
        ...currentUser(),
        capabilities: capabilities({ authenticatedYoutubeImport: enabled }),
      },
    },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [CONNECTIONS]: { body: { connections } },
    [CREATE]: { status: 201, body: connection() },
    ...overrides,
  })
}

/** Render the dialog inside the Workspace scope it reads. */
function open(): void {
  renderWithApi(
    <WorkspaceProvider>
      <YouTubeConnectionDialog />
    </WorkspaceProvider>,
  )
}

/** Hand the file input one cookie jar, the way a member would. */
async function chooseJar(user: ReturnType<typeof userEvent.setup>, contents: string) {
  const file = new File([contents], 'cookies.txt', { type: 'text/plain' })
  await user.upload(screen.getByLabelText(/cookie file/i), file)
}

describe('the YouTube connection dialog', () => {
  test('does not exist where the server has not switched the capability on', async () => {
    stub({ enabled: false })
    open()

    await waitFor(() => {
      expect(screen.queryByRole('region', { name: /youtube account/i })).toBeNull()
    })
    expect(screen.queryByLabelText(/cookie file/i)).toBeNull()
  })

  test('says what a cookie jar is, what it risks, and how it ends', async () => {
    stub()
    open()

    const dialog = await screen.findByRole('region', { name: /youtube account/i })

    expect(within(dialog).getByText(/signed-in session/i)).toBeVisible()
    expect(within(dialog).getByText(/sign out of youtube/i)).toBeVisible()
    expect(within(dialog).getByText(/restrict or ban/i)).toBeVisible()
    expect(within(dialog).getByText(/seven days/i)).toBeVisible()
    expect(within(dialog).getByText(/revoke/i)).toBeVisible()
    expect(within(dialog).getByText(/your own account/i)).toBeVisible()
  })

  test('will not send anything until both confirmations are given', async () => {
    const user = userEvent.setup()
    const api = stub()
    open()
    await screen.findByRole('region', { name: /youtube account/i })

    await chooseJar(user, '# Netscape HTTP Cookie File\n')
    const connect = screen.getByRole('button', { name: /connect account/i })
    expect(connect).toBeDisabled()

    await user.click(screen.getByLabelText(/i understand/i))
    expect(connect).toBeDisabled()

    await user.click(screen.getByLabelText(/my own account/i))
    await waitFor(() => {
      expect(connect).toBeEnabled()
    })
    expect(api.calls.filter((call) => call.method === 'POST')).toHaveLength(0)
  })

  test('sends the jar and both confirmations, and nothing else', async () => {
    const user = userEvent.setup()
    const api = stub()
    // The double-submit token the client echoes on every unsafe method.
    document.cookie = 'clipah_csrf=token-1234'
    open()
    await screen.findByRole('region', { name: /youtube account/i })

    await chooseJar(user, '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1\tSID\tv\n')
    await user.click(screen.getByLabelText(/i understand/i))
    await user.click(screen.getByLabelText(/my own account/i))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /connect account/i })).toBeEnabled()
    })
    await user.click(screen.getByRole('button', { name: /connect account/i }))

    await waitFor(() => {
      expect(api.calls.filter((call) => call.method === 'POST')).toHaveLength(1)
    })
    const sent = api.calls.find((call) => call.method === 'POST')
    const body = sent?.body as Record<string, unknown>
    expect(Object.keys(body).sort()).toEqual([
      'consentAcknowledged',
      'cookiesBase64',
      'ownershipAttested',
    ])
    expect(body.consentAcknowledged).toBe(true)
    expect(body.ownershipAttested).toBe(true)
    expect(atob(body.cookiesBase64 as string)).toContain('.youtube.com')
    expect(sent?.headers.get('X-CSRF-Token')).toBe('token-1234')
  })

  test('shows a stored connection by its metadata and never by its contents', async () => {
    stub({ connections: [connection()] })
    open()

    const dialog = await screen.findByRole('region', { name: /youtube account/i })

    expect(await within(dialog).findByText(/YouTube cookies \(7 accepted\)/)).toBeVisible()
    expect(within(dialog).getByText('.youtube.com,.google.com')).toBeVisible()
    expect(within(dialog).getByText(/expires/i)).toBeVisible()
    expect(dialog.textContent).not.toContain('SID')
  })

  test('revokes a connection and says it is finished', async () => {
    const user = userEvent.setup()
    const api = stub({ connections: [connection()] })
    api.set(`DELETE ${REVOKE_PREFIX}${CONNECTION_ID}`, () => {
      // The credential is destroyed by the refusal above; the list says so afterwards.
      api.set(CONNECTIONS, { body: { connections: [connection({ status: 'revoked' })] } })
      return { status: 204 }
    })
    open()
    await screen.findByRole('region', { name: /youtube account/i })

    await user.click(await screen.findByRole('button', { name: /^revoke$/i }))

    await waitFor(() => {
      expect(api.calls.some((call) => call.method === 'DELETE')).toBe(true)
    })
    expect(await screen.findByText(/revoked/i)).toBeVisible()
  })

  test('shows the backend refusal for a jar it will not accept', async () => {
    const user = userEvent.setup()
    stub({}, {
      [CREATE]: {
        status: 422,
        body: {
          error: {
            code: 'COOKIE_FILE_INSUFFICIENT',
            message: 'That file does not contain a YouTube sign-in.',
            requestId: 'request-1234',
          },
        },
      },
    })
    open()
    await screen.findByRole('region', { name: /youtube account/i })

    await chooseJar(user, 'nonsense')
    await user.click(screen.getByLabelText(/i understand/i))
    await user.click(screen.getByLabelText(/my own account/i))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /connect account/i })).toBeEnabled()
    })
    await user.click(screen.getByRole('button', { name: /connect account/i }))

    expect(await screen.findByText(/does not contain a youtube sign-in/i)).toBeVisible()
  })

  test('never offers to read cookies out of a browser profile', async () => {
    stub()
    open()

    const dialog = await screen.findByRole('region', { name: /youtube account/i })

    expect(dialog.textContent).not.toMatch(/cookies-from-browser/i)
    expect(within(dialog).queryByRole('button', { name: /from my browser/i })).toBeNull()
  })
})
