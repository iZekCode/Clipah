'use client'

import { ChevronLeft, ChevronRight, Pause, Play, Repeat, SkipBack, SkipForward } from 'lucide-react'

import { IconButton } from '@/components/ui/icon-button'
import { formatTimecode } from '@/lib/time/timecode'

import { FRAME_MS } from './use-editor-keys'

/** Play, step, jump, loop, and the playhead as a timecode. */
export function TransportBar({
  playing,
  playheadMs,
  durationMs,
  loop,
  onPlayingChange,
  onSeek,
  onLoop,
}: {
  playing: boolean
  playheadMs: number
  durationMs: number
  loop: boolean
  onPlayingChange: (playing: boolean) => void
  onSeek: (ms: number) => void
  onLoop: (loop: boolean) => void
}) {
  const clamp = (ms: number) => Math.min(durationMs, Math.max(0, Math.round(ms)))
  return (
    <div
      role="group"
      aria-label="Transport"
      className="mx-auto flex items-center justify-center gap-1 rounded-md border border-border bg-card px-2 py-1"
    >
      <IconButton
        label="Jump to start"
        className="hidden sm:inline-flex"
        icon={<SkipBack strokeWidth={1.75} />}
        size="sm"
        onClick={() => onSeek(0)}
      />
      <IconButton
        label="Previous frame"
        shortcut="←"
        icon={<ChevronLeft strokeWidth={1.75} />}
        size="sm"
        onClick={() => onSeek(clamp(playheadMs - FRAME_MS))}
      />
      <IconButton
        label={playing ? 'Pause' : 'Play'}
        shortcut="Space"
        variant="secondary"
        icon={playing ? <Pause strokeWidth={1.75} /> : <Play strokeWidth={1.75} />}
        onClick={() => onPlayingChange(!playing)}
      />
      <IconButton
        label="Next frame"
        shortcut="→"
        icon={<ChevronRight strokeWidth={1.75} />}
        size="sm"
        onClick={() => onSeek(clamp(playheadMs + FRAME_MS))}
      />
      <IconButton
        label="Jump to end"
        className="hidden sm:inline-flex"
        icon={<SkipForward strokeWidth={1.75} />}
        size="sm"
        onClick={() => onSeek(durationMs)}
      />
      <output aria-label="Playhead" className="tabular mx-2 whitespace-nowrap font-mono text-small text-foreground">
        {formatTimecode(playheadMs)} / {formatTimecode(durationMs)}
      </output>
      <IconButton
        label="Loop the clip"
        aria-pressed={loop}
        icon={<Repeat strokeWidth={1.75} />}
        size="sm"
        onClick={() => onLoop(!loop)}
      />
    </div>
  )
}
