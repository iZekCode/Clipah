'use client'

import Link from 'next/link'
import { useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
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
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-6">
      <h1 className="text-2xl font-semibold tracking-tight">Workspace invitation</h1>
      {error === null ? null : <ErrorNotice error={error} />}
      {member === null ? (
        <>
          <p className="text-sm text-muted-foreground">
            Accept this invitation using your currently signed-in Clipah account.
          </p>
          <button
            type="button"
            disabled={accepting}
            onClick={() => void accept()}
            className="w-fit rounded bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50"
          >
            {accepting ? 'Accepting…' : 'Accept invite'}
          </button>
        </>
      ) : (
        <div role="status" className="space-y-3">
          <p>You joined as {member.role}.</p>
          <Link href="/dashboard/team" className="underline">Open team settings</Link>
        </div>
      )}
    </main>
  )
}
