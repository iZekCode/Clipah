/**
 * Connecting, repairing, and ending a workspace's authority to post as someone.
 *
 * Every connection here is a standing permission to act on a member's behalf, so the
 * page is tested for how honestly it reports one: what it may do, when it stops working,
 * and that ending it is described as the irreversible thing it is.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { SocialConnections } from '@/features/connections/SocialConnections'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'
import type { CapabilitiesResponse, SocialAccountResponse } from '@/lib/api/generated/model'

import { renderWithApi, stubApi, type Handler, type StubbedApi } from './support/api'
import { capabilities, currentUser, socialAccount, workspace } from './support/fixtures'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const ACCOUNTS = `GET /api/v1/workspaces/${workspace().id}/social-accounts`
const CONNECT = `POST /api/v1/workspaces/${workspace().id}/social-accounts/youtube/connect`

const YOUTUBE = socialAccount()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/dashboard/settings/connections',
}))

/** Every publishing gate open unless a test closes one. */
function gates(overrides: Partial<CapabilitiesResponse> = {}): CapabilitiesResponse {
  return capabilities({
    socialPublishing: true,
    youtubePublishing: true,
    instagramPublishing: true,
    tiktokPublishing: true,
    ...overrides,
  })
}

/** Stub the reads and writes the connections page performs. */
function stub(
  {
    accounts = [YOUTUBE],
    user = currentUser({ capabilities: gates() }),
    role = 'owner' as const,
  }: {
    accounts?: SocialAccountResponse[]
    user?: ReturnType<typeof currentUser>
    role?: 'owner' | 'admin' | 'editor' | 'viewer'
  } = {},
  overrides: Record<string, Handler> = {},
): StubbedApi {
  return stubApi({
    [ME]: { body: user },
    [WORKSPACES]: { body: { workspaces: [workspace({ role })] } },
    [ACCOUNTS]: { body: { socialAccounts: accounts } },
    [CONNECT]: { body: { authorizationUrl: 'https://accounts.google.test/o/oauth2/v2/auth?x=1' } },
    ...overrides,
  })
}

/** Render the connections panel inside its Workspace scope. */
function open(): void {
  renderWithApi(
    <WorkspaceProvider>
      <SocialConnections />
    </WorkspaceProvider>,
  )
}

describe('connection health', () => {
  test('a healthy connection names the account, what it may do, and when it expires', async () => {
    stub()

    open()

    const item = await screen.findByRole('group', { name: /Rin on YouTube/ })
    expect(item).toHaveTextContent('YouTube')
    expect(item).toHaveTextContent(/Connected/)
    expect(item).toHaveTextContent(/youtube.upload/)
    expect(item).toHaveTextContent(/10 September 2026/)
  })

  test('an expired access token is reported as needing a refresh, not as broken', async () => {
    stub({
      accounts: [socialAccount({ accessTokenExpiresAt: '2020-01-01T00:00:00+00:00' })],
    })

    open()

    const item = await screen.findByRole('group', { name: /Rin on YouTube/ })
    expect(item).toHaveTextContent(/expired/i)
    expect(within(item).getByRole('button', { name: /Refresh/ })).toBeEnabled()
  })

  test('a connection the provider rejected asks to be reconnected', async () => {
    stub({ accounts: [socialAccount({ connectionStatus: 'reconnect_required' })] })

    open()

    const item = await screen.findByRole('group', { name: /Rin on YouTube/ })
    expect(item).toHaveTextContent(/has to be reconnected/)
    expect(within(item).getByRole('button', { name: /Reconnect/ })).toBeEnabled()
  })

  test('a revoked connection is kept visible and cannot be refreshed', async () => {
    stub({
      accounts: [
        socialAccount({ connectionStatus: 'revoked', revokedAt: '2026-09-09T00:00:00+00:00' }),
      ],
    })

    open()

    const item = await screen.findByRole('group', { name: /Rin on YouTube/ })
    expect(item).toHaveTextContent(/revoked/i)
    expect(within(item).queryByRole('button', { name: /Refresh/ })).not.toBeInTheDocument()
  })

  test('a failed read is reported instead of an empty connections list', async () => {
    stub({}, { [ACCOUNTS]: { status: 500 } })

    open()

    expect(await screen.findByRole('alert')).toHaveTextContent(/Something went wrong/)
  })

  test('a refresh that the provider refuses turns into a reconnect instruction', async () => {
    const user = userEvent.setup()
    stub(
      {},
      {
        [`POST /api/v1/social-accounts/${YOUTUBE.id}/refresh`]: {
          status: 409,
          body: {
            error: {
              code: 'SOCIAL_ACCOUNT_RECONNECT_REQUIRED',
              message: 'That conflicts with the current state.',
              requestId: 'request-1234',
            },
          },
        },
      },
    )

    open()
    const item = await screen.findByRole('group', { name: /Rin on YouTube/ })
    await user.click(within(item).getByRole('button', { name: /Refresh/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/has to be reconnected/)
  })
})

describe('rollout gates and roles', () => {
  test('a provider whose gate is closed cannot be connected', async () => {
    stub({ accounts: [], user: currentUser({ capabilities: gates({ tiktokPublishing: false }) }) })

    open()

    expect(await screen.findByRole('button', { name: /Connect YouTube/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Connect TikTok/ })).not.toBeInTheDocument()
  })

  test('a deployment without social publishing offers no connection at all', async () => {
    stub({ accounts: [], user: currentUser({ capabilities: capabilities() }) })

    open()

    expect(await screen.findByText(/Social publishing is not enabled/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Connect/ })).not.toBeInTheDocument()
  })

  test('a deployment with no provider switched on says that, not that publishing is off', async () => {
    stub({
      accounts: [],
      user: currentUser({ capabilities: capabilities({ socialPublishing: true }) }),
    })

    open()

    expect(
      await screen.findByText(/No publishing provider is switched on/),
    ).toBeInTheDocument()
  })

  test('a member who may not manage connections sees them without controls', async () => {
    stub({ role: 'editor' })

    open()

    expect(await screen.findByRole('group', { name: /Rin on YouTube/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Disconnect/ })).not.toBeInTheDocument()
    expect(screen.getByText(/Only an owner or admin can change connections/)).toBeInTheDocument()
  })

  test('connecting needs a fresh sign-in before the ceremony may start', async () => {
    stub({ user: currentUser({ capabilities: gates(), recentAuthentication: false }) })

    open()

    expect(await screen.findByText(/Sign in again to change connections/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Connect YouTube/ })).not.toBeInTheDocument()
  })
})

describe('starting and ending a connection', () => {
  test('connecting sends the member to the provider the button names', async () => {
    const user = userEvent.setup()
    const assign = vi.fn()
    vi.stubGlobal('location', { assign, href: 'https://clipah.test/dashboard' })
    const api = stub({ accounts: [] })

    open()
    await user.click(await screen.findByRole('button', { name: /Connect YouTube/ }))

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith('https://accounts.google.test/o/oauth2/v2/auth?x=1'),
    )
    expect(api.calls.filter((call) => call.path.endsWith('/connect'))).toHaveLength(1)
  })

  test('disconnecting says what it ends and what it leaves behind before it happens', async () => {
    const user = userEvent.setup()
    stub()

    open()
    const item = await screen.findByRole('group', { name: /Rin on YouTube/ })
    await user.click(within(item).getByRole('button', { name: /Disconnect/ }))

    const dialog = await screen.findByRole('alertdialog')
    expect(dialog).toHaveTextContent(/cannot be undone/)
    expect(dialog).toHaveTextContent(/scheduled publications to this account will be cancelled/i)
    expect(dialog).toHaveTextContent(/videos already published stay on YouTube/i)
  })

  test('a confirmed disconnect removes the connection from the list', async () => {
    const user = userEvent.setup()
    const api = stub({}, { [`DELETE /api/v1/social-accounts/${YOUTUBE.id}`]: { status: 204 } })

    open()
    const item = await screen.findByRole('group', { name: /Rin on YouTube/ })
    await user.click(within(item).getByRole('button', { name: /Disconnect/ }))
    api.set(ACCOUNTS, { body: { socialAccounts: [] } })
    await user.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', {
        name: /Disconnect this account/,
      }),
    )

    await waitFor(() =>
      expect(screen.queryByRole('group', { name: /Rin on YouTube/ })).not.toBeInTheDocument(),
    )
  })

  test('a dismissed confirmation ends nothing', async () => {
    const user = userEvent.setup()
    const api = stub()

    open()
    const item = await screen.findByRole('group', { name: /Rin on YouTube/ })
    await user.click(within(item).getByRole('button', { name: /Disconnect/ }))
    await user.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', { name: /Keep/ }),
    )

    expect(api.calls.some((call) => call.method === 'DELETE')).toBe(false)
    expect(screen.getByRole('group', { name: /Rin on YouTube/ })).toBeInTheDocument()
  })
})
