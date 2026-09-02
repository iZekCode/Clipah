'use client'

import Link from 'next/link'
import type { ReactNode } from 'react'

import { ErrorNotice } from '@/components/error-notice'

import { isUnauthenticated, useSession } from './session'

/**
 * Show private content only once the backend has confirmed a Session.
 *
 * Nothing is guessed from the browser: until `/api/v1/me` answers, the page says it is
 * still checking, and a refusal turns into an invitation to sign in rather than an
 * empty Workspace that looks like data loss.
 */
export function RequireSession({ children }: { children: ReactNode }) {
  const session = useSession()

  if (session.isPending) {
    return (
      <p role="status" className="p-6 text-sm text-muted-foreground">
        Checking your session…
      </p>
    )
  }

  if (session.isError) {
    if (!isUnauthenticated(session.error)) {
      return <ErrorNotice error={session.error} />
    }
    return (
      <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-6">
        <h1 className="text-2xl font-semibold tracking-tight">Sign in to continue</h1>
        <p className="text-sm text-muted-foreground">
          This area belongs to a Workspace, so it opens only for a signed-in member.
        </p>
        <Link
          href="/signin"
          className="inline-flex w-fit rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          Sign in
        </Link>
      </div>
    )
  }

  return <>{children}</>
}
