'use client'

import { useCallback, useEffect, useId, useRef, useState, type ChangeEvent } from 'react'

import { Upload as UploadIcon } from 'lucide-react'

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
export interface ProjectJob {
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
export function UploadPanel({
  projectId,
  addMedia = true,
  onJob,
}: {
  projectId: string
  /** Whether this Project still needs its video; a processed Project only shows its work. */
  addMedia?: boolean
  onJob?: (job: ProjectJob) => void
}) {
  const { active } = useWorkspaceScope()

  if (!mayWriteProjects(active.role)) {
    return addMedia ? (
      <p className="text-sm text-muted-foreground">
        Only editors and above can add media to a project.
      </p>
    ) : null
  }
  return (
    <SubmissionPanel
      projectId={projectId}
      workspaceId={active.id}
      addMedia={addMedia}
      onJob={onJob}
    />
  )
}

function SubmissionPanel({
  projectId,
  workspaceId,
  addMedia,
  onJob,
}: {
  projectId: string
  workspaceId: string
  addMedia: boolean
  onJob?: (job: ProjectJob) => void
}) {
  const fieldId = useId()
  const [uploading, setUploading] = useState<UploadProgress | null>(null)
  const [uploadId, setUploadId] = useState<string | null>(null)
  const [failure, setFailure] = useState<unknown>(null)
  const [job, setJob] = useState<ProjectJob | null>(null)
  const [connected, setConnected] = useState(true)
  const [analysisPending, setAnalysisPending] = useState(false)
  const reportJob = useRef(onJob)
  reportJob.current = onJob

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
        reportJob.current?.(announced)
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
      // Completing the upload is what puts the Project on the pipeline: the backend
      // starts ingest, and transcription and analysis follow it. Asking for an analysis
      // here refused every time, because the Project has not been transcribed yet.
      setUploadId(uploaded.uploadId)
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
  const label = submissionLabel({ uploading, uploadId, analysisPending, job })
  const showStatus = uploading !== null || job !== null || uploadId !== null || analysisPending

  return (
    <section aria-label="Add media" className="space-y-4">
      {addMedia ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <section aria-label="Upload a video" className="surface space-y-3 p-5">
            <div className="flex items-center gap-3">
              <span className="flex size-9 items-center justify-center rounded-lg bg-accent text-accent-foreground">
                <UploadIcon aria-hidden="true" className="size-4" />
              </span>
              <div>
                <h3 className="text-sm font-semibold">Upload a video</h3>
                <p className="text-xs text-muted-foreground">Recommended — the most reliable way in.</p>
              </div>
            </div>
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
              className="block w-full text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-primary file:px-3 file:py-2 file:text-sm file:font-medium file:text-primary-foreground hover:file:bg-primary/90"
            />
          </section>

          <YouTubeImportForm projectId={projectId} onStarted={() => setFailure(null)} />
        </div>
      ) : null}

      <div hidden={!showStatus} className="surface space-y-3 p-5">
        <PipelineSteps job={job} uploading={uploading !== null} />
        <p role="status" className="text-sm font-medium">
          {label}
        </p>
        {uploading === null ? null : (
          <div
            role="progressbar"
            aria-label="Upload progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent(uploading)}
            className="h-2 overflow-hidden rounded-full bg-secondary"
          >
            <div
              className="h-full rounded-full bg-primary transition-[width]"
              style={{ width: `${percent(uploading)}%` }}
            />
          </div>
        )}
        {job !== null && job.errorCode !== null ? (
          <p className="text-xs text-muted-foreground">Reported as {job.errorCode}.</p>
        ) : null}
        {connected ? null : (
          <p className="text-xs text-warning">Reconnecting to live updates…</p>
        )}
        <div className="flex flex-wrap gap-2">
          {running ? (
            <button
              type="button"
              onClick={() => void stopJob(job.jobId)}
              className="rounded-lg border bg-card px-3 py-1.5 text-sm font-medium hover:bg-secondary"
            >
              Stop this job
            </button>
          ) : null}
          {uploadId !== null && !running ? (
            <button
              type="button"
              onClick={() => void findMoments(uploadId)}
              className="rounded-lg border bg-card px-3 py-1.5 text-sm font-medium hover:bg-secondary"
            >
              Find moments again
            </button>
          ) : null}
        </div>
      </div>
      {failure === null ? null : <UploadFailure failure={failure} />}
    </section>
  )
}

const PIPELINE = [
  { label: 'Import', kinds: ['source_import', 'ingest'] },
  { label: 'Transcribe', kinds: ['transcribe'] },
  { label: 'Find moments', kinds: ['analyze'] },
] as const

/**
 * The three stages a video passes through, marked only from what the backend reported.
 * No percentage is invented: a stage is done, current, or still ahead.
 */
function PipelineSteps({ job, uploading }: { job: ProjectJob | null; uploading: boolean }) {
  const current = job === null ? -1 : PIPELINE.findIndex((step) => (step.kinds as readonly string[]).includes(job.kind))
  const finished = job !== null && job.status === 'succeeded'
  if (current === -1 && !uploading) {
    return null
  }
  return (
    <ol aria-label="Processing stages" className="flex flex-wrap items-center gap-2 text-xs">
      {PIPELINE.map((step, index) => {
        const done = index < current || (index === current && finished)
        const active = index === current && !finished
        return (
          <li key={step.label} className="flex items-center gap-2">
            <span
              className={`rounded-full px-2.5 py-1 font-medium ${
                done
                  ? 'bg-success-soft text-success'
                  : active
                    ? 'bg-info-soft text-info'
                    : 'bg-secondary text-muted-foreground'
              }`}
            >
              {step.label}
              <span className="sr-only">{done ? ' (done)' : active ? ' (in progress)' : ' (not started)'}</span>
            </span>
            {index < PIPELINE.length - 1 ? <span aria-hidden="true" className="text-muted-foreground">→</span> : null}
          </li>
        )
      })}
    </ol>
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
    return 'Queued — waiting for the workspace to start'
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
