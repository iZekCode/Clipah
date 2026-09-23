'use client'

import { useQuery } from '@tanstack/react-query'
import { Eye, Film } from 'lucide-react'
import { useContext, useState, type PointerEvent } from 'react'

import { CardFocusContext } from '@/components/media/card-focus'
import { useInView } from '@/features/media/use-in-view'
import { useReducedMotion } from '@/features/media/use-reduced-motion'
import { SIGNED_MEDIA_STALE_MS, useStoryboard } from '@/features/media/use-storyboard'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { MediaPreviewResponse } from '@/lib/api/generated/model'
import { thumbnailApiV1ProjectsProjectIdThumbnailGet } from '@/lib/api/generated/studio/studio'
import { posterTimeMs, scrubTimeMs, tileAt } from '@/lib/media/storyboard'
import { formatClock } from '@/lib/media/time'
import { cn } from '@/lib/utils'

import { SpriteFrame } from './sprite-frame'

const ASPECTS = { video: 16 / 9, portrait: 9 / 16 } as const

/**
 * A picture of a Project or one of its clips.
 *
 * It draws a storyboard tile, scrubs across the clip under the pointer, and shows the middle
 * of the clip while its card has keyboard focus. Without a storyboard it falls back to the
 * Project thumbnail, and without that to a designed frame. Everything on it is decoration:
 * the card around it carries the name and the link.
 */
export function Poster({
  projectId,
  startMs,
  endMs,
  aspect = 'video',
  hasMedia = true,
  rank,
  score,
  durationMs,
  hook,
  fullHook = false,
  cornerReserved = false,
  largeBadges = false,
  imageUrl,
  hoverOverlay = false,
  className,
}: {
  projectId: string
  startMs?: number
  endMs?: number
  aspect?: 'video' | 'portrait'
  hasMedia?: boolean
  rank?: number
  /** The analysis score from 0 to 1, shown out of 100 beside the rank. */
  score?: number
  durationMs?: number
  hook?: string
  /** Show the whole hook rather than three lines of it, for cards wide enough to hold it. */
  fullHook?: boolean
  /** The card puts a control on the top-right corner, so the length moves left of it. */
  cornerReserved?: boolean
  /** Rank, score, and length as tall as a small icon button, for cards that carry one. */
  largeBadges?: boolean
  /** A sharp picture drawn for this clip; the storyboard tile stands in until it loads. */
  imageUrl?: string
  /**
   * Darken the picture and show an eye while the card is hovered, to say it opens. Only the
   * picture darkens: the badges and the hook are drawn above the wash.
   */
  hoverOverlay?: boolean
  className?: string
}) {
  const { active } = useWorkspaceScope()
  const [frame, visible] = useInView<HTMLDivElement>()
  const reducedMotion = useReducedMotion()
  const cardFocused = useContext(CardFocusContext)
  const [scrubMs, setScrubMs] = useState<number | null>(null)
  const [brokenThumbnail, setBrokenThumbnail] = useState(false)
  const [brokenImage, setBrokenImage] = useState<string | null>(null)
  const sharp = imageUrl !== undefined && brokenImage !== imageUrl

  const storyboard = useStoryboard(projectId, { enabled: hasMedia && visible })
  const thumbnail = useQuery<MediaPreviewResponse, ApiError>({
    queryKey: ['/api/v1/projects/thumbnail', active.id, projectId],
    queryFn: ({ signal }) =>
      thumbnailApiV1ProjectsProjectIdThumbnailGet(
        projectId,
        { workspace_id: active.id },
        { signal },
      ),
    enabled: hasMedia && visible && storyboard.isError,
    retry: false,
    staleTime: SIGNED_MEDIA_STALE_MS,
  })

  const manifest = storyboard.data ?? null
  const range =
    manifest === null ? null : { start: startMs ?? 0, end: endMs ?? manifest.durationMs }
  const shownMs =
    range === null
      ? null
      : (scrubMs ??
        (cardFocused
          ? scrubTimeMs(range.start, range.end, 0.5)
          : posterTimeMs(range.start, range.end)))
  const tile = manifest === null || shownMs === null ? null : tileAt(manifest, shownMs)
  const length =
    durationMs ?? (startMs === undefined && manifest !== null ? manifest.durationMs : undefined)

  const badge = cn(
    'rounded-sm bg-background/85 font-mono text-caption',
    largeBadges ? 'flex h-8 items-center rounded-md px-2.5 text-small' : 'px-1.5 py-0.5',
  )

  function scrub(event: PointerEvent<HTMLDivElement>): void {
    if (range === null || reducedMotion) return
    const bounds = event.currentTarget.getBoundingClientRect()
    if (bounds.width <= 0) return
    setScrubMs(scrubTimeMs(range.start, range.end, (event.clientX - bounds.left) / bounds.width))
  }

  return (
    <div
      ref={frame}
      data-testid="poster"
      aria-hidden="true"
      onPointerMove={scrub}
      onPointerLeave={() => setScrubMs(null)}
      className={cn('absolute inset-0 overflow-hidden bg-stage', className)}
    >
      {tile !== null ? (
        <SpriteFrame tile={tile} containerAspect={ASPECTS[aspect]} />
      ) : thumbnail.data !== undefined && !brokenThumbnail ? (
        // Signed object-store URLs are not known to the Next image optimizer.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={thumbnail.data.url}
          alt=""
          loading="lazy"
          onError={() => setBrokenThumbnail(true)}
          className="absolute inset-0 size-full object-cover"
        />
      ) : (
        <DesignedFrame startMs={startMs} endMs={endMs} durationMs={length} />
      )}
      {sharp ? (
        // Laid over the tile, so the tile shows until the sharp picture has loaded.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={imageUrl}
          alt=""
          loading="lazy"
          onError={() => setBrokenImage(imageUrl)}
          className="absolute inset-0 size-full object-cover"
        />
      ) : null}
      {hoverOverlay ? (
        <span className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background/60 opacity-0 transition-opacity duration-fast ease-signal group-hover:opacity-100">
          <span className="flex size-12 items-center justify-center rounded-full bg-background/85 text-foreground">
            <Eye strokeWidth={1.75} className="size-6" />
          </span>
        </span>
      ) : null}
      {rank === undefined && score === undefined ? null : (
        <span className="absolute left-2 top-2 flex gap-1">
          {rank === undefined ? null : (
            <span className={cn(badge, 'text-primary')}>
              #{rank}
            </span>
          )}
          {score === undefined ? null : (
            <span
              data-testid="poster-score"
              className={cn(badge, 'tabular text-foreground')}
            >
              {Math.round(score * 100)}
            </span>
          )}
        </span>
      )}
      {length === undefined ? null : (
        <span
          className={cn(
            badge,
            'tabular absolute top-2 text-foreground',
            cornerReserved ? 'right-12' : 'right-2',
          )}
        >
          {formatClock(length)}
        </span>
      )}
      {hook === undefined ? null : (
        // The clamp sits on an inner span: clamping the padded box lets the next line show
        // through its bottom padding.
        <p className="font-display absolute inset-x-0 bottom-0 bg-background/60 px-3 py-2 text-title leading-tight text-foreground">
          <span className={cn('block', !fullHook && 'line-clamp-3')}>{hook}</span>
        </p>
      )}
    </div>
  )
}

/** The intentional stand-in for media that has not been produced: graphite and a timecode. */
export function DesignedFrame({
  startMs,
  endMs,
  durationMs,
}: {
  startMs?: number
  endMs?: number
  durationMs?: number
}) {
  const label =
    startMs !== undefined && endMs !== undefined
      ? `${formatClock(startMs)} – ${formatClock(endMs)}`
      : durationMs !== undefined
        ? formatClock(durationMs)
        : null
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-stage">
      <Film aria-hidden="true" strokeWidth={1.75} className="size-5 text-subtle-foreground" />
      {label === null ? null : (
        <span className="font-mono text-caption text-muted-foreground">{label}</span>
      )}
    </div>
  )
}
