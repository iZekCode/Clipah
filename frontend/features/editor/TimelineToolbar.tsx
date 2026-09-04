'use client'

import { useState } from 'react'

import type { SoundTrackKind } from './store'

/**
 * The controls that act on the timeline as a whole, or on whatever is selected on it.
 *
 * Every one of them is a button or a checkbox rather than a gesture, because a member
 * working from the keyboard has to be able to reach the same operations a member with a
 * pointer reaches by dragging. The gestures are additions to these, never replacements.
 */
export function TimelineToolbar({
  snapping,
  ripple,
  hasSelection,
  markerCount,
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
}: {
  snapping: boolean
  ripple: boolean
  hasSelection: boolean
  markerCount: number
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
}) {
  const [label, setLabel] = useState('')

  return (
    <section aria-label="Editing tools" className="flex flex-wrap items-center gap-2 rounded-lg border p-3 text-xs">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onSplit}
          disabled={!hasSelection}
          className="rounded border px-2 py-1 disabled:opacity-50"
        >
          Split
        </button>
        <button
          type="button"
          onClick={onSplitAwayLeft}
          disabled={!hasSelection}
          className="rounded border px-2 py-1 disabled:opacity-50"
        >
          Split away the left
        </button>
        <button
          type="button"
          onClick={onSplitAwayRight}
          disabled={!hasSelection}
          className="rounded border px-2 py-1 disabled:opacity-50"
        >
          Split away the right
        </button>
        <button
          type="button"
          onClick={onDuplicate}
          disabled={!hasSelection}
          className="rounded border px-2 py-1 disabled:opacity-50"
        >
          Duplicate
        </button>
        <button
          type="button"
          onClick={onDelete}
          disabled={!hasSelection}
          className="rounded border px-2 py-1 disabled:opacity-50"
        >
          Delete
        </button>
      </div>

      <label className="flex items-center gap-1">
        <input
          type="checkbox"
          aria-label="Snap to edges"
          checked={snapping}
          onChange={(event) => onSnapping(event.currentTarget.checked)}
        />
        Snap
      </label>
      <label className="flex items-center gap-1">
        <input
          type="checkbox"
          aria-label="Ripple edits"
          checked={ripple}
          onChange={(event) => onRipple(event.currentTarget.checked)}
        />
        Ripple
      </label>

      <div className="flex items-center gap-1">
        <input
          type="text"
          aria-label="Marker label"
          value={label}
          onChange={(event) => setLabel(event.currentTarget.value)}
          placeholder="Marker label"
          className="w-32 rounded border px-2 py-1"
        />
        <button
          type="button"
          onClick={() => {
            onAddMarker(label.trim() === '' ? 'Marker' : label)
            setLabel('')
          }}
          className="rounded border px-2 py-1"
        >
          Add marker
        </button>
        <button
          type="button"
          onClick={onPreviousMarker}
          disabled={markerCount === 0}
          className="rounded border px-2 py-1 disabled:opacity-50"
        >
          Previous marker
        </button>
        <button
          type="button"
          onClick={onNextMarker}
          disabled={markerCount === 0}
          className="rounded border px-2 py-1 disabled:opacity-50"
        >
          Next marker
        </button>
      </div>

      <div className="flex items-center gap-1">
        <button type="button" onClick={() => onAddTrack('music')} className="rounded border px-2 py-1">
          Add a music lane
        </button>
        <button
          type="button"
          onClick={() => onAddTrack('extractedAudio')}
          className="rounded border px-2 py-1"
        >
          Add an audio lane
        </button>
      </div>
    </section>
  )
}
