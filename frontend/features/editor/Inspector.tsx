'use client'

import { useState } from 'react'

import { ASPECT_CANVAS, type Aspect } from './store'
import type { SaveStatus } from './autosave'
import type { CompositionV1 } from '@/lib/api/generated/model'

type TrackItem = CompositionV1['tracks'][number]['items'][number]

/** What the editor says about work that has not reached the backend yet. */
const SAVE_LABELS: Record<SaveStatus, string> = {
  idle: 'Not saved yet',
  saving: 'Saving…',
  saved: 'Saved',
  offline: 'Offline — your changes are kept here',
  conflict: 'Conflict',
}

/**
 * The inspector: the numbers behind the selected item, and the state of the document.
 *
 * Trim and crop are typed here as well as dragged on the timeline, because a member who
 * knows the exact frame they want should not have to find it with a mouse, and because a
 * numeric field is the control a keyboard and a screen reader can both use.
 */
export function Inspector({
  composition,
  item,
  status,
  dirty,
  canUndo,
  canRedo,
  onTrim,
  onCrop,
  onAspect,
  onSplit,
  onDelete,
  onUndo,
  onRedo,
  onSave,
  playheadMs,
}: {
  composition: CompositionV1
  item: TrackItem | null
  status: SaveStatus
  dirty: boolean
  canUndo: boolean
  canRedo: boolean
  onTrim: (sourceInMs: number, sourceOutMs: number) => void
  onCrop: (crop: TrackItem['crop']) => void
  onAspect: (aspect: Aspect) => void
  onSplit: () => void
  onDelete: () => void
  onUndo: () => void
  onRedo: () => void
  onSave: () => void
  playheadMs: number
}) {
  const aspect = currentAspect(composition)
  // Trim fields are committed when a member leaves them. Committing every keystroke
  // would clamp a half-typed number back into the field and fight whoever is typing.
  const [bounds, setBounds] = useState<{ inMs?: string; outMs?: string }>({})

  /** Send whatever is in the two trim fields, then let the document own them again. */
  function commitBounds(): void {
    if (item === null) {
      return
    }
    const sourceInMs = bounds.inMs === undefined ? item.sourceInMs : Number(bounds.inMs)
    const sourceOutMs = bounds.outMs === undefined ? item.sourceOutMs : Number(bounds.outMs)
    setBounds({})
    if (Number.isFinite(sourceInMs) && Number.isFinite(sourceOutMs)) {
      onTrim(sourceInMs, sourceOutMs)
    }
  }

  return (
    <section aria-label="Inspector" className="flex flex-col gap-3 rounded-lg border p-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-medium">Inspector</h2>
        <p role="status" className="ml-auto text-xs text-muted-foreground">
          {dirty && status === 'saved' ? SAVE_LABELS.idle : SAVE_LABELS[status]}
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {(Object.keys(ASPECT_CANVAS) as Aspect[]).map((preset) => (
          <button
            key={preset}
            type="button"
            aria-pressed={preset === aspect}
            onClick={() => onAspect(preset)}
            className={`rounded border px-2 py-1 text-xs ${
              preset === aspect ? 'border-primary bg-primary/10' : ''
            }`}
          >
            {preset}
          </button>
        ))}
      </div>

      {item === null ? (
        <p className="text-xs text-muted-foreground">Select an item on the timeline to edit it.</p>
      ) : (
        <div className="flex flex-col gap-2 text-xs">
          <label className="flex items-center justify-between gap-2">
            Clip starts at (ms)
            <input
              type="number"
              aria-label="Clip starts at"
              value={bounds.inMs ?? item.sourceInMs}
              step={100}
              onChange={(event) => {
                const value = event.currentTarget.value
                setBounds((current) => ({ ...current, inMs: value }))
              }}
              onBlur={commitBounds}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  commitBounds()
                }
              }}
              className="w-28 rounded border px-2 py-1"
            />
          </label>
          <label className="flex items-center justify-between gap-2">
            Clip ends at (ms)
            <input
              type="number"
              aria-label="Clip ends at"
              value={bounds.outMs ?? item.sourceOutMs}
              step={100}
              onChange={(event) => {
                const value = event.currentTarget.value
                setBounds((current) => ({ ...current, outMs: value }))
              }}
              onBlur={commitBounds}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  commitBounds()
                }
              }}
              className="w-28 rounded border px-2 py-1"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={onSplit} className="rounded border px-2 py-1">
              Split at playhead
            </button>
            <button type="button" onClick={onDelete} className="rounded border px-2 py-1">
              Delete item
            </button>
            <span className="text-muted-foreground">Playhead {playheadMs} ms</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onCrop(null)}
              disabled={item.crop === null}
              className="rounded border px-2 py-1 disabled:opacity-50"
            >
              Clear crop
            </button>
            <span className="text-muted-foreground">
              {item.crop === null
                ? 'Full frame'
                : `Cropped to ${Math.round(item.crop.width * 100)}% × ${Math.round(
                    item.crop.height * 100,
                  )}%`}
            </span>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onUndo}
          disabled={!canUndo}
          className="rounded border px-2 py-1 text-xs disabled:opacity-50"
        >
          Undo
        </button>
        <button
          type="button"
          onClick={onRedo}
          disabled={!canRedo}
          className="rounded border px-2 py-1 text-xs disabled:opacity-50"
        >
          Redo
        </button>
        <button
          type="button"
          onClick={onSave}
          className="ml-auto rounded border px-3 py-1 text-xs font-medium"
        >
          Save
        </button>
      </div>
    </section>
  )
}

/** Which preset, if any, the canvas currently matches. */
function currentAspect(composition: CompositionV1): Aspect | null {
  for (const [preset, canvas] of Object.entries(ASPECT_CANVAS) as Array<
    [Aspect, { width: number; height: number }]
  >) {
    if (canvas.width === composition.canvas.width && canvas.height === composition.canvas.height) {
      return preset
    }
  }
  return null
}
