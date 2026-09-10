'use client'

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { useSession } from '@/features/auth/session'
import { mayManageConnections, openProviders, providerLabel } from '@/features/publishing/gates'
import { formatDay } from '@/features/publishing/schedule'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import { ApiError } from '@/lib/api/client'
import {
  disconnectApiV1SocialAccountsSocialAccountIdDelete,
  indexApiV1WorkspacesWorkspaceIdSocialAccountsGet,
  refreshApiV1SocialAccountsSocialAccountIdRefreshPost,
  startConnectionApiV1WorkspacesWorkspaceIdSocialAccountsProviderConnectPost,
} from '@/lib/api/generated/social-accounts/social-accounts'
import type { SocialAccountResponse, SocialAccountsResponse } from '@/lib/api/generated/model'

/** The Social Accounts this Workspace may publish through, and how healthy each one is. */
export function SocialConnections() {
  const { active } = useWorkspaceScope()
  const session = useSession()
  const [failure, setFailure] = useState<{ message: string; error: unknown } | null>(null)
  const [ending, setEnding] = useState<SocialAccountResponse | null>(null)

  const accounts = useQuery<SocialAccountsResponse, ApiError>({
    queryKey: ['/api/v1/social-accounts', active.id],
    queryFn: ({ signal }) => indexApiV1WorkspacesWorkspaceIdSocialAccountsGet(active.id, { signal }),
    retry: false,
    enabled: session.data?.capabilities.socialPublishing === true,
  })

  if (session.isPending) return <p role="status">Loading connections…</p>
  if (session.isError) return <ErrorNotice error={session.error} />

  const providers = openProviders(session.data.capabilities)
  if (!session.data.capabilities.socialPublishing) {
    return <p>Social publishing is not enabled for this deployment.</p>
  }
  if (providers.length === 0) {
    return <p>No publishing provider is switched on for this deployment yet.</p>
  }
  const manageable = mayManageConnections(active)
  const fresh = session.data.recentAuthentication

  async function connect(provider: string): Promise<void> {
    setFailure(null)
    try {
      const started =
        await startConnectionApiV1WorkspacesWorkspaceIdSocialAccountsProviderConnectPost(
          active.id,
          provider as 'youtube' | 'instagram' | 'tiktok',
          undefined,
        )
      window.location.assign(started.authorizationUrl)
    } catch (error) {
      setFailure({ message: connectionRefusal(error), error })
    }
  }

  async function refresh(account: SocialAccountResponse): Promise<void> {
    setFailure(null)
    try {
      await refreshApiV1SocialAccountsSocialAccountIdRefreshPost(account.id, {
        workspace_id: active.id,
      })
      await accounts.refetch()
    } catch (error) {
      setFailure({ message: connectionRefusal(error), error })
    }
  }

  async function disconnect(account: SocialAccountResponse): Promise<void> {
    setFailure(null)
    setEnding(null)
    try {
      await disconnectApiV1SocialAccountsSocialAccountIdDelete(account.id, {
        workspace_id: active.id,
      })
      await accounts.refetch()
    } catch (error) {
      setFailure({ message: connectionRefusal(error), error })
    }
  }

  return (
    <section aria-labelledby="social-connections-heading" className="space-y-4">
      <h2 id="social-connections-heading" className="text-lg font-semibold">
        Publishing accounts
      </h2>
      {accounts.isError ? <ErrorNotice error={accounts.error} /> : null}
      {failure === null ? null : (
        <div role="alert" className="rounded border border-destructive/40 bg-destructive/10 p-4">
          <p className="text-sm">{failure.message}</p>
          {failure.error instanceof ApiError && failure.error.requestId !== null ? (
            <p className="text-xs text-muted-foreground">Request ID: {failure.error.requestId}</p>
          ) : null}
        </div>
      )}
      {manageable ? null : <p>Only an owner or admin can change connections.</p>}
      {manageable && !fresh ? (
        <p>Sign in again to change connections, so we know it is still you.</p>
      ) : null}

      {(accounts.data?.socialAccounts ?? []).map((account) => (
        <fieldset key={account.id} className="space-y-2 rounded border p-4 text-sm">
          <legend className="px-1 font-medium">
            {account.displayName} ({providerLabel(account.provider)})
          </legend>
          <p>{healthLine(account)}</p>
          <p className="text-muted-foreground">Scopes: {account.grantedScopes.join(', ')}</p>
          {account.accessTokenExpiresAt === null ? null : (
            <p className="text-muted-foreground">
              Access expires {formatDay(account.accessTokenExpiresAt, 'UTC')}
            </p>
          )}
          {manageable && fresh ? (
            <div className="flex gap-2">
              {account.connectionStatus === 'revoked' ? null : account.connectionStatus ===
                'active' ? (
                <button
                  type="button"
                  onClick={() => {
                    void refresh(account)
                  }}
                >
                  Refresh this connection
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    void connect(account.provider)
                  }}
                >
                  Reconnect this account
                </button>
              )}
              <button type="button" onClick={() => setEnding(account)}>
                Disconnect this account
              </button>
            </div>
          ) : null}
        </fieldset>
      ))}

      {manageable && fresh ? (
        <div className="flex gap-2">
          {providers.map((provider) => (
            <button
              key={provider}
              type="button"
              onClick={() => {
                void connect(provider)
              }}
            >
              Connect {providerLabel(provider)}
            </button>
          ))}
        </div>
      ) : null}

      {ending === null ? null : (
        <div role="alertdialog" aria-label="Disconnect this account" className="rounded border p-4">
          <p className="text-sm">
            Disconnecting ends Clipah&apos;s permission to post as {ending.displayName}. This cannot
            be undone: reconnecting starts a new authorization.
          </p>
          <p className="text-sm">
            Scheduled publications to this account will be cancelled, and videos already published
            stay on {providerLabel(ending.provider)}.
          </p>
          <button
            type="button"
            onClick={() => {
              void disconnect(ending)
            }}
          >
            Disconnect this account
          </button>
          <button type="button" onClick={() => setEnding(null)}>
            Keep this connection
          </button>
        </div>
      )}
    </section>
  )
}

/** What state one connection is in, said the way a member would say it. */
function healthLine(account: SocialAccountResponse): string {
  if (account.connectionStatus === 'revoked') {
    return 'Access was revoked. Nothing can be published to this account.'
  }
  if (account.connectionStatus !== 'active') {
    return 'This connection has to be reconnected before it can publish again.'
  }
  if (
    account.accessTokenExpiresAt !== null &&
    Date.parse(account.accessTokenExpiresAt) <= Date.now()
  ) {
    return 'Connected, but its access has expired. Refresh it before publishing.'
  }
  return 'Connected and working.'
}

function connectionRefusal(error: unknown): string {
  if (error instanceof ApiError && error.code === 'SOCIAL_ACCOUNT_RECONNECT_REQUIRED') {
    return 'This connection has to be reconnected: the provider will not renew it.'
  }
  if (error instanceof ApiError) {
    return error.message
  }
  return 'Something went wrong. Please try again.'
}
