'use client'

import { AlertCircle } from 'lucide-react'

import { ApiError } from '@/lib/api/client'
import { notify } from '@/lib/notify'

/**
 * A failed request, said plainly, with its support reference one click away.
 *
 * The public message is the sentence; the code and request identifier sit in small mono
 * type and are copied together, which is what support needs to find the log entry.
 * Everything is rendered as React children, so a message containing markup stays text.
 */
export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof ApiError ? error.message : 'Something went wrong. Try again.'
  const reference =
    error instanceof ApiError && error.requestId ? `${error.code} ${error.requestId}` : null

  async function copy(): Promise<void> {
    if (reference === null) return
    try {
      await navigator.clipboard.writeText(reference)
      notify.success('Details copied')
    } catch {
      notify.info(reference)
    }
  }

  return (
    <div role="alert" className="flex gap-3 rounded-lg border border-destructive/40 bg-destructive-soft p-4">
      <AlertCircle aria-hidden="true" strokeWidth={1.75} className="mt-0.5 size-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1 space-y-2">
        <p className="text-small font-medium text-foreground">{message}</p>
        <div className="flex flex-wrap items-center gap-3">
          {onRetry === undefined ? null : (
            <button type="button" onClick={onRetry} className="text-small font-semibold text-primary hover:underline">
              Try again
            </button>
          )}
          {reference === null ? null : (
            <>
              <button
                type="button"
                onClick={() => void copy()}
                className="text-small font-medium text-muted-foreground hover:text-foreground"
              >
                Copy details
              </button>
              <p className="font-mono text-caption text-subtle-foreground">
                Ref {error instanceof ApiError ? error.requestId : ''}
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
