'use client'

import Link from 'next/link'
import { useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { PublicFrame } from '@/components/public-frame'
import { RequireSession } from '@/features/auth/require-session'
import type { ApiError } from '@/lib/api/client'
import { acceptWorkspaceInviteApiV1WorkspaceInvitesTokenAcceptPost } from '@/lib/api/generated/workspace-memberships/workspace-memberships'
import type { MemberResponse } from '@/lib/api/generated/model'

/** Bind one bearer invitation to the User whose authenticated Session is present. */
export function InviteAcceptance({ token }: { token: string }) {
  return (
    <RequireSession>
      <InviteAcceptanceBody token={token} />
    </RequireSession>
  )
}

function InviteAcceptanceBody({ token }: { token: string }) {
  const [member, setMember] = useState<MemberResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [accepting, setAccepting] = useState(false)

  async function accept() {
    setAccepting(true)
    setError(null)
    try {
      setMember(await acceptWorkspaceInviteApiV1WorkspaceInvitesTokenAcceptPost(token))
    } catch (cause) {
      setError(cause as ApiError)
    } finally {
      setAccepting(false)
    }
  }

  return (
    <PublicFrame signInLink={false}>
    <main className="flex flex-1 items-center justify-center px-6 py-12">
      <div className="surface w-full max-w-md space-y-4 p-8 shadow-md">
      <h1 className="text-2xl font-semibold tracking-tight">Workspace invitation</h1>
      {error === null ? null : <ErrorNotice error={error} />}
      {member === null ? (
        <>
          <p className="text-sm text-muted-foreground">
            You have been invited to work together in a Clipah workspace. Accepting adds you
            with the role the inviter chose, using the account you are signed in with now.
          </p>
          <button
            type="button"
            disabled={accepting}
            onClick={() => void accept()}
            className="h-10 w-full rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-50"
          >
            {accepting ? 'Accepting…' : 'Accept invite'}
          </button>
        </>
      ) : (
        <div role="status" className="space-y-3">
          <p>You joined as {member.role}.</p>
          <Link
            href="/dashboard"
            className="inline-flex h-10 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
          >
            Go to the workspace
          </Link>
          <Link href="/dashboard/team" className="block text-sm font-medium text-primary hover:underline">
            Open team settings
          </Link>
        </div>
      )}
      </div>
    </main>
    </PublicFrame>
  )
}
