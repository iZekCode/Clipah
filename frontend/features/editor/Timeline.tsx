'use client'

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'

import { Filmstrip } from '@/components/media/filmstrip'
import { WaveformCanvas } from '@/components/media/waveform-canvas'
import { Slider } from '@/components/ui/slider'
import type { CompositionV1, StoryboardResponse } from '@/lib/api/generated/model'
import { formatRuler, formatTimecode } from '@/lib/time/timecode'
import { cn } from '@/lib/utils'

import { captionPhrases } from './caption-phrases'
import { snap, snapTargets, snapThresholdMs } from './store'

/** How far apart the zoom levels are, in pixels per second. */
export const ZOOM_LEVELS = [4, 8, 16, 32, 64] as const

/** How far one arrow-key press moves or stretches an item. */
export const KEYBOARD_STEP_MS = 100

type Track = CompositionV1['tracks'][number]
type TrackItem = Track['items'][number]
type Gesture = { itemId: string; kind: 'move' | 'start' | 'end'; originPx: number }

/** What each lane type is called in the label column. */
const LANE_NAMES: Record<string, string> = {
  video: 'Video',
  audio: 'Audio',
  music: 'Music',
  extractedAudio: 'Extracted audio',
}
const LANE_HEIGHT = 'h-12'

/** The largest zoom that fits the whole clip in a container, or the smallest zoom. */
export function fitZoom(containerPx: number, durationMs: number): number {
  const fitting = [...ZOOM_LEVELS]
    .reverse()
    .find((level) => (durationMs / 1000) * level <= containerPx)
  return fitting ?? ZOOM_LEVELS[0]
}

/**
 * The timeline: a ruler that is also the scrub control, markers as flags on it, a playhead,
 * and one row per lane with its name in a sticky label column. Video lanes show frames from
 * the storyboard, sound from the source shows its waveform, and captions show as phrases.
 *
 * Items are drawn from the document rather than from a parallel model, so a split, a
 * drag, or a ripple delete shows up here for the same reason it shows up in the preview:
 * the document changed. Every item is a button and every edge is a button, so an item can
 * be selected, moved, and trimmed from the keyboard alone — the pointer gestures are an
 * addition to that, never the only way in.
 */
export function Timeline({
  composition,
  selectedItemId,
  selectedTrackId,
  playheadMs,
  zoom,
  snapping,
  lockedTrackIds,
  storyboard,
  peaks,
  peaksPerSecond,
  sourceAssetId,
  onSelect,
  onSelectOverlay,
  onSelectTrack,
  onSeek,
  onMove,
  onResize,
  onRemoveMarker,
}: {
  composition: CompositionV1
  selectedItemId: string | null
  selectedTrackId: string | null
  playheadMs: number
  zoom: number
  snapping: boolean
  lockedTrackIds: string[]
  storyboard: StoryboardResponse | null
  peaks: Uint8Array | null
  peaksPerSecond: number | null
  sourceAssetId: string
  onSelect: (itemId: string) => void
  /** Choosing an overlay on its lane; it also moves the playhead to where it starts. */
  onSelectOverlay?: (overlayId: string) => void
  onSelectTrack: (trackId: string) => void
  onSeek: (ms: number) => void
  onMove: (itemId: string, toMs: number) => void
  onResize: (itemId: string, edge: 'start' | 'end', toMs: number) => void
  onRemoveMarker: (bookmarkId: string) => void
}) {
  const pixelsPerMs = zoom / 1000
  const ticks = useMemo(() => rulerTicks(composition.durationMs, zoom), [composition.durationMs, zoom])
  const phrases = useMemo(
    () => captionPhrases(composition.captions.words),
    [composition.captions.words],
  )
  const gesture = useRef<Gesture | null>(null)
  const [dragging, setDragging] = useState<string | null>(null)

  /** Where a dragged edge lands: the pointer's own answer, or the edge it is near. */
  function resolve(itemId: string, kind: 'move' | 'start' | 'end', deltaMs: number): number {
    const item = itemOf(composition, itemId)
    if (item === null) {
      return 0
    }
    const lengthMs = item.sourceOutMs - item.sourceInMs
    const startMs = item.timelineStartMs + deltaMs
    const endMs = startMs + lengthMs
    if (!snapping) {
      return kind === 'end' ? item.timelineStartMs + lengthMs + deltaMs : Math.max(0, startMs)
    }
    const targets = snapTargets(composition).filter(
      (target) => target !== item.timelineStartMs && target !== item.timelineStartMs + lengthMs,
    )
    const threshold = snapThresholdMs(zoom)
    if (kind === 'end') {
      return snap(item.timelineStartMs + lengthMs + deltaMs, targets, threshold)
    }
    if (kind === 'start') {
      return snap(startMs, targets, threshold)
    }
    const snappedStart = snap(startMs, targets, threshold)
    const snappedEnd = snap(endMs, targets, threshold)
    if (snappedStart !== startMs) {
      return snappedStart
    }
    return snappedEnd === endMs ? Math.max(0, startMs) : Math.max(0, snappedEnd - lengthMs)
  }

  // A drag is finished on the window, so a pointer that leaves the item — or the
  // timeline — still commits the gesture it started rather than abandoning it. The
  // document changes once, when the pointer is released, so one drag is one undo.
  useEffect(() => {
    function onPointerUp(event: PointerEvent): void {
      const active = gesture.current
      if (active === null) {
        return
      }
      gesture.current = null
      setDragging(null)
      const deltaMs = Math.round((event.clientX - active.originPx) / pixelsPerMs)
      if (deltaMs === 0) {
        return
      }
      const toMs = resolve(active.itemId, active.kind, deltaMs)
      if (active.kind === 'move') {
        onMove(active.itemId, toMs)
        return
      }
      onResize(active.itemId, active.kind, toMs)
    }

    window.addEventListener('pointerup', onPointerUp)
    return () => {
      window.removeEventListener('pointerup', onPointerUp)
    }
  })

  /** Begin one gesture, unless the lane it aims at is locked. */
  function begin(item: TrackItem, track: Track, kind: Gesture['kind'], clientX: number): void {
    if (lockedTrackIds.includes(track.id)) {
      return
    }
    gesture.current = { itemId: item.id, kind, originPx: clientX }
    setDragging(item.id)
  }

  /** Move or stretch one item from the keyboard, in steps a member can count. */
  function onItemKeyDown(
    event: KeyboardEvent<HTMLButtonElement>,
    item: TrackItem,
    track: Track,
  ): void {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {
      return
    }
    if (!event.altKey && !event.shiftKey) {
      return
    }
    if (lockedTrackIds.includes(track.id)) {
      return
    }
    event.preventDefault()
    const direction = event.key === 'ArrowRight' ? 1 : -1
    const lengthMs = item.sourceOutMs - item.sourceInMs
    if (event.altKey) {
      onMove(item.id, Math.max(0, item.timelineStartMs + direction * KEYBOARD_STEP_MS))
      return
    }
    onResize(item.id, 'end', item.timelineStartMs + lengthMs + direction * KEYBOARD_STEP_MS)
  }

  const width = Math.max(composition.durationMs * pixelsPerMs, 1)
  const laneLabel = `${LANE_HEIGHT} flex items-center border-b px-2 text-caption text-muted-foreground`

  return (
    <section aria-label="Timeline" className="flex min-h-0 flex-1 overflow-auto">
      <div className="sticky left-0 z-30 w-28 shrink-0 border-r bg-card">
        <div className="h-8 border-b" />
        {composition.tracks.map((track, index) => (
          <div
            key={track.id}
            className={`${LANE_HEIGHT} flex items-center justify-between gap-1 border-b px-2`}
          >
            <span className="truncate text-caption text-muted-foreground">
              {LANE_NAMES[track.type] ?? track.type}
              {composition.tracks.filter((other) => other.type === track.type).length > 1
                ? ` ${index + 1}`
                : ''}
            </span>
            <button
              type="button"
              aria-label={`Select the lane ${track.id}`}
              aria-pressed={track.id === selectedTrackId}
              onClick={() => onSelectTrack(track.id)}
              className={cn(
                'size-4 shrink-0 rounded-sm border',
                track.id === selectedTrackId ? 'border-primary bg-primary' : 'border-input',
              )}
            />
          </div>
        ))}
        <div className={laneLabel}>Captions</div>
        {composition.overlays.length === 0 ? null : <div className={laneLabel}>Overlays</div>}
      </div>

      <div className="relative shrink-0" style={{ width }}>
        <div className="relative h-8 border-b bg-card">
          <ol aria-hidden="true" className="absolute inset-0">
            {ticks.map((tick) => (
              <li
                key={tick}
                className="absolute top-0 h-full border-l border-line-strong pl-1 font-mono text-[10px] leading-4 text-subtle-foreground"
                style={{ left: tick * pixelsPerMs }}
              >
                {formatRuler(tick)}
              </li>
            ))}
          </ol>
          <Slider
            aria-label="Scrub the clip"
            aria-valuetext={formatTimecode(playheadMs)}
            min={0}
            max={composition.durationMs}
            step={10}
            value={playheadMs}
            onChange={(event) => onSeek(Number(event.currentTarget.value))}
            className="timeline-scrub absolute inset-0 z-10 h-8"
          />
          <ul aria-label="Markers" className="absolute inset-x-0 bottom-0 z-20 h-3.5">
            {composition.bookmarks.map((bookmark) => (
              <li
                key={bookmark.id}
                className="group absolute bottom-0 flex items-center"
                style={{ left: bookmark.timelineMs * pixelsPerMs }}
              >
                <button
                  type="button"
                  onClick={() => onSeek(bookmark.timelineMs)}
                  className="whitespace-nowrap rounded-t-sm bg-warning px-1 font-mono text-[10px] leading-[14px] text-background"
                >
                  {formatRuler(bookmark.timelineMs)} {bookmark.label}
                </button>
                <button
                  type="button"
                  aria-label={`Remove the marker ${bookmark.label}`}
                  onClick={() => onRemoveMarker(bookmark.id)}
                  className="ml-0.5 rounded-sm bg-popover px-1 text-[10px] leading-[14px] opacity-0 focus:opacity-100 group-hover:opacity-100"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </div>

        {composition.tracks.map((track) => (
          <div
            key={track.id}
            role="group"
            aria-label={`${track.type} lane ${track.id}`}
            className={`relative ${LANE_HEIGHT} border-b`}
          >
            {[...track.items]
              .sort((left, right) => left.timelineStartMs - right.timelineStartMs)
              .map((item) => {
                const lengthMs = item.sourceOutMs - item.sourceInMs
                const itemPx = Math.max(lengthMs * pixelsPerMs, 32)
                const locked = lockedTrackIds.includes(track.id)
                const chosen = item.id === selectedItemId
                return (
                  <div
                    key={item.id}
                    className="absolute inset-y-1 flex"
                    style={{ left: item.timelineStartMs * pixelsPerMs, width: itemPx }}
                  >
                    <button
                      type="button"
                      aria-label={`Trim the start of ${item.id}`}
                      aria-disabled={locked}
                      onPointerDown={(event) => begin(item, track, 'start', event.clientX)}
                      className="w-1.5 shrink-0 cursor-ew-resize rounded-l-sm bg-line-strong hover:bg-primary"
                    />
                    <button
                      type="button"
                      aria-label={`Select ${item.id}`}
                      aria-pressed={chosen}
                      aria-disabled={locked}
                      onPointerDown={(event) => begin(item, track, 'move', event.clientX)}
                      onKeyDown={(event) => onItemKeyDown(event, item, track)}
                      onClick={() => onSelect(item.id)}
                      className={cn(
                        'relative min-w-0 flex-1 overflow-hidden border-y text-left',
                        chosen ? 'border-primary' : 'border-line-strong',
                        dragging === item.id && 'opacity-70',
                        locked && 'cursor-not-allowed opacity-60',
                      )}
                    >
                      {track.type === 'video' ? (
                        <Filmstrip
                          storyboard={storyboard}
                          startMs={item.sourceInMs}
                          endMs={item.sourceOutMs}
                          tileCount={Math.max(1, Math.round(itemPx / 64))}
                          className="absolute inset-0"
                        />
                      ) : item.sourceAssetId === sourceAssetId ? (
                        <WaveformCanvas
                          peaks={peaks}
                          peaksPerSecond={peaksPerSecond}
                          startMs={item.sourceInMs}
                          endMs={item.sourceOutMs}
                          className="absolute inset-0"
                        />
                      ) : (
                        <span aria-hidden="true" className="absolute inset-0 bg-secondary" />
                      )}
                      <span className="absolute left-1 top-0.5 max-w-[calc(100%-0.5rem)] truncate rounded-sm bg-background/80 px-1 font-mono text-[10px] text-foreground">
                        {item.id}
                      </span>
                    </button>
                    <button
                      type="button"
                      aria-label={`Trim the end of ${item.id}`}
                      aria-disabled={locked}
                      onPointerDown={(event) => begin(item, track, 'end', event.clientX)}
                      className="w-1.5 shrink-0 cursor-ew-resize rounded-r-sm bg-line-strong hover:bg-primary"
                    />
                  </div>
                )
              })}
          </div>
        ))}

        <div role="group" aria-label="Captions lane" className={`relative ${LANE_HEIGHT} border-b`}>
          {phrases.map((phrase) => (
            <button
              key={phrase.id}
              type="button"
              onClick={() => onSeek(phrase.startMs)}
              className="absolute inset-y-2 truncate rounded-sm bg-secondary px-1.5 text-left text-caption text-foreground hover:bg-line-strong"
              style={{
                left: phrase.startMs * pixelsPerMs,
                width: Math.max((phrase.endMs - phrase.startMs) * pixelsPerMs, 24),
              }}
            >
              {phrase.text}
            </button>
          ))}
        </div>

        {composition.overlays.length === 0 ? null : (
          <div role="group" aria-label="Overlays lane" className={`relative ${LANE_HEIGHT} border-b`}>
            {composition.overlays.map((overlay) => (
              <button
                key={overlay.id}
                type="button"
                aria-label={overlayLabel(overlay)}
                onClick={() => {
                  onSeek(overlay.timelineStartMs)
                  onSelectOverlay?.(overlay.id)
                }}
                className="absolute inset-y-2 truncate rounded-sm border border-dashed border-line-strong bg-secondary px-1.5 text-left text-caption"
                style={{
                  left: overlay.timelineStartMs * pixelsPerMs,
                  width: Math.max(
                    (overlay.timelineEndMs - overlay.timelineStartMs) * pixelsPerMs,
                    32,
                  ),
                }}
              >
                {overlayLabel(overlay)}
              </button>
            ))}
          </div>
        )}

        <div
          data-testid="editor-playhead"
          aria-hidden="true"
          className="pointer-events-none absolute bottom-0 top-8 z-10 w-px bg-primary"
          style={{ left: playheadMs * pixelsPerMs }}
        />
      </div>
    </section>
  )
}

/**
 * What one overlay is called on the timeline, and when it is on screen.
 *
 * An accepted suggestion is named for what it is rather than for the proposal behind it:
 * once a member has agreed to a picture it is theirs, and the record of which suggestion
 * it answers belongs in the document rather than on the lane.
 */
function overlayLabel(overlay: CompositionV1['overlays'][number]): string {
  const window = `from ${formatTimecode(overlay.timelineStartMs)} to ${formatTimecode(overlay.timelineEndMs)}`
  if (overlay.type === 'text' || overlay.type === 'citation') {
    return `Text ${window}`
  }
  return overlay.origin.type === 'brollSuggestion'
    ? `B-roll ${window}`
    : `Overlay ${window}`
}

/** Ruler ticks spaced so their labels never overlap at the current zoom. */
function rulerTicks(durationMs: number, zoom: number): number[] {
  const everyMs = zoom >= 64 ? 1_000 : zoom >= 32 ? 2_000 : zoom >= 16 ? 5_000 : zoom >= 8 ? 10_000 : 30_000
  const ticks: number[] = []
  for (let at = 0; at <= durationMs; at += everyMs) {
    ticks.push(at)
  }
  return ticks
}

/** Find one item anywhere on the timeline. */
function itemOf(composition: CompositionV1, itemId: string): TrackItem | null {
  for (const track of composition.tracks) {
    const item = track.items.find((candidate) => candidate.id === itemId)
    if (item !== undefined) {
      return item
    }
  }
  return null
}
