'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'

import { Poster } from '@/components/media/poster'
import { StatusBadge, type StatusTone } from '@/components/status-badge'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import { cancelApiV1JobsJobIdCancelPost } from '@/lib/api/generated/jobs/jobs'
import { notify } from '@/lib/notify'

/** Every event type a Job appends to its own history. */
export const JOB_EVENT_TYPES = [
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

/** The steps inside a Job, in plain words. A step not named here is never shown raw. */
const STAGE_LABELS: Record<string, string> = {
  importing: 'Importing',
  transcribing: 'Transcribing',
  analyzing: 'Finding moments',
  rendering: 'Rendering',
  publishing: 'Publishing',
  download: 'Downloading the video',
  probe: 'Reading the video',
  proxy: 'Making a preview copy',
  transcription_audio: 'Extracting the audio',
  thumbnail: 'Making a thumbnail',
  upload: 'Saving',
  render: 'Rendering',
}

/** What each kind of work is called in plain words. */
export const JOB_KIND_LABELS: Record<string, string> = {
  source_import: 'Importing video',
  ingest: 'Preparing video',
  transcribe: 'Transcribing',
  analyze: 'Finding moments',
  broll_plan: 'Planning B-roll',
  broll_retrieve: 'Finding B-roll',
  broll_generate: 'Generating B-roll',
  render: 'Exporting clip',
  campaign_generate: 'Writing campaign copy',
  social_rendition: 'Preparing for publishing',
  social_publish: 'Publishing',
  social_reconcile: 'Checking publication',
  cleanup: 'Cleaning up',
  preview_media: 'Preparing previews',
  clip_posters: 'Preparing posters',
  clip_cover: 'Drawing a cover',
}

const STATUS_LABELS: Record<string, string> = {
  queued: 'Waiting',
  running: 'Working',
  retrying: 'Retrying',
  cancel_requested: 'Stopping',
  succeeded: 'Finished',
  failed: 'Failed',
  canceled: 'Canceled',
}

const STATUS_TONES: Record<string, StatusTone> = {
  queued: 'neutral',
  running: 'progress',
  retrying: 'attention',
  cancel_requested: 'attention',
  succeeded: 'success',
  failed: 'danger',
  canceled: 'neutral',
}

/** One Job the center is announcing, as the stream last described it. */
interface AnnouncedJob {
  jobId: string
  projectId: string | null
  kind: string
  status: string
  stage: string
  progress: number
}

/**
 * Follow every Job of the active Workspace on one Server-Sent Events stream.
 *
 * The stream outlives the Jobs it announces, so finished work stays in the list for this
 * Workspace instead of vanishing the moment it succeeds. Nothing survives a Workspace
 * switch: the connection is closed and the list emptied, so one Workspace's work is never
 * shown while another is open.
 */
export function JobCenter({
  onActiveCountChange,
}: {
  onActiveCountChange?: (count: number) => void
} = {}) {
  const { active } = useWorkspaceScope()
  const [jobs, setJobs] = useState<AnnouncedJob[]>([])

  useEffect(() => {
    setJobs([])
    const stream = new EventSource(`/api/v1/jobs/events?workspace_id=${active.id}`, {
      withCredentials: true,
    })
    // The browser reconnects on its own and replays from `Last-Event-ID`, so a dropped
    // connection resumes the Workspace history rather than starting it over.
    const listeners = JOB_EVENT_TYPES.map((type) => {
      const listener = (event: MessageEvent) => {
        const announced = readJob(event.data)
        if (announced !== null) {
          setJobs((known) => withAnnouncedJob(known, announced))
        }
      }
      stream.addEventListener(type, listener)
      return { type, listener }
    })

    return () => {
      for (const { type, listener } of listeners) {
        stream.removeEventListener(type, listener)
      }
      stream.close()
    }
  }, [active.id])

  const running = jobs.filter((job) => !TERMINAL_STATUSES.has(job.status)).length
  useEffect(() => {
    onActiveCountChange?.(running)
  }, [onActiveCountChange, running])

  // Newest work first: what a creator just started is what they are looking for.
  const ordered = [...jobs].reverse()

  async function stop(job: AnnouncedJob): Promise<void> {
    try {
      await cancelApiV1JobsJobIdCancelPost(job.jobId, { workspace_id: active.id })
    } catch (error) {
      notify.failure(error)
    }
  }

  return (
    <section aria-label="Job center" className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-title">Activity</h2>
        <span className="font-mono text-caption text-subtle-foreground">
          {running === 0 ? 'All caught up' : `${running} running`}
        </span>
      </div>
      {jobs.length === 0 ? (
        <p className="text-small text-muted-foreground">Nothing is running.</p>
      ) : (
        <ul className="space-y-2">
          {ordered.map((job) => (
            <li key={job.jobId} className="flex gap-3 rounded-md border border-border bg-card px-3 py-2.5">
              {job.projectId === null ? null : (
                <div className="relative aspect-video w-16 shrink-0 overflow-hidden rounded-sm">
                  <Poster projectId={job.projectId} />
                </div>
              )}
              <div className="min-w-0 flex-1 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-small font-medium">{stageLabel(job)}</p>
                    {stepLabel(job) === null ? null : (
                      <p className="text-caption text-muted-foreground">{stepLabel(job)}</p>
                    )}
                  </div>
                  <StatusBadge tone={STATUS_TONES[job.status] ?? 'neutral'}>{statusLabel(job.status)}</StatusBadge>
                </div>
                <div className="flex items-center gap-3 text-caption">
                  {job.projectId === null ? null : (
                    <Link href={`/dashboard/projects/${job.projectId}`} className="font-medium text-primary hover:underline">
                      Open project
                    </Link>
                  )}
                  {TERMINAL_STATUSES.has(job.status) || job.status === 'cancel_requested' ? null : (
                    <button
                      type="button"
                      onClick={() => void stop(job)}
                      aria-label={`Stop ${stageLabel(job)}`}
                      className="font-medium text-muted-foreground hover:text-destructive"
                    >
                      Stop
                    </button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** Name the kind of work, never an internal identifier. */
function stageLabel(job: AnnouncedJob): string {
  return JOB_KIND_LABELS[job.kind] ?? 'Background work'
}

/** The step a running Job is on, when it has plain words and says more than the title. */
function stepLabel(job: AnnouncedJob): string | null {
  if (TERMINAL_STATUSES.has(job.status)) return null
  const step = STAGE_LABELS[job.stage]
  return step === undefined || step === stageLabel(job) ? null : step
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

/** Keep the newest description of each Job, in the order the Workspace first saw them. */
function withAnnouncedJob(known: AnnouncedJob[], announced: AnnouncedJob): AnnouncedJob[] {
  if (!known.some((job) => job.jobId === announced.jobId)) {
    return [...known, announced]
  }
  return known.map((job) => (job.jobId === announced.jobId ? announced : job))
}

/** Read one event body, ignoring anything that is not a Job the center can announce. */
function readJob(data: unknown): AnnouncedJob | null {
  if (typeof data !== 'string') {
    return null
  }
  let payload: unknown
  try {
    payload = JSON.parse(data)
  } catch {
    return null
  }
  if (typeof payload !== 'object' || payload === null) {
    return null
  }
  const { jobId, projectId, kind, status, stage, progress } = payload as Record<string, unknown>
  if (typeof jobId !== 'string') {
    return null
  }
  return {
    jobId,
    projectId: typeof projectId === 'string' ? projectId : null,
    kind: typeof kind === 'string' ? kind : '',
    status: typeof status === 'string' ? status : 'running',
    stage: typeof stage === 'string' ? stage : '',
    progress: typeof progress === 'number' ? progress : 0,
  }
}
