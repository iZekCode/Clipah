'use client'

import { useQuery } from '@tanstack/react-query'
import { useCallback, useRef, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
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
}: {
  projectId: string
  candidateId: string
  workspaceId: string
  proxyUrl: string | null
}) {
  const player = useRef<HTMLVideoElement>(null)
  const [durations, setDurations] = useState<number[]>([])
  const [platform, setPlatform] = useState<Platform>('tiktok')
  const [working, setWorking] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)

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

  return (
    <section aria-label="Variants" className="flex flex-col gap-3 text-xs">
      <header>
        <h2 className="text-sm font-semibold">Variants</h2>
        <p className="text-muted-foreground">
          Other honest readings of this moment. Nothing here changes the clip.
        </p>
      </header>

      {proxyUrl === null ? null : (
        // One proxy for every variant: a variant is a boundary, not another copy.
        <video ref={player} src={proxyUrl} controls preload="metadata" className="w-full" />
      )}

      <fieldset className="flex flex-wrap items-center gap-2">
        <legend className="sr-only">Lengths to offer</legend>
        {supported.map((duration) => (
          <label key={duration} className="flex items-center gap-1">
            <input
              type="checkbox"
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
          <select
            value={platform}
            onChange={(event) => setPlatform(event.target.value as Platform)}
            className="rounded border px-2 py-1"
          >
            {PLATFORMS.map((value) => (
              <option key={value} value={value}>
                {value.replaceAll('_', ' ')}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={working || durations.length === 0}
          onClick={() => {
            void request()
          }}
          className="rounded border px-2 py-1"
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
            <li key={variant.id} className="flex flex-col gap-1 rounded border p-2">
              <p className="font-medium">
                {Math.round(variant.durationMs / 1_000)}s ·{' '}
                {variant.hookStrategy.replaceAll('_', ' ')} ·{' '}
                {variant.platform.replaceAll('_', ' ')}
              </p>
              <p className="text-muted-foreground">{variant.rationale}</p>
              <button
                type="button"
                onClick={() => seek(player.current, variant)}
                className="w-fit rounded border px-2 py-1"
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

/** Move the one proxy to where this variant begins. */
function seek(player: HTMLVideoElement | null, variant: ClipVariantResponse): void {
  if (player === null) {
    return
  }
  player.currentTime = variant.startMs / 1_000
}
