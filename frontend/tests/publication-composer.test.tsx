/**
 * Choosing where an approved clip goes, and refusing to guess anything on the way.
 *
 * Publishing is the one irreversible thing this product does on someone else's behalf,
 * so the composer is tested for what it refuses: it offers no destination, no privacy
 * value, no disclosure, and no schedule the member did not pick, it names every effect
 * before it happens, and it never sends a second copy of the same submission.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'vitest-axe'
import { describe, expect, test, vi } from 'vitest'

import { PublicationComposer } from '@/features/publishing/PublicationComposer'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'
import type { CapabilitiesResponse, SocialAccountResponse } from '@/lib/api/generated/model'

import { renderWithApi, stubApi, type Handler, type StubbedApi } from './support/api'
import { capabilities, currentUser, publication, socialAccount, workspace } from './support/fixtures'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const ACCOUNTS = `GET /api/v1/workspaces/${workspace().id}/social-accounts`
const EDIT_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1'
const RENDER_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1'
const BATCH_ID = publication().batchId
const PREPARE = `POST /api/v1/edits/${EDIT_ID}/revisions/3/publication-drafts`
const PREFLIGHT = `POST /api/v1/publication-drafts/${BATCH_ID}/preflight`
const CONFIRM = `POST /api/v1/publication-drafts/${BATCH_ID}/confirm`

const YOUTUBE = socialAccount()
const INSTAGRAM = socialAccount({
  id: '88888888-8888-4888-8888-888888888882',
  provider: 'instagram',
  displayName: 'Rin on Instagram',
  externalAccountId: 'ig-1',
  accountType: 'BUSINESS',
})
const TIKTOK = socialAccount({
  id: '88888888-8888-4888-8888-888888888883',
  provider: 'tiktok',
  displayName: 'Rin on TikTok',
  externalAccountId: 'tt-1',
})

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/dashboard/clips/clip-1',
}))

/** Every gate this deployment could open, with only the ones a test names opened. */
function gates(overrides: Partial<CapabilitiesResponse> = {}): CapabilitiesResponse {
  return capabilities({
    socialPublishing: true,
    youtubePublishing: true,
    instagramPublishing: true,
    tiktokPublishing: true,
    ...overrides,
  })
}

/** The capability snapshot each provider reports for one connected account. */
function capabilitySnapshot(account: SocialAccountResponse): Handler {
  if (account.provider === 'tiktok') {
    return {
      body: {
        version: account.capabilityVersion,
        capabilities: {
          creator_username: 'rin',
          privacy_level_options: [
            'PUBLIC_TO_EVERYONE',
            'MUTUAL_FOLLOW_FRIENDS',
            'FOLLOWER_OF_CREATOR',
            'SELF_ONLY',
          ],
          comment_disabled: false,
          duet_disabled: true,
          stitch_disabled: false,
          max_video_post_duration_sec: 600,
        },
      },
    }
  }
  return { body: { version: account.capabilityVersion, capabilities: {} } }
}

/** Stub every read and write the composer performs, with the happy path wired. */
function stub(
  {
    accounts = [YOUTUBE],
    user = currentUser({ capabilities: gates() }),
    statuses = ['scheduled'],
  }: {
    accounts?: SocialAccountResponse[]
    user?: ReturnType<typeof currentUser>
    statuses?: string[]
  } = {},
  overrides: Record<string, Handler> = {},
): StubbedApi {
  const batch = {
    id: BATCH_ID,
    editRevisionId: 'cccccccc-cccc-4ccc-8ccc-ccccccccccc1',
    renderArtifactId: RENDER_ID,
    publications: accounts.map((account, index) =>
      publication({
        id: `99999999-9999-4999-8999-99999999999${index + 1}`,
        socialAccountId: account.id,
        status: statuses[index] ?? statuses[0] ?? 'scheduled',
      }),
    ),
  }
  const snapshots = Object.fromEntries(
    accounts.map((account) => [
      `GET /api/v1/social-accounts/${account.id}/capabilities`,
      capabilitySnapshot(account),
    ]),
  )
  return stubApi({
    [ME]: { body: user },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [ACCOUNTS]: { body: { socialAccounts: accounts } },
    ...snapshots,
    [PREPARE]: { status: 201, body: { ...batch, publications: batch.publications.map((item) => ({ ...item, status: 'draft' })) } },
    [PREFLIGHT]: { body: { ...batch, publications: batch.publications.map((item) => ({ ...item, status: 'awaiting_approval' })) } },
    [CONFIRM]: { body: batch },
    ...overrides,
  })
}

/** Render the composer for one approved revision and its exact rendered artifact. */
function open(): void {
  renderWithApi(
    <WorkspaceProvider>
      <PublicationComposer
        editId={EDIT_ID}
        revision={3}
        renderArtifactId={RENDER_ID}
        renderDigest={'ab12cd34'.repeat(8)}
        durationMs={42_000}
      />
    </WorkspaceProvider>,
  )
}

/** Select one connected destination by the account name a member would look for. */
async function choose(user: ReturnType<typeof userEvent.setup>, name: string): Promise<void> {
  await user.click(await screen.findByRole('checkbox', { name: new RegExp(name) }))
}

/** Fill in every YouTube declaration so a test can reach the confirmation step. */
async function declareYouTube(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  const panel = await screen.findByRole('group', { name: /Rin on YouTube/ })
  await user.type(within(panel).getByLabelText('Title'), 'Approved title')
  await user.click(within(panel).getByRole('radio', { name: 'Private' }))
  await user.click(within(panel).getByRole('radio', { name: /Not made for kids/ }))
  await user.click(within(panel).getByRole('radio', { name: /No synthetic or altered media/ }))
}

describe('publishing gates', () => {
  test('a deployment with social publishing switched off offers no destination at all', async () => {
    stub({ user: currentUser({ capabilities: capabilities() }) })

    open()

    expect(await screen.findByText(/Social publishing is not enabled/)).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  test('only providers whose rollout gate is open can be chosen', async () => {
    stub({
      accounts: [YOUTUBE, INSTAGRAM, TIKTOK],
      user: currentUser({ capabilities: gates({ tiktokPublishing: false }) }),
    })

    open()

    expect(await screen.findByRole('checkbox', { name: /Rin on YouTube/ })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /Rin on Instagram/ })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /Rin on TikTok/ })).not.toBeInTheDocument()
  })

  test('a member whose role may not publish is told so instead of being offered controls', async () => {
    stubApi({
      [ME]: { body: currentUser({ capabilities: gates() }) },
      [WORKSPACES]: { body: { workspaces: [workspace({ role: 'reviewer' })] } },
      [ACCOUNTS]: { body: { socialAccounts: [YOUTUBE] } },
    })

    open()

    expect(await screen.findByText(/Your role may not publish/)).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  test('an editor may not publish where the workspace reserves publishing for owners', async () => {
    stubApi({
      [ME]: { body: currentUser({ capabilities: gates() }) },
      [WORKSPACES]: {
        body: {
          workspaces: [workspace({ role: 'editor', publishingRolePolicy: 'owner_admin' })],
        },
      },
      [ACCOUNTS]: { body: { socialAccounts: [YOUTUBE] } },
    })

    open()

    expect(await screen.findByText(/Your role may not publish/)).toBeInTheDocument()
  })

  test('publishing waits for a fresh sign-in when the session is no longer recent', async () => {
    stub({ user: currentUser({ capabilities: gates(), recentAuthentication: false }) })

    open()

    expect(await screen.findByText(/Sign in again before publishing/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Review and publish/ })).not.toBeInTheDocument()
  })

  test('a second destination is refused until multi-destination scheduling is switched on', async () => {
    const user = userEvent.setup()
    stub({ accounts: [YOUTUBE, INSTAGRAM] })

    open()
    await choose(user, 'Rin on YouTube')
    await choose(user, 'Rin on Instagram')

    expect(
      await screen.findByText(/Publishing to more than one destination at a time is not enabled/),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Review and publish/ })).toBeDisabled()
  })
})

describe('connection health', () => {
  test('an account that must be reconnected cannot be chosen and says why', async () => {
    stub({ accounts: [socialAccount({ connectionStatus: 'reconnect_required' })] })

    open()

    const destination = await screen.findByRole('checkbox', { name: /Rin on YouTube/ })
    expect(destination).toBeDisabled()
    expect(screen.getByText(/This account has to be reconnected/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Reconnect/ })).toHaveAttribute(
      'href',
      '/dashboard/settings/connections',
    )
  })

  test('a revoked account is never offered as a destination', async () => {
    stub({
      accounts: [
        socialAccount({ connectionStatus: 'revoked', revokedAt: '2026-09-09T00:00:00+00:00' }),
      ],
    })

    open()

    expect(await screen.findByText(/access was revoked/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /Rin on YouTube/ })).toBeDisabled()
  })

  test('a failure to read connections is reported instead of an empty destination list', async () => {
    stub({}, { [ACCOUNTS]: { status: 500 } })

    open()

    expect(await screen.findByRole('alert')).toHaveTextContent(/Something went wrong/)
  })
})

describe('per-platform declarations', () => {
  test('nothing is chosen for the member, and publishing waits for every declaration', async () => {
    const user = userEvent.setup()
    stub()

    open()
    await choose(user, 'Rin on YouTube')

    const panel = await screen.findByRole('group', { name: /Rin on YouTube/ })
    for (const radio of within(panel).getAllByRole('radio')) {
      expect(radio).not.toBeChecked()
    }
    expect(screen.getByRole('button', { name: /Review and publish/ })).toBeDisabled()
  })

  test('YouTube may only publish privately until this deployment passes its audit', async () => {
    const user = userEvent.setup()
    stub()

    open()
    await choose(user, 'Rin on YouTube')

    const panel = await screen.findByRole('group', { name: /Rin on YouTube/ })
    expect(within(panel).getByRole('radio', { name: 'Public' })).toBeDisabled()
    expect(within(panel).getByRole('radio', { name: 'Unlisted' })).toBeDisabled()
    expect(within(panel).getByRole('radio', { name: 'Private' })).toBeEnabled()
    expect(within(panel).getByText(/until YouTube completes its audit/)).toBeInTheDocument()
  })

  test('an audited deployment may offer every YouTube visibility', async () => {
    const user = userEvent.setup()
    stub({ user: currentUser({ capabilities: gates({ youtubePublicPrivacy: true }) }) })

    open()
    await choose(user, 'Rin on YouTube')

    const panel = await screen.findByRole('group', { name: /Rin on YouTube/ })
    expect(within(panel).getByRole('radio', { name: 'Public' })).toBeEnabled()
  })

  test('TikTok offers exactly the privacy values this creator currently has', async () => {
    const user = userEvent.setup()
    stub({ accounts: [TIKTOK] })

    open()
    await choose(user, 'Rin on TikTok')

    const panel = await screen.findByRole('group', { name: /Rin on TikTok/ })
    const options = within(panel)
      .getAllByRole('radio', { name: /Everyone|Friends|Followers|Only you/ })
      .map((option) => option.getAttribute('value'))
    expect(options).toEqual([
      'PUBLIC_TO_EVERYONE',
      'MUTUAL_FOLLOW_FRIENDS',
      'FOLLOWER_OF_CREATOR',
      'SELF_ONLY',
    ])
  })

  test('an interaction TikTok has switched off for this creator cannot be switched on', async () => {
    const user = userEvent.setup()
    stub({ accounts: [TIKTOK] })

    open()
    await choose(user, 'Rin on TikTok')

    const panel = await screen.findByRole('group', { name: /Rin on TikTok/ })
    expect(within(panel).getByRole('checkbox', { name: /Duet/ })).toBeDisabled()
    expect(within(panel).getByRole('checkbox', { name: /comments/i })).toBeEnabled()
    expect(within(panel).getByText(/TikTok has switched Duet off for this account/)).toBeInTheDocument()
  })

  test('branded content forbids a private TikTok post and asks for the extra terms', async () => {
    const user = userEvent.setup()
    stub({ accounts: [TIKTOK] })

    open()
    await choose(user, 'Rin on TikTok')
    const panel = await screen.findByRole('group', { name: /Rin on TikTok/ })
    await user.click(within(panel).getByRole('radio', { name: /Branded content/ }))

    expect(within(panel).getByRole('radio', { name: /Only you/ })).toBeDisabled()
    expect(within(panel).getByRole('checkbox', { name: /Branded Content Policy/ })).toBeInTheDocument()
  })

  test('a deployment without TikTok Direct Post says the video arrives as a draft', async () => {
    const user = userEvent.setup()
    stub({ accounts: [TIKTOK] })

    open()
    await choose(user, 'Rin on TikTok')

    expect(await screen.findByText(/saved to your TikTok inbox as a draft/)).toBeInTheDocument()
  })

  test('an audited deployment posts to TikTok directly and says so', async () => {
    const user = userEvent.setup()
    stub({
      accounts: [TIKTOK],
      user: currentUser({ capabilities: gates({ tiktokDirectPost: true }) }),
    })

    open()
    await choose(user, 'Rin on TikTok')

    expect(await screen.findByText(/posted straight to TikTok/)).toBeInTheDocument()
  })

  test('Instagram asks only for the fields Reels actually supports', async () => {
    const user = userEvent.setup()
    stub({ accounts: [INSTAGRAM] })

    open()
    await choose(user, 'Rin on Instagram')

    const panel = await screen.findByRole('group', { name: /Rin on Instagram/ })
    expect(within(panel).getByLabelText('Caption')).toBeInTheDocument()
    expect(within(panel).getByRole('radio', { name: /Also share to the feed/ })).toBeInTheDocument()
    expect(within(panel).queryByRole('radio', { name: 'Private' })).not.toBeInTheDocument()
  })

  test('deselecting a destination warns before its typed metadata is discarded', async () => {
    const user = userEvent.setup()
    stub()

    open()
    await choose(user, 'Rin on YouTube')
    const panel = await screen.findByRole('group', { name: /Rin on YouTube/ })
    await user.type(within(panel).getByLabelText('Title'), 'Approved title')
    await choose(user, 'Rin on YouTube')

    expect(await screen.findByRole('alertdialog')).toHaveTextContent(/discard what you wrote/)
    expect(screen.getByRole('group', { name: /Rin on YouTube/ })).toBeInTheDocument()
  })
})

describe('scheduling', () => {
  test('a schedule in the past is refused before anything is sent', async () => {
    const user = userEvent.setup()
    const api = stub()

    open()
    await choose(user, 'Rin on YouTube')
    await declareYouTube(user)
    await user.click(screen.getByRole('radio', { name: /Schedule/ }))
    await user.type(screen.getByLabelText(/Date and time/), '2020-01-01T09:00')

    expect(await screen.findByText(/Choose a time in the future/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Review and publish/ })).toBeDisabled()
    expect(api.calls.some((call) => call.method === 'POST')).toBe(false)
  })

  test('a scheduled time is sent as the exact instant the chosen zone means', async () => {
    const user = userEvent.setup()
    const api = stub()

    open()
    await choose(user, 'Rin on YouTube')
    await declareYouTube(user)
    await user.click(screen.getByRole('radio', { name: /Schedule/ }))
    await user.selectOptions(screen.getByLabelText(/Time zone/), 'Asia/Jakarta')
    await user.type(screen.getByLabelText(/Date and time/), '2030-03-04T09:00')
    await user.click(screen.getByRole('button', { name: /Review and publish/ }))
    await user.click(await screen.findByRole('button', { name: /^Schedule 1 destination$/ }))

    await waitFor(() => expect(api.calls.some((call) => call.path.endsWith('/confirm'))).toBe(true))
    const prepared = api.calls.find((call) => call.path.endsWith('/publication-drafts'))
    const body = prepared?.body as { destinations: Array<Record<string, unknown>> }
    expect(body.destinations[0]?.scheduledFor).toBe('2030-03-04T02:00:00.000Z')
    expect(body.destinations[0]?.displayTimezone).toBe('Asia/Jakarta')
  })
})

describe('confirmation', () => {
  test('the confirmation names every effect before anything leaves the workspace', async () => {
    const user = userEvent.setup()
    stub()

    open()
    await choose(user, 'Rin on YouTube')
    await declareYouTube(user)
    await user.click(screen.getByRole('button', { name: /Review and publish/ }))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Rin on YouTube')
    expect(dialog).toHaveTextContent('YouTube')
    expect(dialog).toHaveTextContent('Revision 3')
    expect(dialog).toHaveTextContent('ab12cd34')
    expect(dialog).toHaveTextContent('Private')
    expect(dialog).toHaveTextContent(/Not made for kids/)
    expect(dialog).toHaveTextContent(/Publish now/)
    expect(dialog).toHaveTextContent(/cannot be undone from Clipah/)
  })

  test('nothing is sent while the confirmation is still open', async () => {
    const user = userEvent.setup()
    const api = stub()

    open()
    await choose(user, 'Rin on YouTube')
    await declareYouTube(user)
    await user.click(screen.getByRole('button', { name: /Review and publish/ }))

    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(api.calls.every((call) => call.method === 'GET')).toBe(true)
  })

  test('one approved submission sends one prepare, one preflight, and one confirm', async () => {
    const user = userEvent.setup()
    const api = stub()

    open()
    await choose(user, 'Rin on YouTube')
    await declareYouTube(user)
    await user.click(screen.getByRole('button', { name: /Review and publish/ }))
    await user.click(await screen.findByRole('button', { name: /^Publish 1 destination now$/ }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/Rin on YouTube/))
    const posts = api.calls.filter((call) => call.method === 'POST')
    expect(posts.map((call) => call.path.split('/').pop())).toEqual([
      'publication-drafts',
      'preflight',
      'confirm',
    ])
    expect(posts[0]?.headers.get('Idempotency-Key')).not.toBeNull()
  })

  test('a repeated approval reuses one idempotency key rather than publishing twice', async () => {
    const user = userEvent.setup()
    const api = stub({}, { [PREFLIGHT]: { status: 500 } })

    open()
    await choose(user, 'Rin on YouTube')
    await declareYouTube(user)
    await user.click(screen.getByRole('button', { name: /Review and publish/ }))
    const approve = await screen.findByRole('button', { name: /^Publish 1 destination now$/ })
    await user.click(approve)
    await screen.findByRole('alert')
    api.set(PREFLIGHT, {
      body: {
        id: BATCH_ID,
        editRevisionId: 'cccccccc-cccc-4ccc-8ccc-ccccccccccc1',
        renderArtifactId: RENDER_ID,
        publications: [publication({ status: 'awaiting_approval' })],
      },
    })
    await user.click(screen.getByRole('button', { name: /^Publish 1 destination now$/ }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/Rin on YouTube/))
    const keys = api.calls
      .filter((call) => call.path.endsWith('/publication-drafts'))
      .map((call) => call.headers.get('Idempotency-Key'))
    expect(new Set(keys).size).toBe(1)
  })

  test('each destination reports its own outcome when only some of them are accepted', async () => {
    const user = userEvent.setup()
    stub({
      accounts: [YOUTUBE, INSTAGRAM],
      user: currentUser({ capabilities: gates({ multiDestinationScheduling: true }) }),
      statuses: ['scheduled', 'reconnect_required'],
    })

    open()
    await choose(user, 'Rin on YouTube')
    await choose(user, 'Rin on Instagram')
    await declareYouTube(user)
    const instagram = await screen.findByRole('group', { name: /Rin on Instagram/ })
    await userEvent.type(within(instagram).getByLabelText('Caption'), 'A caption')
    await user.click(within(instagram).getByRole('radio', { name: /Also share to the feed/ }))
    await user.click(screen.getByRole('button', { name: /Review and publish/ }))
    await user.click(await screen.findByRole('button', { name: /^Publish 2 destinations now$/ }))

    const result = await screen.findByRole('status')
    expect(result).toHaveTextContent(/Rin on YouTube.*Scheduled/s)
    expect(result).toHaveTextContent(/Rin on Instagram.*reconnect/is)
  })

  test('a refusal keeps the composer open and shows the reason the backend gave', async () => {
    const user = userEvent.setup()
    stub(
      {},
      {
        [CONFIRM]: {
          status: 409,
          body: {
            error: {
              code: 'PUBLICATION_STATE_CONFLICT',
              message: 'That conflicts with the current state.',
              requestId: 'request-1234',
            },
          },
        },
      },
    )

    open()
    await choose(user, 'Rin on YouTube')
    await declareYouTube(user)
    await user.click(screen.getByRole('button', { name: /Review and publish/ }))
    await user.click(await screen.findByRole('button', { name: /^Publish 1 destination now$/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/conflicts with the current state/)
    expect(screen.getByRole('group', { name: /Rin on YouTube/ })).toBeInTheDocument()
  })
})

describe('keyboard and screen readers', () => {
  test('the composer with an open destination has no accessibility violations', async () => {
    const user = userEvent.setup()
    stub()

    open()
    await choose(user, 'Rin on YouTube')
    await screen.findByRole('group', { name: /Rin on YouTube/ })

    const violations = (
      await axe(document.body, { rules: { 'color-contrast': { enabled: false } } })
    ).violations
    expect(violations).toHaveLength(0)
  })

  test('a destination can be chosen and declared with the keyboard alone', async () => {
    const user = userEvent.setup()
    stub()

    open()
    await screen.findByRole('checkbox', { name: /Rin on YouTube/ })
    await user.tab()
    expect(screen.getByRole('checkbox', { name: /Rin on YouTube/ })).toHaveFocus()
    await user.keyboard(' ')

    expect(await screen.findByRole('group', { name: /Rin on YouTube/ })).toBeInTheDocument()
  })
})
