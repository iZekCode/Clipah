'use client'

import { useEffect, useMemo, useRef, type SyntheticEvent } from 'react'

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
  onSeek,
  onPlayingChange,
  engine: injected,
}: {
  composition: CompositionV1
  source: PreviewSource
  playheadMs: number
  playing: boolean
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
  const aspect = `${composition.canvas.width} / ${composition.canvas.height}`
  const crop = placed[0]?.item.crop ?? null

  /** Follow the media element, so the playhead reflects what is actually playing. */
  function follow(event: SyntheticEvent<HTMLVideoElement>) {
    const sourceMs = Math.round(event.currentTarget.currentTime * 1000)
    const clipMs = clipMsAt(placed, sourceMs)
    if (clipMs === null) {
      onPlayingChange(false)
      return
    }
    onSeek(clipMs)
    if (clipMs >= composition.durationMs) {
      onPlayingChange(false)
    }
  }

  return (
    <section aria-label="Preview" className="flex flex-col gap-2">
      <div
        data-testid="editor-canvas"
        style={{ aspectRatio: aspect, background: composition.canvas.background }}
        className="relative mx-auto w-full max-w-sm overflow-hidden rounded-lg"
      >
        <video
          ref={video}
          data-testid="editor-video"
          src={source.url}
          preload="metadata"
          playsInline
          onTimeUpdate={follow}
          style={crop === null ? undefined : cropStyle(crop)}
          className="h-full w-full object-cover"
        />
        {composition.captions.mode === 'off' || activeWord === undefined ? null : (
          <p
            data-testid="editor-caption"
            style={{
              color: composition.captions.style.color,
              fontFamily: composition.captions.style.fontFamily,
              fontWeight: composition.captions.style.weight,
              textAlign: composition.captions.style.align,
            }}
            className="absolute inset-x-0 bottom-6 px-4 text-center text-sm"
          >
            {activeWord.text}
          </p>
        )}
      </div>
      <div className="flex items-center justify-center gap-3">
        <button
          type="button"
          onClick={() => onPlayingChange(!playing)}
          className="rounded-md border px-3 py-1 text-sm"
        >
          {playing ? 'Pause' : 'Play'}
        </button>
        <output aria-label="Playhead" className="font-mono text-xs text-muted-foreground">
          {timecode(playheadMs)} / {timecode(composition.durationMs)}
        </output>
      </div>
    </section>
  )
}

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

/** Render one duration the way a timecode field shows it. */
export function timecode(ms: number): string {
  const total = Math.max(0, Math.round(ms / 100) / 10)
  const minutes = Math.floor(total / 60)
  const seconds = (total % 60).toFixed(1).padStart(4, '0')
  return `${minutes}:${seconds}`
}
