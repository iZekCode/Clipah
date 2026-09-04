'use client'

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'

import { timecode } from './Player'
import { snap, snapTargets, snapThresholdMs } from './store'
import type { CompositionV1 } from '@/lib/api/generated/model'

/** How far apart the zoom levels are, in pixels per second. */
export const ZOOM_LEVELS = [4, 8, 16, 32, 64] as const

/** How far one arrow-key press moves or stretches an item. */
export const KEYBOARD_STEP_MS = 100

type Track = CompositionV1['tracks'][number]
type TrackItem = Track['items'][number]
type Gesture = { itemId: string; kind: 'move' | 'start' | 'end'; originPx: number }

/**
 * The timeline: a ruler, markers, a playhead, and one row per lane.
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
  onSelect,
  onSelectTrack,
  onSeek,
  onZoom,
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
  onSelect: (itemId: string) => void
  onSelectTrack: (trackId: string) => void
  onSeek: (ms: number) => void
  onZoom: (zoom: number) => void
  onMove: (itemId: string, toMs: number) => void
  onResize: (itemId: string, edge: 'start' | 'end', toMs: number) => void
  onRemoveMarker: (bookmarkId: string) => void
}) {
  const pixelsPerMs = zoom / 1000
  const ticks = useMemo(() => rulerTicks(composition.durationMs), [composition.durationMs])
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

  return (
    <section aria-label="Timeline" className="flex flex-col gap-2 rounded-lg border p-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-medium">Timeline</h2>
        <button
          type="button"
          aria-label="Zoom out"
          onClick={() => onZoom(neighbourZoom(zoom, -1))}
          className="rounded border px-2 text-sm"
        >
          −
        </button>
        <button
          type="button"
          aria-label="Zoom in"
          onClick={() => onZoom(neighbourZoom(zoom, 1))}
          className="rounded border px-2 text-sm"
        >
          +
        </button>
        <label className="ml-auto flex items-center gap-2 text-xs">
          Playhead
          <input
            type="range"
            aria-label="Scrub the clip"
            min={0}
            max={composition.durationMs}
            step={100}
            value={playheadMs}
            onChange={(event) => onSeek(Number(event.currentTarget.value))}
          />
        </label>
      </div>

      <ol
        aria-label="Timeline ruler"
        className="flex text-[10px] text-muted-foreground"
        style={{ width: composition.durationMs * pixelsPerMs }}
      >
        {ticks.map((tick) => (
          <li key={tick} style={{ width: tick === 0 ? 0 : 5_000 * pixelsPerMs }}>
            {timecode(tick)}
          </li>
        ))}
      </ol>

      <ul aria-label="Markers" className="flex flex-wrap gap-2 text-[10px]">
        {composition.bookmarks.map((bookmark) => (
          <li key={bookmark.id} className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => onSeek(bookmark.timelineMs)}
              className="rounded border px-2 py-0.5"
            >
              {timecode(bookmark.timelineMs)} {bookmark.label}
            </button>
            <button
              type="button"
              aria-label={`Remove the marker ${bookmark.label}`}
              onClick={() => onRemoveMarker(bookmark.id)}
              className="rounded border px-1"
            >
              ×
            </button>
          </li>
        ))}
      </ul>

      <div className="relative flex flex-col gap-1" style={{ width: composition.durationMs * pixelsPerMs }}>
        {composition.tracks.map((track) => (
          <div
            key={track.id}
            role="group"
            aria-label={`${track.type} lane ${track.id}`}
            className="relative h-10"
          >
            <button
              type="button"
              aria-label={`Select the lane ${track.id}`}
              aria-pressed={track.id === selectedTrackId}
              onClick={() => onSelectTrack(track.id)}
              className="absolute -top-1 right-0 rounded border px-1 text-[10px]"
            >
              lane
            </button>
            {[...track.items]
              .sort((left, right) => left.timelineStartMs - right.timelineStartMs)
              .map((item) => {
                const lengthMs = item.sourceOutMs - item.sourceInMs
                const locked = lockedTrackIds.includes(track.id)
                return (
                  <div
                    key={item.id}
                    className="absolute top-0 flex h-10 items-stretch"
                    style={{
                      left: item.timelineStartMs * pixelsPerMs,
                      width: Math.max(lengthMs * pixelsPerMs, 32),
                    }}
                  >
                    <button
                      type="button"
                      aria-label={`Trim the start of ${item.id}`}
                      aria-disabled={locked}
                      onPointerDown={(event) => begin(item, track, 'start', event.clientX)}
                      className="w-2 rounded-l border bg-muted"
                    />
                    <button
                      type="button"
                      aria-label={`Select ${item.id}`}
                      aria-pressed={item.id === selectedItemId}
                      aria-disabled={locked}
                      onPointerDown={(event) => begin(item, track, 'move', event.clientX)}
                      onKeyDown={(event) => onItemKeyDown(event, item, track)}
                      onClick={() => onSelect(item.id)}
                      className={`flex-1 truncate border-y px-2 text-left text-xs ${
                        item.id === selectedItemId ? 'border-primary bg-primary/10' : 'bg-muted'
                      } ${dragging === item.id ? 'opacity-70' : ''}`}
                    >
                      {item.id}
                    </button>
                    <button
                      type="button"
                      aria-label={`Trim the end of ${item.id}`}
                      aria-disabled={locked}
                      onPointerDown={(event) => begin(item, track, 'end', event.clientX)}
                      className="w-2 rounded-r border bg-muted"
                    />
                  </div>
                )
              })}
          </div>
        ))}
        <div
          data-testid="editor-playhead"
          aria-hidden
          className="pointer-events-none absolute top-0 bottom-0 w-px bg-primary"
          style={{ left: playheadMs * pixelsPerMs }}
        />
      </div>
    </section>
  )
}

/** The next zoom level in one direction, staying inside the levels that exist. */
function neighbourZoom(zoom: number, direction: 1 | -1): number {
  const index = ZOOM_LEVELS.indexOf(zoom as (typeof ZOOM_LEVELS)[number])
  const next = Math.min(Math.max((index === -1 ? 2 : index) + direction, 0), ZOOM_LEVELS.length - 1)
  return ZOOM_LEVELS[next] ?? zoom
}

/** One tick every five seconds, and always one at the start. */
function rulerTicks(durationMs: number): number[] {
  const ticks: number[] = []
  for (let at = 0; at <= durationMs; at += 5_000) {
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
