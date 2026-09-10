'use client'

import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { indexApiV1PublicationsGet } from '@/lib/api/generated/publications/publications'
import { indexApiV1WorkspacesWorkspaceIdSocialAccountsGet } from '@/lib/api/generated/social-accounts/social-accounts'
import type {
  PublicationResponse,
  PublicationsResponse,
  SocialAccountResponse,
  SocialAccountsResponse,
} from '@/lib/api/generated/model'

import { providerLabel } from './gates'
import { formatDay, formatTime } from './schedule'
import { statusLabel } from './status'

/** Everything this Workspace has published or is about to, destination by destination. */
export function PublicationHistory() {
  const { active } = useWorkspaceScope()
  const [view, setView] = useState<'list' | 'calendar'>('list')

  const publications = useQuery<PublicationsResponse, ApiError>({
    queryKey: ['/api/v1/publications', active.id],
    queryFn: ({ signal }) => indexApiV1PublicationsGet({ workspace_id: active.id }, { signal }),
    retry: false,
  })
  const accounts = useQuery<SocialAccountsResponse, ApiError>({
    queryKey: ['/api/v1/social-accounts', active.id],
    queryFn: ({ signal }) => indexApiV1WorkspacesWorkspaceIdSocialAccountsGet(active.id, { signal }),
    retry: false,
  })

  const rows = publications.data?.publications ?? []
  const byAccount = useMemo(
    () => new Map((accounts.data?.socialAccounts ?? []).map((account) => [account.id, account])),
    [accounts.data],
  )

  if (publications.isError) return <ErrorNotice error={publications.error} />
  if (publications.isPending) return <p role="status">Loading publications…</p>
  if (rows.length === 0) {
    return <p>Nothing has been published from this workspace yet.</p>
  }

  return (
    <section aria-labelledby="publishing-history-heading" className="space-y-4">
      <h2 id="publishing-history-heading" className="text-lg font-semibold">
        Publishing
      </h2>
      {accounts.isError ? <ErrorNotice error={accounts.error} /> : null}
      <p className="text-sm text-muted-foreground">{tally(rows)}</p>
      <div className="flex gap-2">
        <button type="button" onClick={() => setView('list')} aria-pressed={view === 'list'}>
          List
        </button>
        <button type="button" onClick={() => setView('calendar')} aria-pressed={view === 'calendar'}>
          Calendar
        </button>
      </div>
      {view === 'list' ? (
        <ul className="divide-y rounded border">
          {rows.map((item) => (
            <li key={item.id} className="space-y-1 p-3 text-sm">
              <DestinationLine publication={item} account={byAccount.get(item.socialAccountId)} />
            </li>
          ))}
        </ul>
      ) : (
        <Calendar rows={rows} accounts={byAccount} />
      )}
    </section>
  )
}

function DestinationLine({
  publication,
  account,
}: {
  publication: PublicationResponse
  account: SocialAccountResponse | undefined
}) {
  const provider = account?.provider ?? ''
  return (
    <>
      <p className="font-medium">
        {account?.displayName ?? 'A disconnected account'} · {providerLabel(provider)}
      </p>
      <p>{statusLabel(publication.status, provider)}</p>
      {publication.scheduledFor === null ? null : (
        <p className="text-muted-foreground">
          {formatDay(publication.scheduledFor, publication.displayTimezone)} at{' '}
          {formatTime(publication.scheduledFor, publication.displayTimezone)} (
          {publication.displayTimezone})
        </p>
      )}
      <a className="underline" href={`/dashboard/publishing/${publication.batchId}`}>
        Open this batch
      </a>
    </>
  )
}

function Calendar({
  rows,
  accounts,
}: {
  rows: PublicationResponse[]
  accounts: Map<string, SocialAccountResponse>
}) {
  const scheduled = rows.filter(
    (item): item is PublicationResponse & { scheduledFor: string } => item.scheduledFor !== null,
  )
  const days = new Map<string, PublicationResponse[]>()
  for (const item of scheduled) {
    const day = formatDay(item.scheduledFor, item.displayTimezone)
    days.set(day, [...(days.get(day) ?? []), item])
  }
  if (days.size === 0) {
    return <p>Nothing is scheduled.</p>
  }
  return (
    <div className="space-y-4">
      {[...days.entries()].map(([day, items]) => (
        <fieldset key={day} className="rounded border p-3">
          <legend className="px-1 text-sm font-medium">{day}</legend>
          <ul className="text-sm">
            {items.map((item) => {
              const account = accounts.get(item.socialAccountId)
              return (
                <li key={item.id}>
                  {formatTime(item.scheduledFor as string, item.displayTimezone)} ·{' '}
                  {account?.displayName ?? 'A disconnected account'} ·{' '}
                  {statusLabel(item.status, account?.provider ?? '')}
                </li>
              )
            })}
          </ul>
        </fieldset>
      ))}
    </div>
  )
}

/** How many destinations reached their provider, and how many did not. */
function tally(rows: PublicationResponse[]): string {
  const published = rows.filter((item) => item.status === 'published').length
  const retrying = rows.filter((item) => item.status === 'retryable_failed').length
  const failed = rows.filter(
    (item) => item.status === 'permanent_failed' || item.status === 'reconnect_required',
  ).length
  const parts = [`${published} of ${rows.length} published`]
  if (retrying > 0) parts.push(`${retrying} waiting to retry`)
  if (failed > 0) parts.push(`${failed} needing attention`)
  return parts.join(' · ')
}
