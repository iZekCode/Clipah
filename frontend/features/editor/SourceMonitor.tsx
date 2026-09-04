'use client'

import { useState } from 'react'

import { timecode } from './Player'
import { MIN_ITEM_MS } from './store'
import type { CompositionV1, ProxyPlaybackResponse } from '@/lib/api/generated/model'

/**
 * The source, played on its own, so a member can take a second span out of it.
 *
 * The monitor's position is expressed against the clip's own source range rather than
 * against the whole recording, because that range is what the analysis chose and what
 * the proxy capability plays. Marking in and out here changes nothing: a span reaches
 * the timeline only when a member adds it.
 */
export function SourceMonitor({
  composition,
  source,
  markInMs,
  markOutMs,
  onMarkIn,
  onMarkOut,
  onClear,
  onAdd,
}: {
  composition: CompositionV1
  source: ProxyPlaybackResponse | null
  markInMs: number | null
  markOutMs: number | null
  onMarkIn: (ms: number) => void
  onMarkOut: (ms: number) => void
  onClear: () => void
  onAdd: () => void
}) {
  const lengthMs = composition.sourceRange.outMs - composition.sourceRange.inMs
  const [positionMs, setPositionMs] = useState(0)
  const usable = markInMs !== null && markOutMs !== null && markOutMs - markInMs >= MIN_ITEM_MS

  return (
    <section aria-label="Source monitor" className="flex flex-col gap-2 rounded-lg border p-3">
      <h2 className="text-sm font-medium">Source monitor</h2>

      {source === null ? (
        <p role="status" className="text-xs text-muted-foreground">
          Loading the source…
        </p>
      ) : (
        <video
          data-testid="source-monitor-video"
          src={source.url}
          preload="metadata"
          controls
          className="w-full rounded bg-black"
        />
      )}

      <label className="flex items-center gap-2 text-xs">
        Source position
        <input
          type="range"
          aria-label="Source position"
          min={0}
          max={lengthMs}
          step={100}
          value={positionMs}
          onChange={(event) => setPositionMs(Number(event.currentTarget.value))}
          className="flex-1"
        />
        <span className="font-mono">{timecode(positionMs)}</span>
      </label>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <button type="button" onClick={() => onMarkIn(positionMs)} className="rounded border px-2 py-1">
          Mark in
        </button>
        <button
          type="button"
          onClick={() => onMarkOut(positionMs)}
          className="rounded border px-2 py-1"
        >
          Mark out
        </button>
        <button
          type="button"
          onClick={onClear}
          disabled={markInMs === null && markOutMs === null}
          className="rounded border px-2 py-1 disabled:opacity-50"
        >
          Clear marks
        </button>
        <button
          type="button"
          onClick={onAdd}
          disabled={!usable}
          className="rounded border px-2 py-1 disabled:opacity-50"
        >
          Add to timeline
        </button>
        <span className="text-muted-foreground">
          {markInMs === null ? 'No mark in' : `In ${timecode(markInMs)}`} ·{' '}
          {markOutMs === null ? 'no mark out' : `out ${timecode(markOutMs)}`}
        </span>
      </div>
    </section>
  )
}
