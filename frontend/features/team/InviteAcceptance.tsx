'use client'

import Link from 'next/link'
import { useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { MarketingFrame } from '@/components/marketing-frame'
import { PublicFrame } from '@/components/public-frame'
import { Button } from '@/components/ui/button'
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
      <main className="mx-auto grid w-full max-w-studio flex-1 items-center gap-12 px-4 py-12 sm:px-6 lg:min-h-[calc(100vh-8rem)] lg:grid-cols-[3fr_2fr]">
        <MarketingFrame
          still="/marketing/review.png"
          alt="Clipah's review mode"
          className="hidden lg:block"
        />
        <div className="mx-auto w-full max-w-sm space-y-4">
          <h1 className="font-display text-h1">Workspace invitation</h1>
          {error === null ? null : <ErrorNotice error={error} />}
          {member === null ? (
            <>
              <p className="text-small text-muted-foreground">
                You have been invited to work together in a Clipah workspace. Accepting adds you
                with the role the inviter chose, using the account you are signed in with now.
              </p>
              <Button type="button" className="w-full" disabled={accepting} onClick={() => void accept()}>
                {accepting ? 'Accepting…' : 'Accept invite'}
              </Button>
            </>
          ) : (
            <div role="status" className="space-y-3">
              <p>You joined as {member.role}.</p>
              <Button asChild>
                <Link href="/dashboard">Go to the workspace</Link>
              </Button>
              <Link href="/dashboard/team" className="block text-small font-semibold text-primary hover:underline">
                Open team settings
              </Link>
            </div>
          )}
        </div>
      </main>
    </PublicFrame>
  )
}
