'use client'

import { useQuery } from '@tanstack/react-query'
import { useRef, type SyntheticEvent } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { showApiV1ProjectsProjectIdProxyGet } from '@/lib/api/generated/playback/playback'
import type { ProxyPlaybackResponse } from '@/lib/api/generated/model'

/**
 * Play one candidate's range against the Project's proxy.
 *
 * The capability is asked for at the moment it is used and never kept: the backend signs
 * a five-minute URL, this component holds it only while the preview is open, and opening
 * the preview again asks for a new one. Playback is bounded by the candidate itself, so a
 * reviewer hears the moment being proposed rather than whatever follows it.
 */
export function ClipPreview({
  projectId,
  startMs,
  endMs,
}: {
  projectId: string
  startMs: number
  endMs: number
}) {
  const { active } = useWorkspaceScope()
  const video = useRef<HTMLVideoElement>(null)

  const playback = useQuery<ProxyPlaybackResponse, ApiError>({
    queryKey: ['/api/v1/projects/proxy', active.id, projectId],
    queryFn: ({ signal }) =>
      showApiV1ProjectsProjectIdProxyGet(projectId, { workspace_id: active.id }, { signal }),
    retry: false,
    gcTime: 0,
    staleTime: 0,
  })

  if (playback.isPending) {
    return (
      <p role="status" className="text-xs text-muted-foreground">
        Loading preview…
      </p>
    )
  }
  if (playback.isError) {
    return <ErrorNotice error={playback.error} />
  }

  /** Start at the candidate rather than at the beginning of the source. */
  function start(event: SyntheticEvent<HTMLVideoElement>) {
    const element = event.currentTarget
    element.currentTime = startMs / 1000
    void element.play()
  }

  /** Stop where the candidate ends, so the preview never runs past the proposal. */
  function stopAtEnd(event: SyntheticEvent<HTMLVideoElement>) {
    const element = event.currentTarget
    if (element.currentTime >= endMs / 1000) {
      element.pause()
      element.currentTime = startMs / 1000
    }
  }

  return (
    <video
      ref={video}
      data-testid="clip-preview-video"
      src={playback.data.url}
      controls
      preload="metadata"
      onLoadedMetadata={start}
      onTimeUpdate={stopAtEnd}
      className="mt-2 w-full max-w-md rounded-md border"
    />
  )
}
