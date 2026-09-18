'use client'

import { formatTimecode } from '@/lib/time/timecode'
import { useState } from 'react'

import { MIN_ITEM_MS } from './store'
import { Slider } from '@/components/ui/slider'
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
    <section aria-label="Source monitor" className="space-y-3">
      <h2 className="text-title">Source monitor</h2>

      {source === null ? (
        <p role="status" className="text-caption text-muted-foreground">
          Loading the source…
        </p>
      ) : (
        <video
          data-testid="source-monitor-video"
          src={source.url}
          preload="metadata"
          controls
          className="w-full rounded-sm bg-stage"
        />
      )}

      <label className="flex items-center gap-2 text-xs">
        Source position
        <Slider
          aria-label="Source position"
          min={0}
          max={lengthMs}
          step={100}
          value={positionMs}
          onChange={(event) => setPositionMs(Number(event.currentTarget.value))}
          className="flex-1"
        />
        <span className="font-mono">{formatTimecode(positionMs)}</span>
      </label>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <button type="button" onClick={() => onMarkIn(positionMs)} className="inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40">
          Mark in
        </button>
        <button
          type="button"
          onClick={() => onMarkOut(positionMs)}
          className="inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40"
        >
          Mark out
        </button>
        <button
          type="button"
          onClick={onClear}
          disabled={markInMs === null && markOutMs === null}
          className="inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40"
        >
          Clear marks
        </button>
        <button
          type="button"
          onClick={onAdd}
          disabled={!usable}
          className="inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40"
        >
          Add to timeline
        </button>
        <span className="text-muted-foreground">
          {markInMs === null ? 'No mark in' : `In ${formatTimecode(markInMs)}`} ·{' '}
          {markOutMs === null ? 'no mark out' : `out ${formatTimecode(markOutMs)}`}
        </span>
      </div>
    </section>
  )
}
