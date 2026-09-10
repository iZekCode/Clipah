/**
 * Reading what publishing actually did, one destination at a time.
 *
 * A batch is a convenience, not a unit of truth, so history refuses to collapse three
 * destinations into one verdict: partial success stays visible, every failure carries a
 * reason a member can act on, and cancelling says plainly what it can and cannot stop.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { PublicationBatchDetail } from '@/features/publishing/PublicationBatchDetail'
import { PublicationHistory } from '@/features/publishing/PublicationHistory'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'
import type { PublicationResponse, SocialAccountResponse } from '@/lib/api/generated/model'

import { renderWithApi, stubApi, type Handler, type StubbedApi } from './support/api'
import { capabilities, currentUser, publication, socialAccount, workspace } from './support/fixtures'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const ACCOUNTS = `GET /api/v1/workspaces/${workspace().id}/social-accounts`
const PUBLICATIONS = 'GET /api/v1/publications'
const BATCH_ID = publication().batchId
const BATCH = `GET /api/v1/publication-batches/${BATCH_ID}`

const YOUTUBE = socialAccount()
const INSTAGRAM = socialAccount({
  id: '88888888-8888-4888-8888-888888888882',
  provider: 'instagram',
  displayName: 'Rin on Instagram',
  externalAccountId: 'ig-1',
})
const TIKTOK = socialAccount({
  id: '88888888-8888-4888-8888-888888888883',
  provider: 'tiktok',
  displayName: 'Rin on TikTok',
  externalAccountId: 'tt-1',
})

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/dashboard/publishing',
}))

/** One batch of three destinations whose states differ on purpose. */
function mixedBatch(): PublicationResponse[] {
  return [
    publication({
      id: '99999999-9999-4999-8999-999999999991',
      socialAccountId: YOUTUBE.id,
      status: 'published',
      publishedAt: '2026-09-10T06:00:00+00:00',
      dispatchedAt: '2026-09-10T05:30:00+00:00',
      transferredAt: '2026-09-10T05:40:00+00:00',
      processingAt: '2026-09-10T05:45:00+00:00',
      providerPublicationId: 'video-1',
      providerPermalink: 'https://www.youtube.com/watch?v=video-1',
      scheduledFor: null,
    }),
    publication({
      id: '99999999-9999-4999-8999-999999999992',
      socialAccountId: INSTAGRAM.id,
      status: 'retryable_failed',
      attemptCount: 2,
      failedAt: '2026-09-10T05:50:00+00:00',
      nextAttemptAt: '2026-09-10T06:10:00+00:00',
      normalizedErrorCode: 'instagram_rate_limited',
      sanitizedErrorMessage: 'Instagram is asking us to wait before publishing again.',
      scheduledFor: null,
    }),
    publication({
      id: '99999999-9999-4999-8999-999999999993',
      socialAccountId: TIKTOK.id,
      status: 'scheduled',
      scheduledFor: '2026-09-12T02:00:00+00:00',
      displayTimezone: 'Asia/Jakarta',
    }),
  ]
}

/** Stub every read the history and detail views perform. */
function stub(
  publications: PublicationResponse[],
  accounts: SocialAccountResponse[] = [YOUTUBE, INSTAGRAM, TIKTOK],
  overrides: Record<string, Handler> = {},
): StubbedApi {
  return stubApi({
    [ME]: { body: currentUser({ capabilities: capabilities({ socialPublishing: true }) }) },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [ACCOUNTS]: { body: { socialAccounts: accounts } },
    [PUBLICATIONS]: { body: { publications } },
    [BATCH]: {
      body: {
        id: BATCH_ID,
        editRevisionId: 'cccccccc-cccc-4ccc-8ccc-ccccccccccc1',
        renderArtifactId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1',
        publications,
      },
    },
    ...overrides,
  })
}

/** Render the Workspace-wide history view. */
function openHistory(): void {
  renderWithApi(
    <WorkspaceProvider>
      <PublicationHistory />
    </WorkspaceProvider>,
  )
}

/** Render one batch's detail view. */
function openBatch(): void {
  renderWithApi(
    <WorkspaceProvider>
      <PublicationBatchDetail batchId={BATCH_ID} />
    </WorkspaceProvider>,
  )
}

describe('history', () => {
  test('one batch reports each destination separately rather than as one verdict', async () => {
    stub(mixedBatch())

    openHistory()

    const rows = await screen.findAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent(/Rin on YouTube.*Published/s)
    expect(rows[1]).toHaveTextContent(/Rin on Instagram.*Will retry/s)
    expect(rows[2]).toHaveTextContent(/Rin on TikTok.*Scheduled/s)
  })

  test('a partly failed batch says how many destinations reached their provider', async () => {
    stub(mixedBatch())

    openHistory()

    expect(await screen.findByText(/1 of 3 published/)).toBeInTheDocument()
    expect(screen.getByText(/1 waiting to retry/)).toBeInTheDocument()
  })

  test('a scheduled destination is shown in the timezone the member chose for it', async () => {
    stub(mixedBatch())

    openHistory()

    const row = (await screen.findAllByRole('listitem'))[2]
    expect(row).toHaveTextContent('12 September 2026')
    expect(row).toHaveTextContent('09:00')
    expect(row).toHaveTextContent('Asia/Jakarta')
  })

  test('the calendar view lists scheduled work by its local day', async () => {
    const user = userEvent.setup()
    stub(mixedBatch())

    openHistory()
    await user.click(await screen.findByRole('button', { name: /Calendar/ }))

    const day = await screen.findByRole('group', { name: /12 September 2026/ })
    expect(within(day).getByText(/Rin on TikTok/)).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: /10 September 2026/ })).not.toBeInTheDocument()
  })

  test('an empty workspace says so instead of showing an empty table', async () => {
    stub([])

    openHistory()

    expect(await screen.findByText(/Nothing has been published from this workspace yet/)).toBeInTheDocument()
  })

  test('a failed read is reported rather than shown as no publications', async () => {
    stub(mixedBatch(), [YOUTUBE], { [PUBLICATIONS]: { status: 500 } })

    openHistory()

    expect(await screen.findByRole('alert')).toHaveTextContent(/Something went wrong/)
  })
})

describe('batch detail', () => {
  test('each destination carries its own timeline of what happened when', async () => {
    stub(mixedBatch())

    openBatch()

    const panel = await screen.findByRole('group', { name: /Rin on YouTube/ })
    const steps = within(panel)
      .getAllByRole('listitem')
      .map((item) => item.textContent)
    expect(steps.join(' ')).toMatch(/Approved.*Sent.*Transferred.*Processing.*Published/s)
  })

  test('a published destination links to the video the provider actually created', async () => {
    stub(mixedBatch())

    openBatch()

    const link = await screen.findByRole('link', { name: /View on YouTube/ })
    expect(link).toHaveAttribute('href', 'https://www.youtube.com/watch?v=video-1')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })

  test('a retryable failure names the reason and when the next attempt is due', async () => {
    stub(mixedBatch())

    openBatch()

    const panel = await screen.findByRole('group', { name: /Rin on Instagram/ })
    expect(panel).toHaveTextContent(/Instagram is asking us to wait/)
    expect(panel).toHaveTextContent(/attempt 2/i)
    expect(within(panel).getByRole('button', { name: /Retry/ })).toBeEnabled()
  })

  test('retrying one destination never touches the destinations beside it', async () => {
    const user = userEvent.setup()
    const failing = mixedBatch()[1]!
    const api = stub(mixedBatch(), undefined, {
      [`POST /api/v1/publications/${failing.id}/retry`]: {
        body: { ...failing, status: 'preflighting' },
      },
    })

    openBatch()
    const panel = await screen.findByRole('group', { name: /Rin on Instagram/ })
    await user.click(within(panel).getByRole('button', { name: /Retry/ }))

    await waitFor(() =>
      expect(api.calls.some((call) => call.path === `/api/v1/publications/${failing.id}/retry`)).toBe(true),
    )
    expect(api.calls.filter((call) => call.path.endsWith('/retry'))).toHaveLength(1)
  })

  test('a destination the provider may already have published must be reconciled first', async () => {
    const user = userEvent.setup()
    const failing = mixedBatch()[1]!
    stub(mixedBatch(), undefined, {
      [`POST /api/v1/publications/${failing.id}/retry`]: {
        status: 409,
        body: {
          error: {
            code: 'PROVIDER_RECONCILIATION_REQUIRED',
            message: 'That conflicts with the current state.',
            requestId: 'request-1234',
          },
        },
      },
    })

    openBatch()
    const panel = await screen.findByRole('group', { name: /Rin on Instagram/ })
    await user.click(within(panel).getByRole('button', { name: /Retry/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /has to be checked against Instagram before it can be tried again/,
    )
  })

  test('cancelling says exactly how much of the work it can still stop', async () => {
    const user = userEvent.setup()
    const scheduled = mixedBatch()[2]!
    stub(mixedBatch(), undefined, {
      [`POST /api/v1/publications/${scheduled.id}/cancel`]: {
        body: { ...scheduled, status: 'cancelled', cancelledAt: '2026-09-10T06:00:00+00:00' },
      },
    })

    openBatch()
    const panel = await screen.findByRole('group', { name: /Rin on TikTok/ })
    await user.click(within(panel).getByRole('button', { name: /Cancel/ }))

    const dialog = await screen.findByRole('alertdialog')
    expect(dialog).toHaveTextContent(/has not been sent to TikTok yet/)
    expect(dialog).toHaveTextContent(/cannot remove a video TikTok has already published/)
  })

  test('a destination already sent to its provider offers no cancel button at all', async () => {
    stub(mixedBatch())

    openBatch()

    const panel = await screen.findByRole('group', { name: /Rin on YouTube/ })
    expect(within(panel).queryByRole('button', { name: /Cancel/ })).not.toBeInTheDocument()
    expect(panel).toHaveTextContent(/already published/)
  })

  test('a destination waiting on approval says which approved value drifted', async () => {
    stub([
      publication({
        socialAccountId: YOUTUBE.id,
        status: 'awaiting_approval',
        preflightDiff: [
          { field: 'capabilityVersion', approved: '2026-01-01', current: '2026-09-01' },
        ],
      }),
    ])

    openBatch()

    const panel = await screen.findByRole('group', { name: /Rin on YouTube/ })
    expect(panel).toHaveTextContent(/capabilityVersion/)
    expect(panel).toHaveTextContent(/2026-01-01/)
    expect(panel).toHaveTextContent(/2026-09-01/)
    expect(panel).toHaveTextContent(/approve it again/i)
  })

  test('an account that lost its authorization offers a reconnect rather than a retry', async () => {
    stub([publication({ socialAccountId: YOUTUBE.id, status: 'reconnect_required' })])

    openBatch()

    const panel = await screen.findByRole('group', { name: /Rin on YouTube/ })
    expect(within(panel).getByRole('link', { name: /Reconnect/ })).toHaveAttribute(
      'href',
      '/dashboard/settings/connections',
    )
    expect(within(panel).queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument()
  })

  test('a member who may not publish sees the history without retry or cancel controls', async () => {
    stub(mixedBatch(), undefined, {
      [WORKSPACES]: { body: { workspaces: [workspace({ role: 'viewer' })] } },
    })

    openBatch()

    expect(await screen.findByRole('group', { name: /Rin on Instagram/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Cancel/ })).not.toBeInTheDocument()
  })
})
