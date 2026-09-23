'use client'

import { useQuery } from '@tanstack/react-query'
import { useCallback, useRef, useState, type SyntheticEvent } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { Checkbox } from '@/components/ui/checkbox'
import { Select } from '@/components/ui/select'
import { formatClock } from '@/lib/media/time'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api/client'
import type {
  ClipVariantListResponse,
  ClipVariantResponse,
  Platform,
} from '@/lib/api/generated/model'
import {
  createVariantsApiV1ProjectsProjectIdCandidatesCandidateIdVariantsPost,
  listCollectionApiV1ProjectsProjectIdCandidatesCandidateIdVariantsGet,
} from '@/lib/api/generated/variants/variants'

import { ContextWarnings } from './ContextWarnings'

/** The destinations a member may package a variant for. */
const PLATFORMS: Platform[] = ['tiktok', 'instagram_reels', 'youtube_shorts']

/**
 * Compare readings of one moment against a single proxy.
 *
 * No variant duplicates the source, the transcript, or an asset: a variant is a pair of
 * word IDs and a seek position, so previewing one seeks the proxy already on the page. A
 * variant that carries a warning shows it here, beside the cut, rather than behind a click
 * nobody makes.
 */
export function VariantLab({
  projectId,
  candidateId,
  workspaceId,
  proxyUrl,
  onPlaybackError,
}: {
  projectId: string
  candidateId: string
  workspaceId: string
  /** A playable media URL for the Project's proxy, not the API route that signs one. */
  proxyUrl: string | null
  onPlaybackError?: () => void
}) {
  const player = useRef<HTMLVideoElement>(null)
  const [durations, setDurations] = useState<number[]>([])
  const [platform, setPlatform] = useState<Platform>('tiktok')
  const [working, setWorking] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [chosenId, setChosenId] = useState<string | null>(null)

  const variants = useQuery<ClipVariantListResponse, ApiError>({
    queryKey: ['/api/v1/variants', workspaceId, projectId, candidateId],
    queryFn: ({ signal }) =>
      listCollectionApiV1ProjectsProjectIdCandidatesCandidateIdVariantsGet(
        projectId,
        candidateId,
        { workspace_id: workspaceId },
        { signal },
      ),
    retry: false,
  })

  const request = useCallback(async () => {
    setWorking(true)
    setFailure(null)
    try {
      await createVariantsApiV1ProjectsProjectIdCandidatesCandidateIdVariantsPost(
        projectId,
        candidateId,
        { platforms: [platform], durationsMs: durations },
        { workspace_id: workspaceId },
      )
      await variants.refetch()
    } catch (error) {
      setFailure(error as ApiError)
    } finally {
      setWorking(false)
    }
  }, [candidateId, durations, platform, projectId, variants, workspaceId])

  const found = variants.data?.variants ?? []
  const supported = variants.data?.supportedDurationsMs ?? []
  // The player only ever plays one variant's range: the chosen one, or the first.
  const chosen = found.find((variant) => variant.id === chosenId) ?? found[0] ?? null

  /** Play one variant from its start. */
  function preview(variant: ClipVariantResponse): void {
    setChosenId(variant.id)
    const element = player.current
    if (element === null) return
    element.currentTime = variant.startMs / 1_000
    void element.play()
  }

  /** Keep the player inside the chosen range: a seek outside it lands back at its start. */
  function bound(event: SyntheticEvent<HTMLVideoElement>): void {
    if (chosen === null) return
    const element = event.currentTarget
    const at = element.currentTime * 1_000
    if (at >= chosen.endMs) {
      element.pause()
      element.currentTime = chosen.startMs / 1_000
    } else if (at < chosen.startMs - 250) {
      element.currentTime = chosen.startMs / 1_000
    }
  }

  return (
    <section aria-label="Variants" className="flex flex-col gap-3 text-xs">
      <header>
        <h2 className="text-sm font-semibold">Variants</h2>
        <p className="text-muted-foreground">
          Other honest readings of this moment. Nothing here changes the clip.
        </p>
      </header>

      {proxyUrl === null || chosen === null ? null : (
        // One proxy for every variant: a variant is a boundary, not another copy, so the
        // player is held to the chosen variant's range rather than the whole source.
        <div className="space-y-1">
          <video
            ref={player}
            src={proxyUrl}
            controls
            preload="metadata"
            onLoadedMetadata={(event) => {
              event.currentTarget.currentTime = chosen.startMs / 1_000
            }}
            onTimeUpdate={bound}
            onSeeking={bound}
            onError={onPlaybackError}
            className="w-full"
          />
          <p className="text-muted-foreground">
            Previewing {Math.round(chosen.durationMs / 1_000)}s ·{' '}
            {chosen.platform.replaceAll('_', ' ')}, {formatClock(chosen.startMs)}–
            {formatClock(chosen.endMs)} of the source
          </p>
        </div>
      )}

      <fieldset className="flex flex-wrap items-center gap-2">
        <legend className="sr-only">Lengths to offer</legend>
        {supported.map((duration) => (
          <label key={duration} className="flex items-center gap-1">
            <Checkbox
              value={duration}
              checked={durations.includes(duration)}
              onChange={(event) =>
                setDurations((chosen) =>
                  event.target.checked
                    ? [...chosen, duration]
                    : chosen.filter((value) => value !== duration),
                )
              }
            />
            <span>{Math.round(duration / 1_000)}s</span>
          </label>
        ))}
        <label className="flex items-center gap-1">
          <span className="sr-only">Platform</span>
          <Select
            value={platform}
            onChange={(event) => setPlatform(event.target.value as Platform)}
            controlSize="sm"
          >
            {PLATFORMS.map((value) => (
              <option key={value} value={value}>
                {value.replaceAll('_', ' ')}
              </option>
            ))}
          </Select>
        </label>
        <button
          type="button"
          disabled={working || durations.length === 0}
          onClick={() => {
            void request()
          }}
          className="rounded-lg border bg-card px-2.5 py-1"
        >
          Offer variants
        </button>
      </fieldset>

      {failure === null ? null : <ErrorNotice error={failure} />}

      {found.length === 0 ? (
        <p className="text-muted-foreground">
          No variants yet. Choose a length to see how this moment reads shorter.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {found.map((variant) => (
            <li
              key={variant.id}
              aria-current={variant.id === chosen?.id ? 'true' : undefined}
              className={cn(
                'flex flex-col gap-1 rounded-lg border bg-card p-2',
                variant.id === chosen?.id ? 'border-primary' : 'border-input',
              )}
            >
              <p className="font-medium">
                {Math.round(variant.durationMs / 1_000)}s ·{' '}
                {variant.hookStrategy.replaceAll('_', ' ')} ·{' '}
                {variant.platform.replaceAll('_', ' ')}
              </p>
              <p className="text-muted-foreground">{variant.rationale}</p>
              <button
                type="button"
                onClick={() => preview(variant)}
                className="w-fit rounded-lg border bg-card px-2.5 py-1"
              >
                Preview this variant
              </button>
              <ContextWarnings warnings={variant.warnings} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

