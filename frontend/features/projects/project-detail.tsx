'use client'

import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'next/navigation'
import { useRef, type SyntheticEvent } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { ClipList } from '@/features/clips/ClipList'
import { UploadPanel } from '@/features/uploads/UploadPanel'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { showApiV1ProjectsProjectIdProxyGet } from '@/lib/api/generated/playback/playback'
import { showApiV1ProjectsProjectIdGet } from '@/lib/api/generated/projects/projects'
import type { ProjectResponse, ProxyPlaybackResponse } from '@/lib/api/generated/model'

import { projectStatusLabel } from './status-labels'

/**
 * Show one Project of the active Workspace.
 *
 * A Project belonging to another Workspace is refused exactly like a Project that never
 * existed, so this renders the backend's answer as it stands and never explains the
 * difference between "not yours" and "not there".
 */
export function ProjectDetail({ projectId }: { projectId: string }) {
  const { active } = useWorkspaceScope()
  const project = useQuery<ProjectResponse, ApiError>({
    queryKey: ['/api/v1/projects', active.id, projectId],
    queryFn: ({ signal }) =>
      showApiV1ProjectsProjectIdGet(projectId, { workspace_id: active.id }, { signal }),
    retry: false,
  })

  if (project.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading project…
      </p>
    )
  }
  if (project.isError) {
    return <ErrorNotice error={project.error} />
  }

  return (
    <article className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">{project.data.name}</h1>
        <p className="text-sm text-muted-foreground">{projectStatusLabel(project.data.status)}</p>
      </div>
      <TranscriptMoment projectId={projectId} />
      <UploadPanel projectId={projectId} />
      <ClipList projectId={projectId} />
    </article>
  )
}


/**
 * Open the Project's proxy at the moment a search result pointed at.
 *
 * A transcript result is a timecode, and a timecode a member has to find again by hand is
 * not an answer. The capability is signed when the moment is opened and never kept: the
 * URL lives five minutes, and arriving here again asks the backend for a new one.
 */
function TranscriptMoment({ projectId }: { projectId: string }) {
  const { active } = useWorkspaceScope()
  const parameters = useSearchParams()
  const video = useRef<HTMLVideoElement>(null)
  const requested = parameters?.get('t') ?? null
  const startMs = requested === null ? null : Number.parseInt(requested, 10)
  const opensAMoment = startMs !== null && Number.isFinite(startMs) && startMs >= 0

  const playback = useQuery<ProxyPlaybackResponse, ApiError>({
    queryKey: ['/api/v1/projects/proxy', active.id, projectId],
    queryFn: ({ signal }) =>
      showApiV1ProjectsProjectIdProxyGet(projectId, { workspace_id: active.id }, { signal }),
    enabled: opensAMoment,
    retry: false,
    gcTime: 0,
    staleTime: 0,
  })

  if (!opensAMoment) {
    return null
  }
  if (playback.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Opening that moment…
      </p>
    )
  }
  if (playback.isError) {
    return <ErrorNotice error={playback.error} />
  }

  /** Start where the search result pointed rather than at the top of the source. */
  function start(event: SyntheticEvent<HTMLVideoElement>) {
    event.currentTarget.currentTime = (startMs ?? 0) / 1000
  }

  return (
    <section className="space-y-2">
      <h2 className="text-sm font-medium">The moment you searched for</h2>
      <video
        ref={video}
        data-testid="transcript-moment-video"
        src={playback.data.url}
        controls
        preload="metadata"
        onLoadedMetadata={start}
        className="w-full max-w-md rounded-md border"
      />
    </section>
  )
}
