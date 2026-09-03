'use client'

import { useMemo } from 'react'

import { timecode } from './Player'
import { timelineItems } from './store'
import type { CompositionV1 } from '@/lib/api/generated/model'

/** How far apart the zoom levels are, in pixels per second. */
export const ZOOM_LEVELS = [4, 8, 16, 32, 64] as const

/**
 * The timeline: a ruler, a playhead, and the items the composition actually holds.
 *
 * Items are drawn from the document rather than from a parallel model, so a split or a
 * trim shows up here for the same reason it shows up in the preview — the document
 * changed. Selection is a button per item, so the timeline is reachable by keyboard
 * before it is reachable by pointer.
 */
export function Timeline({
  composition,
  selectedItemId,
  playheadMs,
  zoom,
  onSelect,
  onSeek,
  onZoom,
}: {
  composition: CompositionV1
  selectedItemId: string | null
  playheadMs: number
  zoom: number
  onSelect: (itemId: string) => void
  onSeek: (ms: number) => void
  onZoom: (zoom: number) => void
}) {
  const placed = useMemo(() => timelineItems(composition), [composition])
  const pixelsPerMs = zoom / 1000
  const ticks = useMemo(() => rulerTicks(composition.durationMs), [composition.durationMs])

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

      <div className="relative" style={{ width: composition.durationMs * pixelsPerMs }}>
        <ul className="flex gap-px">
          {placed.map((entry) => (
            <li key={entry.item.id}>
              <button
                type="button"
                aria-label={`Select ${entry.item.id}`}
                aria-pressed={entry.item.id === selectedItemId}
                onClick={() => onSelect(entry.item.id)}
                style={{ width: Math.max((entry.endMs - entry.startMs) * pixelsPerMs, 24) }}
                className={`h-10 truncate rounded border px-2 text-left text-xs ${
                  entry.item.id === selectedItemId ? 'border-primary bg-primary/10' : 'bg-muted'
                }`}
              >
                {entry.item.id}
              </button>
            </li>
          ))}
        </ul>
        <div
          data-testid="editor-playhead"
          aria-hidden
          className="pointer-events-none absolute top-0 h-10 w-px bg-primary"
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
