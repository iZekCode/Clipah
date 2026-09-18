'use client'

import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'next/navigation'
import { useEffect, useMemo, useRef, type SyntheticEvent } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { DesignedFrame } from '@/components/media/poster'
import { useTranscript } from '@/features/media/use-transcript'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { CandidateResponse, ProxyPlaybackResponse } from '@/lib/api/generated/model'
import { showApiV1ProjectsProjectIdProxyGet } from '@/lib/api/generated/playback/playback'
import { formatClock } from '@/lib/media/time'
import { overlaps, transcriptSegments } from '@/lib/media/transcript'
import { cn } from '@/lib/utils'

import { projectIsProcessing } from './status-labels'

/**
 * The moment a search result pointed at, from `?t=` in milliseconds, or null.
 *
 * A timecode a member has to find again by hand is not an answer, so a link from search
 * opens the source at the moment it named.
 */
export function useRequestedMomentMs(): number | null {
  const parameters = useSearchParams()
  const requested = parameters?.get('t') ?? null
  const startMs = requested === null ? null : Number.parseInt(requested, 10)
  return startMs !== null && Number.isFinite(startMs) && startMs >= 0 ? startMs : null
}

/**
 * The source beside its moments: the proxy player and the transcript with every moment's
 * range marked. Choosing a moment highlights its lines and moves the player to its start;
 * choosing a line moves the player to that line.
 *
 * The capability is signed when the column opens and asked for again if the player reports
 * it expired; a Project with no proxy yet shows a designed frame instead of a broken player.
 */
export function SourceColumn({
  projectId,
  status,
  candidates,
  selected,
}: {
  projectId: string
  status: string
  candidates: CandidateResponse[]
  selected: CandidateResponse | null
}) {
  const { active } = useWorkspaceScope()
  const video = useRef<HTMLVideoElement>(null)
  const transcriptList = useRef<HTMLOListElement>(null)
  const requestedMs = useRequestedMomentMs()
  const ready = status === 'ready'
  const playback = useQuery<ProxyPlaybackResponse, ApiError>({
    queryKey: ['/api/v1/projects/proxy', active.id, projectId],
    queryFn: ({ signal }) =>
      showApiV1ProjectsProjectIdProxyGet(projectId, { workspace_id: active.id }, { signal }),
    retry: false,
    gcTime: 0,
    staleTime: 0,
    enabled: ready || projectIsProcessing(status) || requestedMs !== null,
  })
  const transcript = useTranscript(projectId, { enabled: ready })
  const segments = useMemo(
    () => transcriptSegments(transcript.data?.words ?? []),
    [transcript.data],
  )

  useEffect(() => {
    if (selected === null) return
    if (video.current !== null) {
      video.current.currentTime = selected.startMs / 1000
    }
    const line = transcriptList.current?.querySelector('[aria-current="true"]')
    if (line !== null && line !== undefined && typeof line.scrollIntoView === 'function') {
      line.scrollIntoView({ block: 'nearest' })
    }
  }, [selected])

  /** Start where a search result pointed rather than at the top of the source. */
  function start(event: SyntheticEvent<HTMLVideoElement>): void {
    if (requestedMs !== null && selected === null) {
      event.currentTarget.currentTime = requestedMs / 1000
    }
  }

  return (
    <aside aria-label="Source" className="space-y-4 lg:sticky lg:top-[68px] lg:self-start">
      {requestedMs === null ? null : (
        <h2 className="text-small font-medium">The moment you searched for</h2>
      )}
      <div className="relative aspect-video overflow-hidden rounded-lg bg-stage">
        {playback.data === undefined ? (
          <DesignedFrame />
        ) : (
          <video
            ref={video}
            data-testid="transcript-moment-video"
            src={playback.data.url}
            controls
            preload="metadata"
            onLoadedMetadata={start}
            onError={() => void playback.refetch()}
            className="absolute inset-0 size-full object-contain"
          />
        )}
      </div>
      {playback.isError && playback.error.status !== 404 ? (
        <ErrorNotice error={playback.error} />
      ) : null}
      {!ready ? null : transcript.isError ? (
        transcript.error.status === 404 ? null : (
          <ErrorNotice error={transcript.error} />
        )
      ) : (
        <ol
          ref={transcriptList}
          aria-label="Transcript"
          className="max-h-[50vh] space-y-1 overflow-y-auto rounded-lg border bg-card p-2 lg:max-h-[calc(100vh-24rem)]"
        >
          {segments.map((segment) => {
            const inSelected =
              selected !== null && overlaps(segment, selected.startMs, selected.endMs)
            const inAny = candidates.some((entry) =>
              overlaps(segment, entry.startMs, entry.endMs),
            )
            return (
              <li
                key={`${segment.startMs}-${segment.speaker}`}
                aria-current={inSelected ? 'true' : undefined}
              >
                <button
                  type="button"
                  onClick={() => {
                    if (video.current !== null) video.current.currentTime = segment.startMs / 1000
                  }}
                  className={cn(
                    'grid w-full grid-cols-[3rem_minmax(0,1fr)] gap-2 rounded-md px-2 py-1.5 text-left text-small transition-colors duration-fast ease-signal',
                    inSelected
                      ? 'bg-primary-soft text-foreground'
                      : inAny
                        ? 'text-foreground hover:bg-secondary'
                        : 'text-muted-foreground hover:bg-secondary',
                  )}
                >
                  <span className="tabular font-mono text-caption text-subtle-foreground">
                    {formatClock(segment.startMs)}
                  </span>
                  <span
                    className={cn(
                      inAny && !inSelected && 'underline decoration-line-strong underline-offset-4',
                    )}
                  >
                    {segment.text}
                  </span>
                </button>
              </li>
            )
          })}
        </ol>
      )}
    </aside>
  )
}
