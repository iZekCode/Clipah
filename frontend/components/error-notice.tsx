import { ApiError } from '@/lib/api/client'

/**
 * Show a failed request the way support can act on it: the public message the backend
 * chose, plus the request identifier that ties it to one server-side log entry.
 * Everything is rendered as React children, so a message containing markup stays text.
 */
export function ErrorNotice({ error }: { error: unknown }) {
  const message =
    error instanceof ApiError ? error.message : 'Something went wrong. Please try again.'
  const requestId = error instanceof ApiError ? error.requestId : null

  return (
    <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-4">
      <p className="text-sm font-medium text-destructive">{message}</p>
      {requestId === null ? null : (
        <p className="mt-1 text-xs text-muted-foreground">Request ID: {requestId}</p>
      )}
    </div>
  )
}
