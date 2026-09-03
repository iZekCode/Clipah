'use client'

import { useMutation } from '@tanstack/react-query'
import { useId, useState, type FormEvent } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { createApiV1ProjectsProjectIdYoutubeImportsPost } from '@/lib/api/generated/source-imports/source-imports'

/** The hosts the backend's own allowlist accepts, checked here only to save a round trip. */
const YOUTUBE_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'])

/**
 * Import one public YouTube video into this Project.
 *
 * This is a convenience connector, not the way media is meant to arrive: the backend
 * decides what may be imported, and the refusals it sends are shown as it wrote them.
 * Nothing here signs in to YouTube — the authenticated connector is a separate,
 * feature-flagged capability, and while it is off there is no cookie control to find.
 */
export function YouTubeImportForm({
  projectId,
  onStarted,
}: {
  projectId: string
  onStarted: () => void
}) {
  const { active } = useWorkspaceScope()
  const fieldId = useId()
  const [url, setUrl] = useState('')
  const [refusal, setRefusal] = useState<string | null>(null)
  // One key per URL the member typed, so pressing the button twice is one import to the
  // backend while a different video is always a different import.
  const [attempt, setAttempt] = useState(() => crypto.randomUUID())

  const startImport = useMutation<void, ApiError, string>({
    mutationFn: async (source) => {
      await createApiV1ProjectsProjectIdYoutubeImportsPost(
        projectId,
        { url: source },
        { workspace_id: active.id },
        { headers: { 'Idempotency-Key': `youtube-import:${attempt}` } },
      )
    },
    // The Job this admitted announces itself on the Workspace stream the panel already
    // follows, so nothing here has to be told which Job to watch.
    onSuccess: onStarted,
  })

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const problem = unusableUrl(url)
    setRefusal(problem)
    if (problem === null) {
      startImport.mutate(url.trim())
    }
  }

  return (
    <section aria-label="Import from YouTube" className="space-y-2 rounded-lg border p-4">
      <h3 className="text-sm font-medium">Import from YouTube</h3>
      <p className="text-xs text-muted-foreground">
        A convenience connector for one public, non-live video you have the right to use.
        Private, members-only, and age-restricted videos cannot be imported.
      </p>
      <form onSubmit={submit} className="flex flex-wrap items-center gap-2">
        <label htmlFor={fieldId} className="sr-only">
          YouTube video URL
        </label>
        <input
          id={fieldId}
          type="text"
          value={url}
          onChange={(event) => {
            setUrl(event.target.value)
            setRefusal(null)
            setAttempt(crypto.randomUUID())
          }}
          placeholder="https://www.youtube.com/watch?v=…"
          className="min-w-64 flex-1 rounded-md border px-2 py-1 text-sm"
        />
        <button
          type="submit"
          disabled={startImport.isPending}
          className="rounded-md border px-3 py-1 text-sm"
        >
          Import video
        </button>
      </form>
      {refusal === null ? null : (
        <p role="alert" className="text-sm text-destructive">
          {refusal}
        </p>
      )}
      {startImport.isError ? <ErrorNotice error={startImport.error} /> : null}
    </section>
  )
}

/** Say why this URL cannot be imported, or nothing when the backend should decide. */
function unusableUrl(raw: string): string | null {
  let parsed: URL
  try {
    parsed = new URL(raw.trim())
  } catch {
    return 'Enter a full video address, starting with https://.'
  }
  if (parsed.protocol !== 'https:') {
    return 'The video address has to start with https://.'
  }
  if (!YOUTUBE_HOSTS.has(parsed.hostname.toLowerCase())) {
    return 'Only YouTube video addresses can be imported.'
  }
  return null
}
