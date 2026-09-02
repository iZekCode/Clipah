'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'

/** Every event type a Job appends to its own history. */
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

const STAGE_LABELS: Record<string, string> = {
  queued: 'Queued',
  importing: 'Importing',
  transcribing: 'Transcribing',
  analyzing: 'Finding moments',
  rendering: 'Rendering',
  publishing: 'Publishing',
}

const STATUS_LABELS: Record<string, string> = {
  queued: 'Waiting',
  running: 'Working',
  succeeded: 'Finished',
  failed: 'Failed',
  canceled: 'Canceled',
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
export function JobCenter() {
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

  return (
    <section aria-label="Job center" className="space-y-2">
      <h2 className="text-sm font-medium">Activity</h2>
      {jobs.length === 0 ? (
        <p className="text-xs text-muted-foreground">Nothing is running.</p>
      ) : (
        <ul className="space-y-2">
          {jobs.map((job) => (
            <li key={job.jobId} className="rounded-md border px-3 py-2 text-xs">
              <p>{stageLabel(job)}</p>
              <p className="text-muted-foreground">{statusLabel(job.status)}</p>
              {job.projectId === null ? null : (
                <Link href={`/dashboard/projects/${job.projectId}`}>Open project</Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** Name the work being done, or the outcome once there is nothing left to do. */
function stageLabel(job: AnnouncedJob): string {
  if (TERMINAL_STATUSES.has(job.status)) {
    return `${job.kind} · ${Math.round(job.progress * 100)}%`
  }
  return STAGE_LABELS[job.stage] ?? job.stage
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
    kind: typeof kind === 'string' ? kind : 'job',
    status: typeof status === 'string' ? status : 'running',
    stage: typeof stage === 'string' ? stage : '',
    progress: typeof progress === 'number' ? progress : 0,
  }
}
