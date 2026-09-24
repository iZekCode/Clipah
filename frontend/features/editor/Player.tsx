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
import { captionLines } from './caption-phrases'
import { htmlVideoPreviewEngine, type PreviewEngine, type PreviewSource } from './engine'
import { OverlayLayer } from './OverlayLayer'
import { timelineItems, type CompositionWatermark } from './store'
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

  const lines = useMemo(() => captionLines(composition.captions.words), [composition.captions.words])
  // The whole line a viewer reads, as the export draws it — not just the word being said.
  const activeLine = lines.find((line) => playheadMs >= line.startMs && playheadMs < line.endMs)
  const karaoke = composition.captions.mode === 'karaoke'
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
        {showFullFrame || composition.captions.mode === 'off' || activeLine === undefined ? null : (
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
              {activeLine.words.map((word, index) => (
                <span
                  key={word.id}
                  // Karaoke lights each word from the moment it is said and keeps it lit.
                  data-said={karaoke && playheadMs >= word.startMs ? 'true' : undefined}
                  style={
                    karaoke && playheadMs >= word.startMs
                      ? { color: captionStyle.highlightColor }
                      : undefined
                  }
                >
                  {index === 0 ? '' : ' '}
                  {word.text}
                </span>
              ))}
            </span>
          </p>
        )}
        {showFullFrame || composition.watermark == null ? null : (
          <WatermarkMark watermark={composition.watermark} media={overlayMedia} />
        )}
        {overlay}
      </div>
    </section>
  )
}

/** The gap the export keeps between a watermark and the edges it sits against. */
const WATERMARK_MARGIN = 'calc(4cqw)'

/** Where each grid cell anchors a watermark, as the export's overlay offsets do. */
const WATERMARK_PLACEMENT: Record<CompositionWatermark['position'], CSSProperties> = {
  topLeft: { top: WATERMARK_MARGIN, left: WATERMARK_MARGIN },
  topCenter: { top: WATERMARK_MARGIN, left: '50%', transform: 'translateX(-50%)' },
  topRight: { top: WATERMARK_MARGIN, right: WATERMARK_MARGIN },
  middleLeft: { top: '50%', left: WATERMARK_MARGIN, transform: 'translateY(-50%)' },
  center: { top: '50%', left: '50%', transform: 'translate(-50%, -50%)' },
  middleRight: { top: '50%', right: WATERMARK_MARGIN, transform: 'translateY(-50%)' },
  bottomLeft: { bottom: WATERMARK_MARGIN, left: WATERMARK_MARGIN },
  bottomCenter: { bottom: WATERMARK_MARGIN, left: '50%', transform: 'translateX(-50%)' },
  bottomRight: { bottom: WATERMARK_MARGIN, right: WATERMARK_MARGIN },
}

/**
 * The member's mark, drawn where and how large the export burns it. Its size is a share of
 * the frame width: a picture's width, or a line of text's height.
 */
export function WatermarkMark({
  watermark,
  media,
}: {
  watermark: CompositionWatermark
  media: Readonly<Record<string, string | undefined>>
}) {
  const place: CSSProperties = {
    ...WATERMARK_PLACEMENT[watermark.position],
    opacity: watermark.opacity,
  }
  if (watermark.kind === 'text') {
    return (
      <span
        data-testid="editor-watermark"
        style={{
          ...place,
          fontSize: `calc(${watermark.size * 100}cqw)`,
          textShadow: '2px 2px 0 rgb(0 0 0 / 0.5)',
        }}
        className="pointer-events-none absolute whitespace-nowrap font-sans leading-none text-white"
      >
        {watermark.text}
      </span>
    )
  }
  const url =
    watermark.kind === 'clipah' ? CLIPAH_LOGO_URL : media[watermark.assetId ?? ''] ?? undefined
  if (url === undefined) return null
  return (
    // eslint-disable-next-line @next/next/no-img-element -- a signed, short-lived media link
    <img
      data-testid="editor-watermark"
      src={url}
      alt=""
      style={{ ...place, width: `calc(${watermark.size * 100}cqw)` }}
      className="pointer-events-none absolute h-auto"
    />
  )
}

/** Clipah's own mark, the same picture the renderer ships with. */
export const CLIPAH_LOGO_URL = '/clipah-logo.png'

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
