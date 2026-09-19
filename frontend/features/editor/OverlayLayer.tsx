'use client'

import { useEffect, useRef, type CSSProperties } from 'react'

import { captionFontStack } from './caption-fonts'
import type { CompositionV1 } from '@/lib/api/generated/model'

type Overlay = CompositionV1['overlays'][number]
type MediaOverlay = Extract<Overlay, { type: 'video' | 'image' }>
type VideoOverlay = Extract<Overlay, { type: 'video' }>
type WordOverlay = Extract<Overlay, { type: 'text' | 'citation' }>

/** How far an overlay's media may drift from the playhead during playback before it is sought. */
const PLAYING_SYNC_TOLERANCE_MS = 300

/**
 * Every overlay the timeline has reached, drawn the way the export draws it.
 *
 * A picture or video fills the whole frame, as the renderer scales and crops it to the
 * canvas. Text sits near the top, in the middle, or low in the frame. Motion presets and
 * keyframes are not previewed yet: an overlay stands still for its whole window.
 */
export function OverlayLayer({
  overlays,
  playheadMs,
  playing,
  canvasWidth,
  media,
}: {
  overlays: readonly Overlay[]
  playheadMs: number
  playing: boolean
  canvasWidth: number
  /** A playable link for each asset an overlay draws, once it has been signed. */
  media: Readonly<Record<string, string | undefined>>
}) {
  const active = overlays.filter(
    (overlay) => playheadMs >= overlay.timelineStartMs && playheadMs < overlay.timelineEndMs,
  )
  return (
    <>
      {active.map((overlay) => {
        if (overlay.type === 'video' || overlay.type === 'image') {
          const url = media[overlay.assetId]
          if (url === undefined) {
            return null
          }
          return overlay.type === 'video' ? (
            <OverlayVideo
              key={overlay.id}
              overlay={overlay}
              url={url}
              playheadMs={playheadMs}
              playing={playing}
            />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element -- a signed, short-lived link
            <img
              key={overlay.id}
              data-testid={`overlay-${overlay.id}`}
              src={url}
              alt=""
              style={mediaStyle(overlay)}
              className="pointer-events-none absolute inset-0 h-full w-full object-cover"
            />
          )
        }
        return <OverlayWords key={overlay.id} overlay={overlay} canvasWidth={canvasWidth} />
      })}
    </>
  )
}

/** Where in its own media one overlay is when the timeline is at `playheadMs`. */
export function overlayMediaMs(overlay: VideoOverlay, playheadMs: number): number {
  return overlay.sourceInMs + Math.max(0, playheadMs - overlay.timelineStartMs)
}

/** One B-roll clip, kept on the playhead and playing only when the clip plays. */
function OverlayVideo({
  overlay,
  url,
  playheadMs,
  playing,
}: {
  overlay: VideoOverlay
  url: string
  playheadMs: number
  playing: boolean
}) {
  const video = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    const element = video.current
    if (element === null) {
      return
    }
    const targetMs = overlayMediaMs(overlay, playheadMs)
    const tolerance = playing ? PLAYING_SYNC_TOLERANCE_MS : 0
    if (Math.abs(element.currentTime * 1000 - targetMs) > tolerance) {
      element.currentTime = targetMs / 1000
    }
  }, [overlay, playheadMs, playing])

  useEffect(() => {
    const element = video.current
    if (element === null) {
      return
    }
    if (playing) {
      void element.play?.()?.catch?.(() => {})
    } else {
      element.pause?.()
    }
  }, [playing])

  return (
    <video
      ref={video}
      data-testid={`overlay-${overlay.id}`}
      src={url}
      // The dialogue under a cutaway comes from the main video; the export mixes this
      // clip's own sound in only when asked to, which the preview leaves out.
      muted
      playsInline
      preload="auto"
      style={mediaStyle(overlay)}
      className="pointer-events-none absolute inset-0 h-full w-full object-cover"
    />
  )
}

/** Member-written text or a citation, at the height the export draws it. */
function OverlayWords({ overlay, canvasWidth }: { overlay: WordOverlay; canvasWidth: number }) {
  const style = overlay.style
  return (
    <p
      data-placement={overlay.placement}
      style={{ ...WORD_POSITION[overlay.placement], opacity: overlay.opacity }}
      className="pointer-events-none absolute inset-x-0 px-[6cqw]"
    >
      <span
        style={{
          display: 'block',
          color: style.color,
          fontFamily: captionFontStack(style.fontFamily),
          fontWeight: style.weight,
          fontStyle: style.italic ? 'italic' : 'normal',
          // Overlay sizes are canvas pixels; `cqw` scales them to the frame's drawn width.
          fontSize: `calc(${style.fontSize} / ${canvasWidth} * 100cqw)`,
          letterSpacing: `calc(${style.letterSpacing} / ${canvasWidth} * 100cqw)`,
          lineHeight: style.lineHeight,
          textAlign: style.align,
          textDecoration: DECORATIONS[style.decoration],
          backgroundColor: style.backgroundEnabled ? style.backgroundColor : undefined,
        }}
      >
        {overlay.text}
      </span>
    </p>
  )
}

/** A picture or clip's opacity; it always fills the frame, as the export draws it. */
function mediaStyle(overlay: MediaOverlay): CSSProperties {
  return { opacity: overlay.opacity }
}

/** The heights the renderer draws text at: 12% down, centred, or three quarters down. */
const WORD_POSITION: Record<WordOverlay['placement'], CSSProperties> = {
  top: { top: '12%' },
  center: { top: '50%', transform: 'translateY(-50%)' },
  cover: { top: '75%' },
  pictureInPicture: { top: '75%' },
  lowerThird: { top: '75%' },
}

/** The CSS decoration that draws each composition text decoration. */
const DECORATIONS = {
  none: 'none',
  underline: 'underline',
  strikethrough: 'line-through',
} as const
