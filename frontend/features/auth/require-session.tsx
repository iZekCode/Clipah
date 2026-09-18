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
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="w-full max-w-sm space-y-4">
          <h1 className="font-display text-h1">Sign in to continue</h1>
          <p className="text-small text-muted-foreground">Sign in to open your studio.</p>
          <Link
            href="/signin"
            className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-small font-semibold text-primary-foreground transition-colors duration-fast ease-signal hover:bg-primary-hover"
          >
            Sign in
          </Link>
        </div>
      </div>
    )
  }

  return <>{children}</>
}
