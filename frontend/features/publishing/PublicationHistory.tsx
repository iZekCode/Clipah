'use client'

import { useQuery } from '@tanstack/react-query'
import { Send } from 'lucide-react'
import Link from 'next/link'
import { useMemo, useState } from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { StatusBadge, type StatusTone } from '@/components/status-badge'
import { SegmentedControl } from '@/components/ui/segmented-control'
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

import { AccountAvatar } from './AccountAvatar'
import { providerLabel } from './gates'
import { formatDay, formatTime, scheduleBucket } from './schedule'
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

  if (publications.isError) return <ErrorNotice error={publications.error} onRetry={() => void publications.refetch()} />
  if (publications.isPending) return <LoadingState label="Loading publications…" variant="rows" />
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={Send}
        title="Nothing has been published from this workspace yet."
        description="Export a clip, then choose New publication to send it to a connected account."
      />
    )
  }

  return (
    <section aria-labelledby="publishing-history-heading" className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="publishing-history-heading" className="text-title">
            Queue and history
          </h2>
          <p className="font-mono text-caption text-muted-foreground">{tally(rows)}</p>
        </div>
        <SegmentedControl
          label="View"
          value={view}
          options={[
            { value: 'list', label: 'List' },
            { value: 'calendar', label: 'Calendar' },
          ]}
          onChange={setView}
        />
      </div>
      {accounts.isError ? <ErrorNotice error={accounts.error} /> : null}
      {view === 'list' ? (
        <div className="space-y-8">
          {GROUPS.map((group) => {
            const members = rows.filter((item) => group.statuses.includes(item.status))
            if (members.length === 0) {
              return null
            }
            return (
              <section key={group.id} aria-labelledby={`publishing-group-${group.id}`} className="space-y-3">
                <h3 className="flex items-baseline gap-2 text-title">
                  <span id={`publishing-group-${group.id}`}>{group.label}</span>
                  <span className="tabular font-mono text-caption text-muted-foreground">
                    {members.length}
                  </span>
                </h3>
                {group.id === 'scheduled' ? (
                  <ScheduledTimeline members={members} accounts={byAccount} label={group.label} />
                ) : (
                  <ul aria-label={group.label} className="divide-y divide-border rounded-lg border">
                    {members.map((item) => (
                      <li key={item.id} className="px-4 py-3">
                        <DestinationLine publication={item} account={byAccount.get(item.socialAccountId)} />
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )
          })}
        </div>
      ) : (
        <Calendar rows={rows} accounts={byAccount} />
      )}
    </section>
  )
}

const BUCKETS = [
  { id: 'today', label: 'Today' },
  { id: 'tomorrow', label: 'Tomorrow' },
  { id: 'later', label: 'Later' },
] as const

/** Scheduled work by the day it goes out, in the zone each destination was scheduled in. */
function ScheduledTimeline({
  members,
  accounts,
  label,
}: {
  members: PublicationResponse[]
  accounts: Map<string, SocialAccountResponse>
  label: string
}) {
  const now = new Date()
  const byBucket = new Map<string, PublicationResponse[]>()
  for (const item of members) {
    const bucket = scheduleBucket(
      item.scheduledFor ?? item.createdAt ?? now.toISOString(),
      now,
      item.displayTimezone,
    )
    byBucket.set(bucket, [...(byBucket.get(bucket) ?? []), item])
  }
  return (
    <ul aria-label={label} className="space-y-4">
      {BUCKETS.map((bucket) => {
        const items = byBucket.get(bucket.id) ?? []
        if (items.length === 0) return null
        return (
          <li key={bucket.id} className="space-y-2">
            <h4 className="text-caption font-medium uppercase tracking-wide text-subtle-foreground">
              {bucket.label}
            </h4>
            <ul aria-label={bucket.label} className="divide-y divide-border rounded-lg border">
              {items.map((item) => (
                <li key={item.id} className="px-4 py-3">
                  <DestinationLine publication={item} account={accounts.get(item.socialAccountId)} />
                </li>
              ))}
            </ul>
          </li>
        )
      })}
    </ul>
  )
}

/** The piles a creator reads their publishing queue in, most time-sensitive first. */
const GROUPS: { id: string; label: string; statuses: string[] }[] = [
  { id: 'attention', label: 'Needs attention', statuses: ['retryable_failed', 'reconnect_required', 'permanent_failed'] },
  { id: 'scheduled', label: 'Scheduled', statuses: ['draft', 'awaiting_approval', 'scheduled'] },
  { id: 'progress', label: 'In progress', statuses: ['preflighting', 'transferring', 'processing'] },
  { id: 'published', label: 'Published', statuses: ['published'] },
  { id: 'cancelled', label: 'Cancelled', statuses: ['cancelled'] },
]

function DestinationLine({
  publication,
  account,
}: {
  publication: PublicationResponse
  account: SocialAccountResponse | undefined
}) {
  const provider = account?.provider ?? ''
  return (
    <div className="flex flex-wrap items-center gap-3">
      <AccountAvatar account={account} />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-small font-medium">
          {account?.displayName ?? 'A disconnected account'}{' '}
          <span className="font-normal text-muted-foreground">· {providerLabel(provider)}</span>
        </p>
        {publication.scheduledFor === null ? null : (
          <p className="tabular font-mono text-caption text-muted-foreground">
            {formatDay(publication.scheduledFor, publication.displayTimezone)} at{' '}
            {formatTime(publication.scheduledFor, publication.displayTimezone)} (
            {publication.displayTimezone})
          </p>
        )}
        <Link
          className="inline-block text-caption font-semibold text-primary hover:underline"
          href={`/dashboard/publishing/${publication.batchId}`}
        >
          Open this batch
        </Link>
      </div>
      <StatusBadge tone={toneFor(publication.status)}>{statusLabel(publication.status, provider)}</StatusBadge>
    </div>
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
        <fieldset key={day} className="rounded-lg border p-4">
          <legend className="px-1 text-title">{day}</legend>
          <ul className="space-y-1 font-mono text-caption">
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

function toneFor(status: string): StatusTone {
  if (status === 'published') return 'success'
  if (status === 'retryable_failed' || status === 'reconnect_required') return 'attention'
  if (status === 'permanent_failed') return 'danger'
  if (status === 'cancelled' || status === 'draft') return 'neutral'
  return 'progress'
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
