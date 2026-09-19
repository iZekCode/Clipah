'use client'

import {
  useEffect,
  useMemo,
  useRef,
  type CSSProperties,
  type ReactNode,
  type SyntheticEvent,
} from 'react'

import { captionFontStack } from './caption-fonts'
import { htmlVideoPreviewEngine, type PreviewEngine, type PreviewSource } from './engine'
import { OverlayLayer } from './OverlayLayer'
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
  overlayMedia = {},
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
  /** A playable link for each asset the composition's overlays draw, once signed. */
  overlayMedia?: Readonly<Record<string, string | undefined>>
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
    const target = sourceMsAt(placed, playheadMs)
    // While playing, the playhead mostly just echoes the media's own time back. Seeking to
    // it would restart decoding a few times a second and make playback stutter, so only a
    // real jump moves the media.
    const tolerance = playing ? PLAYING_SYNC_TOLERANCE_MS : 0
    if (Math.abs(engine.currentSourceMs() - target) > tolerance) {
      engine.seek(target)
    }
    // `playing` is read, not followed: starting or stopping playback is no reason to seek.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  /** Put freshly loaded media where the playhead is, and keep it playing if it was. */
  function placeAtPlayhead() {
    engine.seek(sourceMsAt(placed, playheadMs))
    if (playing) {
      engine.play()
    }
  }

  /** Follow the media element, so the playhead reflects what is actually playing. */
  function follow(event: SyntheticEvent<HTMLVideoElement>) {
    const sourceMs = Math.round(event.currentTarget.currentTime * 1000)
    const clipMs = clipMsAt(placed, sourceMs)
    if (clipMs === null) {
      // Media before the clip has not been placed yet — a re-signed proxy reloads from
      // its start — so put it back at the playhead rather than treating it as the end.
      if (sourceMs < firstSourceInMs(placed)) {
        engine.seek(sourceMsAt(placed, playheadMs))
        return
      }
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
          // A new proxy URL (they are re-signed every few minutes) loads from 0:00.
          onLoadedMetadata={placeAtPlayhead}
          style={crop === null || showFullFrame ? undefined : cropStyle(crop)}
          className={
            crop !== null && !showFullFrame
              ? 'absolute max-w-none'
              : showFullFrame
                ? 'h-full w-full object-contain'
                : 'h-full w-full object-cover'
          }
        />
        {/* Overlays and captions sit on the canvas, not on the frame a crop is chosen from. */}
        {showFullFrame ? null : (
          <OverlayLayer
            overlays={composition.overlays}
            playheadMs={playheadMs}
            playing={playing}
            canvasWidth={composition.canvas.width}
            media={overlayMedia}
          />
        )}
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

/** The earliest moment of the source any item plays. */
function firstSourceInMs(placed: ReturnType<typeof timelineItems>): number {
  return Math.min(...placed.map((entry) => entry.item.sourceInMs))
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

/** How far the media may drift from the playhead during playback before it is sought. */
const PLAYING_SYNC_TOLERANCE_MS = 300

/**
 * Frame the video by its normalized crop, without re-encoding anything.
 *
 * The whole frame is drawn larger than the canvas and shifted so the crop's window lands
 * exactly on it. A crop already has the canvas's shape, so nothing is stretched; the
 * frame must not also be fitted to the canvas first, or it would be scaled twice.
 */
function cropStyle(
  crop: NonNullable<CompositionV1['tracks'][number]['items'][number]['crop']>,
): CSSProperties {
  return {
    width: `${100 / crop.width}%`,
    height: `${100 / crop.height}%`,
    left: `${(-crop.x / crop.width) * 100}%`,
    top: `${(-crop.y / crop.height) * 100}%`,
    objectFit: 'fill',
  }
}
