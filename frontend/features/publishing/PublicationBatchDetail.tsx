'use client'

import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import { ApiError } from '@/lib/api/client'
import {
  cancelApiV1PublicationsPublicationIdCancelPost,
  retryApiV1PublicationsPublicationIdRetryPost,
  showBatchApiV1PublicationBatchesDraftIdGet,
} from '@/lib/api/generated/publications/publications'
import { indexApiV1WorkspacesWorkspaceIdSocialAccountsGet } from '@/lib/api/generated/social-accounts/social-accounts'
import type {
  PublicationBatchResponse,
  PublicationResponse,
  SocialAccountResponse,
  SocialAccountsResponse,
} from '@/lib/api/generated/model'

import { mayPublish, providerLabel } from './gates'
import { formatInstant } from './schedule'
import { actionableReason, mayCancel, mayRetry, statusLabel, timeline } from './status'

const CONNECTIONS_HREF = '/dashboard/settings/connections'

/** One batch, told as the independent story of each destination inside it. */
export function PublicationBatchDetail({ batchId }: { batchId: string }) {
  const { active } = useWorkspaceScope()
  const [failure, setFailure] = useState<{ message: string; error: unknown } | null>(null)
  const [cancelling, setCancelling] = useState<PublicationResponse | null>(null)

  const batch = useQuery<PublicationBatchResponse, ApiError>({
    queryKey: ['/api/v1/publication-batches', batchId, active.id],
    queryFn: ({ signal }) =>
      showBatchApiV1PublicationBatchesDraftIdGet(batchId, { workspace_id: active.id }, { signal }),
    retry: false,
  })
  const accounts = useQuery<SocialAccountsResponse, ApiError>({
    queryKey: ['/api/v1/social-accounts', active.id],
    queryFn: ({ signal }) => indexApiV1WorkspacesWorkspaceIdSocialAccountsGet(active.id, { signal }),
    retry: false,
  })
  const byAccount = useMemo(
    () => new Map((accounts.data?.socialAccounts ?? []).map((account) => [account.id, account])),
    [accounts.data],
  )

  if (batch.isPending) return <p role="status">Loading this batch…</p>
  if (batch.isError) return <ErrorNotice error={batch.error} />

  const controllable = mayPublish(active)

  async function retry(publication: PublicationResponse, provider: string): Promise<void> {
    setFailure(null)
    try {
      await retryApiV1PublicationsPublicationIdRetryPost(publication.id, {
        workspace_id: active.id,
      })
      await batch.refetch()
    } catch (error) {
      setFailure({ message: retryRefusal(error, provider), error })
    }
  }

  async function cancel(publication: PublicationResponse): Promise<void> {
    setFailure(null)
    setCancelling(null)
    try {
      await cancelApiV1PublicationsPublicationIdCancelPost(publication.id, {
        workspace_id: active.id,
      })
      await batch.refetch()
    } catch (error) {
      setFailure({
        message: 'This destination can no longer be stopped from Clipah.',
        error,
      })
    }
  }

  return (
    <section aria-labelledby="batch-heading" className="space-y-4">
      <h2 id="batch-heading" className="text-lg font-semibold">
        One clip, {batch.data.publications.length}{' '}
        {batch.data.publications.length === 1 ? 'destination' : 'destinations'}
      </h2>
      {failure === null ? null : (
        <div role="alert" className="rounded border border-destructive/40 bg-destructive/10 p-4">
          <p className="text-sm">{failure.message}</p>
          {failure.error instanceof ApiError && failure.error.requestId !== null ? (
            <p className="text-xs text-muted-foreground">Request ID: {failure.error.requestId}</p>
          ) : null}
        </div>
      )}
      {batch.data.publications.map((publication) => (
        <DestinationDetail
          key={publication.id}
          publication={publication}
          account={byAccount.get(publication.socialAccountId)}
          controllable={controllable}
          onRetry={retry}
          onCancel={setCancelling}
        />
      ))}
      {cancelling === null ? null : (
        <div role="alertdialog" aria-label="Cancel this destination" className="rounded border p-4">
          <p className="text-sm">
            This destination has not been sent to{' '}
            {providerLabel(byAccount.get(cancelling.socialAccountId)?.provider ?? '')} yet, so
            cancelling stops it.
          </p>
          <p className="text-sm">
            Cancelling cannot remove a video{' '}
            {providerLabel(byAccount.get(cancelling.socialAccountId)?.provider ?? '')} has already
            published, and it changes nothing at the other destinations in this batch.
          </p>
          <button
            type="button"
            onClick={() => {
              void cancel(cancelling)
            }}
          >
            Cancel this destination
          </button>
          <button type="button" onClick={() => setCancelling(null)}>
            Keep it scheduled
          </button>
        </div>
      )}
    </section>
  )
}

function DestinationDetail({
  publication,
  account,
  controllable,
  onRetry,
  onCancel,
}: {
  publication: PublicationResponse
  account: SocialAccountResponse | undefined
  controllable: boolean
  onRetry: (publication: PublicationResponse, provider: string) => Promise<void>
  onCancel: (publication: PublicationResponse) => void
}) {
  const provider = account?.provider ?? ''
  const reason = actionableReason(publication, provider)
  return (
    <fieldset className="space-y-2 rounded border p-4 text-sm">
      <legend className="px-1 font-medium">
        {account?.displayName ?? 'A disconnected account'} ({providerLabel(provider)})
      </legend>
      <p>{statusLabel(publication.status, provider)}</p>
      {publication.status === 'published' ? (
        <p>
          This destination is already published, so it can no longer be cancelled from Clipah.
        </p>
      ) : null}
      {publication.providerPermalink === null ? null : (
        <a
          className="underline"
          href={publication.providerPermalink}
          target="_blank"
          rel="noopener noreferrer"
        >
          View on {providerLabel(provider)}
        </a>
      )}
      {publication.providerPublicationId === null ? null : (
        <p className="text-muted-foreground">
          {providerLabel(provider)} identifier: {publication.providerPublicationId}
        </p>
      )}
      {reason === null ? null : <p>{reason}</p>}
      {publication.attemptCount > 0 ? <p>Attempt {publication.attemptCount}.</p> : null}
      {publication.nextAttemptAt === null ? null : (
        <p>Next attempt {formatInstant(publication.nextAttemptAt, publication.displayTimezone)}.</p>
      )}
      {publication.preflightDiff.length === 0 ? null : (
        <div>
          <p>Approve it again: what you approved no longer matches this account.</p>
          <ul>
            {publication.preflightDiff.map((difference) => (
              <li key={difference.field}>
                {difference.field}: approved {difference.approved ?? 'nothing'}, now{' '}
                {difference.current ?? 'nothing'}
              </li>
            ))}
          </ul>
        </div>
      )}
      <ul className="text-muted-foreground">
        {timeline(publication, provider).map((step) => (
          <li key={step.label}>
            {step.label} · {formatInstant(step.instant, publication.displayTimezone)}
          </li>
        ))}
      </ul>
      {publication.status === 'reconnect_required' ? (
        <a className="underline" href={CONNECTIONS_HREF}>
          Reconnect this account
        </a>
      ) : null}
      {controllable && mayRetry(publication) ? (
        <button
          type="button"
          onClick={() => {
            void onRetry(publication, provider)
          }}
        >
          Retry this destination
        </button>
      ) : null}
      {controllable && mayCancel(publication) ? (
        <button type="button" onClick={() => onCancel(publication)}>
          Cancel this destination
        </button>
      ) : null}
    </fieldset>
  )
}

/** Say what a refused retry actually means, rather than repeating a status code. */
function retryRefusal(error: unknown, provider: string): string {
  if (error instanceof ApiError && error.code === 'PROVIDER_RECONCILIATION_REQUIRED') {
    return `This destination has to be checked against ${providerLabel(provider)} before it can be tried again, in case the video is already there.`
  }
  if (error instanceof ApiError) {
    return error.message
  }
  return 'Something went wrong. Please try again.'
}
