'use client'

import { useEffect, useMemo, useRef, type ReactNode, type SyntheticEvent } from 'react'

import { captionFontStack } from './caption-fonts'
import { htmlVideoPreviewEngine, type PreviewEngine, type PreviewSource } from './engine'
import { timelineItems } from './store'
import type { CompositionV1 } from '@/lib/api/generated/model'

/**
 * The preview: the Project's proxy, framed by the composition and bounded by it.
 *
 * The composition is the only thing that decides what is shown. Playback maps clip time
 * onto source time through the items themselves, so a trimmed or split clip plays what
 * it says it plays, and the media file underneath is never touched.
 */
export function Player({
  composition,
  source,
  playheadMs,
  playing,
  loop = false,
  showFullFrame = false,
  overlay,
  onSeek,
  onPlayingChange,
  engine: injected,
}: {
  composition: CompositionV1
  source: PreviewSource
  playheadMs: number
  playing: boolean
  /** Go round again from the start instead of stopping at the end of the clip. */
  loop?: boolean
  /** Draw the whole source frame, uncropped, so a crop can be adjusted over it. */
  showFullFrame?: boolean
  /** Drawn over the frame, inside the canvas box. */
  overlay?: ReactNode
  onSeek: (ms: number) => void
  onPlayingChange: (playing: boolean) => void
  engine?: PreviewEngine
}) {
  const video = useRef<HTMLVideoElement>(null)
  const engine = useMemo(() => injected ?? htmlVideoPreviewEngine(), [injected])
  const placed = useMemo(() => timelineItems(composition), [composition])

  useEffect(() => {
    engine.attach(video.current)
    return () => {
      engine.attach(null)
    }
  }, [engine])

  useEffect(() => {
    engine.seek(sourceMsAt(placed, playheadMs))
  }, [engine, placed, playheadMs])

  useEffect(() => {
    if (playing) {
      engine.play()
    } else {
      engine.pause()
    }
  }, [engine, playing])

  const activeWord = composition.captions.words.find(
    (word) => playheadMs >= word.startMs && playheadMs < word.endMs,
  )
  const sourceKnown = source.width !== null && source.height !== null && source.height > 0
  const aspect =
    showFullFrame && sourceKnown
      ? `${source.width} / ${source.height}`
      : `${composition.canvas.width} / ${composition.canvas.height}`
  const crop = placed[0]?.item.crop ?? null
  const captionStyle = composition.captions.style

  /** Follow the media element, so the playhead reflects what is actually playing. */
  function follow(event: SyntheticEvent<HTMLVideoElement>) {
    const sourceMs = Math.round(event.currentTarget.currentTime * 1000)
    const clipMs = clipMsAt(placed, sourceMs)
    if (clipMs === null) {
      onPlayingChange(false)
      return
    }
    if (clipMs >= composition.durationMs) {
      if (loop) {
        onSeek(0)
        return
      }
      onSeek(clipMs)
      onPlayingChange(false)
      return
    }
    onSeek(clipMs)
  }

  return (
    <section aria-label="Preview" className="flex w-full flex-col gap-2">
      <div
        data-testid="editor-canvas"
        style={{
          aspectRatio: aspect,
          background: composition.canvas.background,
          // Fit the frame to the height the studio leaves free, whatever its shape.
          maxWidth: `min(100%, calc((100vh - 26rem) * ${aspect}))`,
        }}
        className="relative mx-auto w-full overflow-hidden [container-type:inline-size]"
      >
        <video
          ref={video}
          data-testid="editor-video"
          src={source.url}
          preload="metadata"
          playsInline
          onTimeUpdate={follow}
          style={crop === null || showFullFrame ? undefined : cropStyle(crop)}
          className={showFullFrame ? 'h-full w-full object-contain' : 'h-full w-full object-cover'}
        />
        {/* Captions sit on the canvas, not on the source frame a crop is chosen from. */}
        {showFullFrame || composition.captions.mode === 'off' || activeWord === undefined ? null : (
          <p
            data-testid="editor-caption"
            // Caption sizes are canvas pixels; `cqw` scales them to the frame's drawn width.
            style={{ fontSize: `calc(${captionStyle.fontSize} / ${composition.canvas.width} * 100cqw)` }}
            className="absolute inset-x-0 bottom-6 px-4"
          >
            <span
              style={{
                color: captionStyle.color,
                fontFamily: captionFontStack(captionStyle.fontFamily),
                fontWeight: captionStyle.weight,
                fontStyle: captionStyle.italic ? 'italic' : 'normal',
                letterSpacing: `calc(${captionStyle.letterSpacing} / ${composition.canvas.width} * 100cqw)`,
                lineHeight: captionStyle.lineHeight,
                textDecoration: DECORATIONS[captionStyle.decoration],
                textAlign: captionStyle.align,
                backgroundColor: captionStyle.backgroundEnabled
                  ? captionStyle.backgroundColor
                  : undefined,
              }}
              className="block px-[0.2em]"
            >
              {activeWord.text}
            </span>
          </p>
        )}
        {overlay}
      </div>
    </section>
  )
}

/** The CSS decoration that draws each composition text decoration. */
const DECORATIONS = {
  none: 'none',
  underline: 'underline',
  strikethrough: 'line-through',
} as const

/** Where in the source media one moment of the clip lives. */
function sourceMsAt(placed: ReturnType<typeof timelineItems>, clipMs: number): number {
  for (const entry of placed) {
    if (clipMs >= entry.startMs && clipMs < entry.endMs) {
      return entry.item.sourceInMs + (clipMs - entry.startMs)
    }
  }
  return placed[0]?.item.sourceInMs ?? 0
}

/** Where in the clip one moment of the source lives, or nowhere at all. */
function clipMsAt(placed: ReturnType<typeof timelineItems>, sourceMs: number): number | null {
  for (const entry of placed) {
    if (sourceMs >= entry.item.sourceInMs && sourceMs < entry.item.sourceOutMs) {
      return entry.startMs + (sourceMs - entry.item.sourceInMs)
    }
  }
  return null
}

/** Frame the video by its normalized crop, without re-encoding anything. */
function cropStyle(crop: NonNullable<CompositionV1['tracks'][number]['items'][number]['crop']>) {
  return {
    transform: `scale(${1 / crop.width}, ${1 / crop.height})`,
    transformOrigin: `${percentage(crop.x, crop.width)}% ${percentage(crop.y, crop.height)}%`,
  }
}

/** Turn a normalized crop edge into the origin percentage CSS scales around. */
function percentage(offset: number, size: number): number {
  const remaining = 1 - size
  return remaining <= 0 ? 50 : (offset / remaining) * 100
}
