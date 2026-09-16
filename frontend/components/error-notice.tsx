import { AlertCircle } from 'lucide-react'

import { ApiError } from '@/lib/api/client'

/**
 * Show a failed request the way support can act on it: the public message the backend
 * chose, plus the request identifier that ties it to one server-side log entry.
 * Everything is rendered as React children, so a message containing markup stays text.
 *
 * When the failure is worth trying again, `onRetry` offers that as the next step rather
 * than leaving the member to reload the page.
 */
export function ErrorNotice({
  error,
  onRetry,
}: {
  error: unknown
  onRetry?: () => void
}) {
  const message =
    error instanceof ApiError ? error.message : 'Something went wrong. Please try again.'
  const requestId = error instanceof ApiError ? error.requestId : null

  return (
    <div
      role="alert"
      className="flex gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4"
    >
      <AlertCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-destructive">{message}</p>
        {requestId === null ? null : (
          <p className="mt-1 text-xs text-muted-foreground">Request ID: {requestId}</p>
        )}
        {onRetry === undefined ? null : (
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 text-sm font-medium text-foreground underline underline-offset-4"
          >
            Try again
          </button>
        )}
      </div>
    </div>
  )
}
