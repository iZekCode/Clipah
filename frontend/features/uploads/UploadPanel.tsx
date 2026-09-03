'use client'

import { useCallback, useEffect, useId, useState, type ChangeEvent } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { mayWriteProjects } from '@/features/workspaces/roles'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import { ApiError } from '@/lib/api/client'
import { createApiV1ProjectsProjectIdAnalysisPost } from '@/lib/api/generated/analysis/analysis'
import { cancelApiV1JobsJobIdCancelPost } from '@/lib/api/generated/jobs/jobs'

import { UploadRejectedError, uploadSource } from './uploader'
import { YouTubeImportForm } from './YouTubeImportForm'

/** Every frame the Workspace stream may carry about a Job of this Project. */
const JOB_EVENT_TYPES = [
  'created',
  'started',
  'progress',
  'retrying',
  'cancel_requested',
  'succeeded',
  'failed',
  'canceled',
] as const

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'canceled'])

/** What each kind of work is called while it is still being done. */
const KIND_LABELS: Record<string, string> = {
  source_import: 'Importing media',
  ingest: 'Importing media',
  transcribe: 'Transcribing audio',
  analyze: 'Finding moments',
}

/** One Job of this Project, as the stream last described it. */
interface ProjectJob {
  jobId: string
  kind: string
  status: string
  attempt: number
  errorCode: string | null
}

/** How far the browser has got with the file the member picked. */
interface UploadProgress {
  uploadedBytes: number
  totalBytes: number
}

/**
 * Get media into one Project, and say honestly what is happening to it.
 *
 * Direct upload is the way in: it is the first thing on the page, it resumes after a
 * refresh, and it never hands the browser a capability it has to keep. Public YouTube
 * import sits underneath as a convenience. Once media has arrived, everything shown here
 * is the backend's own account of the work, read from the Workspace event stream, so this
 * panel never guesses at a stage that has not been reported.
 */
export function UploadPanel({ projectId }: { projectId: string }) {
  const { active } = useWorkspaceScope()

  if (!mayWriteProjects(active.role)) {
    return (
      <p className="text-sm text-muted-foreground">
        Only editors and above can add media to a project.
      </p>
    )
  }
  return <SubmissionPanel projectId={projectId} workspaceId={active.id} />
}

function SubmissionPanel({
  projectId,
  workspaceId,
}: {
  projectId: string
  workspaceId: string
}) {
  const fieldId = useId()
  const [uploading, setUploading] = useState<UploadProgress | null>(null)
  const [uploadId, setUploadId] = useState<string | null>(null)
  const [failure, setFailure] = useState<unknown>(null)
  const [job, setJob] = useState<ProjectJob | null>(null)
  const [connected, setConnected] = useState(true)
  const [analysisPending, setAnalysisPending] = useState(false)

  useEffect(() => {
    setJob(null)
    const stream = new EventSource(`/api/v1/jobs/events?workspace_id=${workspaceId}`, {
      withCredentials: true,
    })
    // The browser reconnects on its own and replays from `Last-Event-ID`, so a dropped
    // connection resumes this Project's history instead of starting it over.
    const onFrame = (event: Event) => {
      setConnected(true)
      const announced = readJob(event, projectId)
      if (announced !== null) {
        setJob(announced)
      }
    }
    const onDrop = () => setConnected(false)
    for (const type of JOB_EVENT_TYPES) {
      stream.addEventListener(type, onFrame)
    }
    stream.addEventListener('error', onDrop)

    return () => {
      for (const type of JOB_EVENT_TYPES) {
        stream.removeEventListener(type, onFrame)
      }
      stream.removeEventListener('error', onDrop)
      stream.close()
    }
  }, [projectId, workspaceId])

  /** Ask for the moments in media the object store already holds. */
  const findMoments = useCallback(
    async (forUploadId: string) => {
      setAnalysisPending(true)
      try {
        await createApiV1ProjectsProjectIdAnalysisPost(
          projectId,
          { workspace_id: workspaceId },
          { headers: { 'Idempotency-Key': `analysis:${forUploadId}` } },
        )
      } catch (error) {
        setFailure(error)
      } finally {
        setAnalysisPending(false)
      }
    },
    [projectId, workspaceId],
  )

  async function pick(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (file === undefined) {
      return
    }
    setFailure(null)
    setUploadId(null)
    setUploading({ uploadedBytes: 0, totalBytes: file.size })
    try {
      const uploaded = await uploadSource({
        file,
        projectId,
        workspaceId,
        onProgress: (uploadedBytes, totalBytes) => setUploading({ uploadedBytes, totalBytes }),
      })
      setUploadId(uploaded.uploadId)
      await findMoments(uploaded.uploadId)
    } catch (error) {
      setFailure(error)
    } finally {
      setUploading(null)
    }
  }

  async function stopJob(jobId: string) {
    try {
      await cancelApiV1JobsJobIdCancelPost(jobId, { workspace_id: workspaceId })
    } catch (error) {
      setFailure(error)
    }
  }

  const running = job !== null && !TERMINAL_STATUSES.has(job.status)

  return (
    <section aria-label="Add media" className="space-y-4">
      <section aria-label="Upload a video" className="space-y-2 rounded-lg border p-4">
        <h3 className="text-sm font-medium">Upload a video</h3>
        <p className="text-xs text-muted-foreground">
          The file goes straight to storage in parts, and picking it again after a refresh
          carries on where it stopped.
        </p>
        <label htmlFor={fieldId} className="block text-sm font-medium">
          Video file
        </label>
        <input
          id={fieldId}
          type="file"
          accept="video/*"
          onChange={(event) => void pick(event)}
          disabled={uploading !== null}
          className="block text-sm"
        />
      </section>

      <YouTubeImportForm projectId={projectId} onStarted={() => setFailure(null)} />

      <p role="status" className="text-sm">
        {submissionLabel({ uploading, uploadId, analysisPending, job })}
      </p>
      {job !== null && job.errorCode !== null ? (
        <p className="text-xs text-muted-foreground">Reported as {job.errorCode}.</p>
      ) : null}
      {connected ? null : (
        <p className="text-xs text-muted-foreground">Reconnecting to live updates…</p>
      )}
      {running ? (
        <button
          type="button"
          onClick={() => void stopJob(job.jobId)}
          className="rounded-md border px-3 py-1 text-sm"
        >
          Stop this job
        </button>
      ) : null}
      {uploadId !== null && !running ? (
        <button
          type="button"
          onClick={() => void findMoments(uploadId)}
          className="rounded-md border px-3 py-1 text-sm"
        >
          Find moments again
        </button>
      ) : null}
      {failure === null ? null : <UploadFailure failure={failure} />}
    </section>
  )
}

/** Show a refusal the browser made itself, or one the backend sent, in its own words. */
function UploadFailure({ failure }: { failure: unknown }) {
  if (failure instanceof ApiError) {
    return <ErrorNotice error={failure} />
  }
  const message =
    failure instanceof UploadRejectedError
      ? failure.message
      : 'That upload could not be finished. Try again.'
  return (
    <p role="alert" className="text-sm text-destructive">
      {message}
    </p>
  )
}

/** Name the one thing this Project is waiting on, as far as anyone has been told. */
function submissionLabel({
  uploading,
  uploadId,
  analysisPending,
  job,
}: {
  uploading: UploadProgress | null
  uploadId: string | null
  analysisPending: boolean
  job: ProjectJob | null
}): string {
  if (uploading !== null) {
    return `Uploading video — ${percent(uploading)}%`
  }
  if (job !== null) {
    return jobLabel(job)
  }
  if (analysisPending || uploadId !== null) {
    return 'Queued'
  }
  return 'Nothing has been submitted yet.'
}

/** What one Job's own status and kind mean to the person waiting on it. */
function jobLabel(job: ProjectJob): string {
  if (job.status === 'retrying') {
    return `Retrying — attempt ${job.attempt}`
  }
  if (job.status === 'canceled') {
    return 'Canceled'
  }
  if (job.status === 'failed') {
    return 'Failed'
  }
  const kind = KIND_LABELS[job.kind] ?? job.kind
  if (job.status === 'succeeded') {
    return job.kind === 'analyze' ? 'Ready to review' : `${kind} finished`
  }
  if (job.status === 'cancel_requested') {
    return `${kind} — stopping`
  }
  return kind
}

/** How much of the file has reached storage, rounded the way a member reads it. */
function percent({ uploadedBytes, totalBytes }: UploadProgress): number {
  if (totalBytes === 0) {
    return 0
  }
  return Math.round((uploadedBytes / totalBytes) * 100)
}

/** Read one frame, ignoring every Job that belongs to another Project. */
function readJob(event: Event, projectId: string): ProjectJob | null {
  if (!(event instanceof MessageEvent) || typeof event.data !== 'string') {
    return null
  }
  let payload: unknown
  try {
    payload = JSON.parse(event.data)
  } catch {
    return null
  }
  if (typeof payload !== 'object' || payload === null) {
    return null
  }
  const fields = payload as Record<string, unknown>
  if (typeof fields['jobId'] !== 'string' || fields['projectId'] !== projectId) {
    return null
  }
  return {
    jobId: fields['jobId'],
    kind: typeof fields['kind'] === 'string' ? fields['kind'] : 'job',
    status: typeof fields['status'] === 'string' ? fields['status'] : 'running',
    attempt: typeof fields['attempt'] === 'number' ? fields['attempt'] : 1,
    errorCode: typeof fields['errorCode'] === 'string' ? fields['errorCode'] : null,
  }
}
