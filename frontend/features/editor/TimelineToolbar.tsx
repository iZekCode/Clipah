'use client'

import {
  ArrowLeftToLine,
  ArrowRightToLine,
  AudioLines,
  ChevronLeft,
  ChevronRight,
  Copy,
  Flag,
  Magnet,
  Maximize2,
  Minus,
  Music,
  Plus,
  Scissors,
  Trash2,
  WrapText,
} from 'lucide-react'
import { useState } from 'react'

import { IconButton } from '@/components/ui/icon-button'
import { Slider } from '@/components/ui/slider'

import type { SoundTrackKind } from './store'
import { ZOOM_LEVELS } from './Timeline'

/**
 * The controls that act on the timeline as a whole, or on whatever is selected on it.
 *
 * Every operation is a named icon button rather than only a gesture, because a member
 * working from the keyboard has to reach the same operations a member with a pointer
 * reaches by dragging. Snap and ripple are pressed toggles.
 */
export function TimelineToolbar({
  snapping,
  ripple,
  hasSelection,
  markerCount,
  zoom,
  onSnapping,
  onRipple,
  onSplit,
  onSplitAwayLeft,
  onSplitAwayRight,
  onDuplicate,
  onDelete,
  onAddMarker,
  onPreviousMarker,
  onNextMarker,
  onAddTrack,
  onZoom,
  onFit,
}: {
  snapping: boolean
  ripple: boolean
  hasSelection: boolean
  markerCount: number
  zoom: number
  onSnapping: (snapping: boolean) => void
  onRipple: (ripple: boolean) => void
  onSplit: () => void
  onSplitAwayLeft: () => void
  onSplitAwayRight: () => void
  onDuplicate: () => void
  onDelete: () => void
  onAddMarker: (label: string) => void
  onPreviousMarker: () => void
  onNextMarker: () => void
  onAddTrack: (kind: SoundTrackKind) => void
  onZoom: (zoom: number) => void
  onFit: () => void
}) {
  const [label, setLabel] = useState('')
  const zoomIndex = Math.max(0, ZOOM_LEVELS.indexOf(zoom as (typeof ZOOM_LEVELS)[number]))
  const divider = <span aria-hidden="true" className="mx-1 h-5 w-px bg-border" />

  return (
    <div
      role="toolbar"
      aria-label="Timeline tools"
      className="flex shrink-0 flex-wrap items-center gap-0.5 border-b px-2 py-1"
    >
      <IconButton
        label="Split"
        shortcut="S"
        icon={<Scissors strokeWidth={1.75} />}
        size="sm"
        disabled={!hasSelection}
        onClick={onSplit}
      />
      <IconButton
        label="Split away the left"
        icon={<ArrowLeftToLine strokeWidth={1.75} />}
        size="sm"
        disabled={!hasSelection}
        onClick={onSplitAwayLeft}
      />
      <IconButton
        label="Split away the right"
        icon={<ArrowRightToLine strokeWidth={1.75} />}
        size="sm"
        disabled={!hasSelection}
        onClick={onSplitAwayRight}
      />
      <IconButton
        label="Duplicate"
        icon={<Copy strokeWidth={1.75} />}
        size="sm"
        disabled={!hasSelection}
        onClick={onDuplicate}
      />
      <IconButton
        label="Delete"
        shortcut="Del"
        icon={<Trash2 strokeWidth={1.75} />}
        size="sm"
        disabled={!hasSelection}
        onClick={onDelete}
      />
      {divider}
      <IconButton
        label="Snap to edges"
        aria-pressed={snapping}
        icon={<Magnet strokeWidth={1.75} />}
        size="sm"
        onClick={() => onSnapping(!snapping)}
      />
      <IconButton
        label="Ripple edits"
        aria-pressed={ripple}
        icon={<WrapText strokeWidth={1.75} />}
        size="sm"
        onClick={() => onRipple(!ripple)}
      />
      {divider}
      <input
        type="text"
        aria-label="Marker label"
        value={label}
        onChange={(event) => setLabel(event.currentTarget.value)}
        placeholder="Marker"
        className="h-8 w-28 rounded-md border border-input bg-secondary px-2 text-caption"
      />
      <IconButton
        label="Add marker"
        shortcut="M"
        icon={<Flag strokeWidth={1.75} />}
        size="sm"
        onClick={() => {
          onAddMarker(label.trim() === '' ? 'Marker' : label)
          setLabel('')
        }}
      />
      <IconButton
        label="Previous marker"
        icon={<ChevronLeft strokeWidth={1.75} />}
        size="sm"
        disabled={markerCount === 0}
        onClick={onPreviousMarker}
      />
      <IconButton
        label="Next marker"
        icon={<ChevronRight strokeWidth={1.75} />}
        size="sm"
        disabled={markerCount === 0}
        onClick={onNextMarker}
      />
      {divider}
      <IconButton
        label="Add a music lane"
        icon={<Music strokeWidth={1.75} />}
        size="sm"
        onClick={() => onAddTrack('music')}
      />
      <IconButton
        label="Add an audio lane"
        icon={<AudioLines strokeWidth={1.75} />}
        size="sm"
        onClick={() => onAddTrack('extractedAudio')}
      />
      <div className="ml-auto flex items-center gap-1">
        <IconButton
          label="Zoom out"
          shortcut="−"
          icon={<Minus strokeWidth={1.75} />}
          size="sm"
          onClick={() => onZoom(ZOOM_LEVELS[Math.max(0, zoomIndex - 1)] ?? zoom)}
        />
        <Slider
          aria-label="Zoom"
          min={0}
          max={ZOOM_LEVELS.length - 1}
          step={1}
          value={zoomIndex}
          onChange={(event) => onZoom(ZOOM_LEVELS[Number(event.currentTarget.value)] ?? zoom)}
          className="w-24"
        />
        <IconButton
          label="Zoom in"
          shortcut="+"
          icon={<Plus strokeWidth={1.75} />}
          size="sm"
          onClick={() => onZoom(ZOOM_LEVELS[Math.min(ZOOM_LEVELS.length - 1, zoomIndex + 1)] ?? zoom)}
        />
        <IconButton
          label="Fit the clip"
          icon={<Maximize2 strokeWidth={1.75} />}
          size="sm"
          onClick={onFit}
        />
      </div>
    </div>
  )
}
