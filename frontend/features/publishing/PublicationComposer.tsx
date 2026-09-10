'use client'

import { useQueries, useQuery } from '@tanstack/react-query'
import { useMemo, useRef, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { useSession } from '@/features/auth/session'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import {
  confirmApiV1PublicationDraftsDraftIdConfirmPost,
  preflightApiV1PublicationDraftsDraftIdPreflightPost,
  prepareApiV1EditsEditIdRevisionsRevisionPublicationDraftsPost,
} from '@/lib/api/generated/publications/publications'
import {
  capabilitiesApiV1SocialAccountsSocialAccountIdCapabilitiesGet,
  indexApiV1WorkspacesWorkspaceIdSocialAccountsGet,
} from '@/lib/api/generated/social-accounts/social-accounts'
import type {
  PublicationBatchResponse,
  SocialAccountResponse,
  SocialAccountsResponse,
  SocialCapabilitiesResponse,
} from '@/lib/api/generated/model'

import { DestinationPanel } from './DestinationPanel'
import { tiktokChoices } from './creator-capabilities'
import {
  destinationPayload,
  emptyDraft,
  hasContent,
  isComplete,
  summaryLines,
  type DestinationDraft,
} from './destination-draft'
import { mayPublish, openProviders, providerLabel } from './gates'
import { browserTimezone, formatInstant, instantFor, timezoneOptions } from './schedule'
import { statusLabel } from './status'

const CONNECTIONS_HREF = '/dashboard/settings/connections'

/** One approved render, and the destinations a member may deliberately send it to. */
export function PublicationComposer({
  editId,
  revision,
  renderArtifactId,
  renderDigest,
  durationMs,
}: {
  editId: string
  revision: number
  renderArtifactId: string
  renderDigest: string | null
  durationMs: number
}) {
  const { active } = useWorkspaceScope()
  const session = useSession()
  const [selected, setSelected] = useState<string[]>([])
  const [drafts, setDrafts] = useState<Record<string, DestinationDraft>>({})
  const [timing, setTiming] = useState<'now' | 'schedule'>('now')
  const [localTime, setLocalTime] = useState('')
  const [timezone, setTimezone] = useState(browserTimezone)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [discarding, setDiscarding] = useState<string | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [result, setResult] = useState<PublicationBatchResponse | null>(null)
  const [sending, setSending] = useState(false)
  const submission = useRef<{ key: string; payload: string } | null>(null)

  const accounts = useQuery<SocialAccountsResponse, ApiError>({
    queryKey: ['/api/v1/social-accounts', active.id],
    queryFn: ({ signal }) => indexApiV1WorkspacesWorkspaceIdSocialAccountsGet(active.id, { signal }),
    retry: false,
    enabled: session.data?.capabilities.socialPublishing === true,
  })
  const snapshots = useQueries({
    queries: selected.map((accountId) => ({
      queryKey: ['/api/v1/social-account-capabilities', accountId],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        capabilitiesApiV1SocialAccountsSocialAccountIdCapabilitiesGet(
          accountId,
          { workspace_id: active.id },
          { signal },
        ),
      retry: false,
    })),
  })
  const capabilityByAccount = useMemo(() => {
    const entries = new Map<string, SocialCapabilitiesResponse | undefined>()
    selected.forEach((accountId, index) => entries.set(accountId, snapshots[index]?.data))
    return entries
  }, [selected, snapshots])

  if (session.isPending) return <p role="status">Loading publishing…</p>
  if (session.isError) return <ErrorNotice error={session.error} />

  const capabilities = session.data.capabilities
  const providers = openProviders(capabilities)
  if (!session.data.capabilities.socialPublishing) {
    return <p>Social publishing is not enabled for this deployment.</p>
  }
  if (providers.length === 0) {
    return <p>No publishing provider is switched on for this deployment yet.</p>
  }
  if (!mayPublish(active)) {
    return <p>Your role may not publish in this workspace.</p>
  }
  if (!session.data.recentAuthentication) {
    return <p>Sign in again before publishing, so we know it is still you.</p>
  }

  const connectable = (accounts.data?.socialAccounts ?? []).filter((account) =>
    (providers as readonly string[]).includes(account.provider),
  )
  const chosen = connectable.filter((account) => selected.includes(account.id))
  const draftFor = (accountId: string) => drafts[accountId] ?? emptyDraft()
  const scheduledFor = timing === 'schedule' ? instantFor(localTime, timezone) : null
  const scheduleIsPast =
    timing === 'schedule' && scheduledFor !== null && Date.parse(scheduledFor) <= Date.now()
  const tooManyDestinations = chosen.length > 1 && !capabilities.multiDestinationScheduling
  const everyDestinationComplete =
    chosen.length > 0 &&
    chosen.every((account) =>
      isComplete(
        account.provider,
        draftFor(account.id),
        account.provider === 'tiktok'
          ? tiktokChoices(capabilityByAccount.get(account.id))
          : null,
      ),
    )
  const ready =
    everyDestinationComplete &&
    !tooManyDestinations &&
    !sending &&
    (timing === 'now' || (scheduledFor !== null && !scheduleIsPast))

  function toggle(account: SocialAccountResponse): void {
    if (selected.includes(account.id)) {
      if (hasContent(draftFor(account.id))) {
        setDiscarding(account.id)
        return
      }
      setSelected(selected.filter((id) => id !== account.id))
      return
    }
    setSelected([...selected, account.id])
  }

  function discard(accountId: string): void {
    setSelected(selected.filter((id) => id !== accountId))
    setDrafts((current) => {
      const next = { ...current }
      delete next[accountId]
      return next
    })
    setDiscarding(null)
  }

  function change(accountId: string, patch: Partial<DestinationDraft>): void {
    setDrafts((current) => ({
      ...current,
      [accountId]: { ...(current[accountId] ?? emptyDraft()), ...patch },
    }))
  }

  function directPostFor(account: SocialAccountResponse): boolean {
    return account.provider !== 'tiktok' || capabilities.tiktokDirectPost
  }

  function idempotencyKeyFor(payload: string): string {
    if (submission.current?.payload !== payload) {
      submission.current = { key: newKey(), payload }
    }
    return submission.current.key
  }

  async function publish(): Promise<void> {
    const destinations = chosen.map((account) =>
      destinationPayload({
        account,
        draft: draftFor(account.id),
        directPost: directPostFor(account),
        scheduledFor,
        displayTimezone: timezone,
        now: new Date(confirming ?? Date.now()),
      }),
    )
    const body = { renderArtifactId, destinations }
    const key = idempotencyKeyFor(JSON.stringify(body))
    setSending(true)
    setFailure(null)
    try {
      const prepared = await prepareApiV1EditsEditIdRevisionsRevisionPublicationDraftsPost(
        editId,
        revision,
        body,
        { workspace_id: active.id },
        { headers: { 'Idempotency-Key': key } },
      )
      await preflightApiV1PublicationDraftsDraftIdPreflightPost(prepared.id, {
        workspace_id: active.id,
      })
      const confirmed = await confirmApiV1PublicationDraftsDraftIdConfirmPost(prepared.id, {
        workspace_id: active.id,
      })
      setResult(confirmed)
      setConfirming(null)
      submission.current = null
    } catch (error) {
      setFailure(error as ApiError)
    } finally {
      setSending(false)
    }
  }

  const approvalLabel =
    timing === 'schedule'
      ? `Schedule ${chosen.length} ${chosen.length === 1 ? 'destination' : 'destinations'}`
      : `Publish ${chosen.length} ${chosen.length === 1 ? 'destination' : 'destinations'} now`

  return (
    <section aria-labelledby="composer-heading" className="space-y-6">
      <h2 id="composer-heading" className="text-lg font-semibold">
        Publish this clip
      </h2>
      {accounts.isError ? <ErrorNotice error={accounts.error} /> : null}
      {failure === null ? null : <ErrorNotice error={failure} />}

      <fieldset className="space-y-2 rounded border p-4">
        <legend className="px-1 text-sm font-medium">Destinations</legend>
        {connectable.length === 0 ? (
          <p>
            No account is connected for a provider this deployment publishes to.{' '}
            <a className="underline" href={CONNECTIONS_HREF}>
              Connect an account
            </a>
            .
          </p>
        ) : null}
        {connectable.map((account) => {
          const unhealthy = healthProblem(account)
          return (
            <div key={account.id} className="space-y-1">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={selected.includes(account.id)}
                  disabled={unhealthy !== null}
                  onChange={() => toggle(account)}
                />
                <span>
                  {account.displayName} · {providerLabel(account.provider)}
                </span>
              </label>
              {unhealthy === null ? null : (
                <p className="pl-6 text-xs text-muted-foreground">
                  {unhealthy}{' '}
                  <a className="underline" href={CONNECTIONS_HREF}>
                    Reconnect this account
                  </a>
                  .
                </p>
              )}
            </div>
          )
        })}
        {tooManyDestinations ? (
          <p className="text-sm">
            Publishing to more than one destination at a time is not enabled for this
            workspace yet. Choose one destination.
          </p>
        ) : null}
      </fieldset>

      {chosen.map((account) => (
        <DestinationPanel
          key={account.id}
          account={account}
          draft={draftFor(account.id)}
          choices={
            account.provider === 'tiktok'
              ? capabilityByAccount.get(account.id) === undefined
                ? null
                : tiktokChoices(capabilityByAccount.get(account.id))
              : null
          }
          publicPrivacyAllowed={capabilities.youtubePublicPrivacy}
          directPost={directPostFor(account)}
          onChange={(patch) => change(account.id, patch)}
        />
      ))}

      <fieldset className="space-y-2 rounded border p-4">
        <legend className="px-1 text-sm font-medium">When to publish</legend>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="publish-timing"
            checked={timing === 'now'}
            onChange={() => setTiming('now')}
          />
          <span>Publish now</span>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="publish-timing"
            checked={timing === 'schedule'}
            onChange={() => setTiming('schedule')}
          />
          <span>Schedule for later</span>
        </label>
        {timing === 'schedule' ? (
          <div className="space-y-2 pl-6">
            <label className="block space-y-1 text-sm font-medium">
              <span>Date and time</span>
              <input
                type="datetime-local"
                value={localTime}
                onChange={(event) => setLocalTime(event.target.value)}
                className="rounded border p-2"
              />
            </label>
            <label className="block space-y-1 text-sm font-medium">
              <span>Time zone</span>
              <select
                value={timezone}
                onChange={(event) => setTimezone(event.target.value)}
                className="rounded border p-2"
              >
                {timezoneOptions().map((zone) => (
                  <option key={zone} value={zone}>
                    {zone}
                  </option>
                ))}
              </select>
            </label>
            {scheduleIsPast ? <p className="text-sm">Choose a time in the future.</p> : null}
          </div>
        ) : null}
      </fieldset>

      <button
        type="button"
        className="rounded bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50"
        disabled={!ready}
        onClick={() => setConfirming(new Date().toISOString())}
      >
        Review and publish
      </button>

      {result === null ? null : (
        <div role="status" className="space-y-1 rounded border p-4 text-sm">
          <p>What each destination is doing now:</p>
          <ul>
            {result.publications.map((item) => {
              const account = connectable.find((row) => row.id === item.socialAccountId)
              return (
                <li key={item.id}>
                  {account?.displayName ?? 'This account'} ·{' '}
                  {statusLabel(item.status, account?.provider ?? '')}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {discarding === null ? null : (
        <div role="alertdialog" aria-label="Discard this destination" className="rounded border p-4">
          <p>Removing this destination will discard what you wrote for it. Continue?</p>
          <button type="button" onClick={() => discard(discarding)}>
            Discard and remove
          </button>
          <button type="button" onClick={() => setDiscarding(null)}>
            Keep this destination
          </button>
        </div>
      )}

      {confirming !== null ? (
        <div role="dialog" aria-label="Confirm publication" className="space-y-3 rounded border p-4">
          <p className="text-sm font-medium">
            Revision {revision}, rendered artifact{' '}
            {renderDigest === null ? renderArtifactId : `${renderDigest.slice(0, 12)}…`},{' '}
            {Math.round(durationMs / 1000)} seconds.
          </p>
          <p className="text-sm">
            {timing === 'schedule' && scheduledFor !== null
              ? `Requested time: ${formatInstant(scheduledFor, timezone)}.`
              : 'Requested time: Publish now.'}
          </p>
          <ul className="space-y-2 text-sm">
            {chosen.map((account) => (
              <li key={account.id}>
                <p className="font-medium">
                  {account.displayName} ({providerLabel(account.provider)})
                </p>
                <ul>
                  {summaryLines(
                    account.provider,
                    draftFor(account.id),
                    directPostFor(account),
                  ).map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
          <p className="text-sm">
            Publishing sends this video to the accounts above. It cannot be undone from
            Clipah once a provider has published it.
          </p>
          <button
            type="button"
            className="rounded bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50"
            disabled={sending}
            onClick={() => {
              void publish()
            }}
          >
            {approvalLabel}
          </button>
          <button type="button" onClick={() => setConfirming(null)}>
            Go back
          </button>
        </div>
      ) : null}
    </section>
  )
}

/** Why one connected account cannot be a destination right now, if it cannot. */
function healthProblem(account: SocialAccountResponse): string | null {
  if (account.connectionStatus === 'revoked') {
    return `This account's access was revoked, so nothing can be published to it.`
  }
  if (account.connectionStatus !== 'active') {
    return 'This account has to be reconnected before it can be published to.'
  }
  return null
}

function newKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `publication-${Date.now()}-${Math.random().toString(16).slice(2)}`
}
