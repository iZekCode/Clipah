'use client'

import { timecode } from './Player'
import { interpolatedAt } from './store'
import type { CompositionV1 } from '@/lib/api/generated/model'

type TrackItem = CompositionV1['tracks'][number]['items'][number]
type Transform = TrackItem['transform']

/**
 * The keyframes on the selected item: what they say, and where they sit.
 *
 * On a base timeline item a keyframe means one thing — where in the source frame the clip
 * is looking — which is what a smart-crop suggestion produces and what a member overrides
 * here. Each keyframe is a row with its own controls, so the whole animation can be built,
 * moved, and taken apart from the keyboard.
 */
export function KeyframeEditor({
  item,
  playheadMs,
  onAdd,
  onMove,
  onRemove,
}: {
  item: TrackItem | null
  playheadMs: number
  onAdd: (atMs: number, transform: Transform) => void
  onMove: (atMs: number, toMs: number) => void
  onRemove: (atMs: number) => void
}) {
  const relativeMs = item === null ? 0 : Math.max(0, playheadMs - item.timelineStartMs)
  const current =
    item === null ? null : (interpolatedAt(item.keyframes, relativeMs).transform ?? item.transform)

  return (
    <section aria-label="Keyframes" className="flex flex-col gap-2 rounded-lg border p-3 text-xs">
      <h2 className="text-sm font-medium">Keyframes</h2>

      {item === null || current === null ? (
        <p className="text-muted-foreground">Select an item on the timeline to animate it.</p>
      ) : (
        <>
          <p className="text-muted-foreground">
            Framing at the playhead: {Math.round(current.x * 100)}% across,{' '}
            {Math.round(current.y * 100)}% down.
          </p>
          <button
            type="button"
            onClick={() => onAdd(relativeMs, current)}
            className="self-start rounded border px-2 py-1"
          >
            Add a keyframe here
          </button>

          <ol className="flex flex-col gap-1">
            {item.keyframes.map((keyframe) => (
              <li key={keyframe.atMs} className="flex items-center gap-2 rounded border p-2">
                <span className="font-mono">{timecode(keyframe.atMs)}</span>
                <span className="text-muted-foreground">
                  {keyframe.transform === null
                    ? 'no framing'
                    : `${Math.round(keyframe.transform.x * 100)}% across`}
                </span>
                <label className="ml-auto flex items-center gap-1">
                  At (ms)
                  <input
                    type="number"
                    aria-label={`Time of the keyframe at ${timecode(keyframe.atMs)}`}
                    step={100}
                    defaultValue={keyframe.atMs}
                    onBlur={(event) => {
                      const value = Number(event.currentTarget.value)
                      if (Number.isFinite(value) && value !== keyframe.atMs) {
                        onMove(keyframe.atMs, value)
                      }
                    }}
                    className="w-24 rounded border px-2 py-1"
                  />
                </label>
                <button
                  type="button"
                  aria-label={`Remove the keyframe at ${timecode(keyframe.atMs)}`}
                  onClick={() => onRemove(keyframe.atMs)}
                  className="rounded border px-2 py-1"
                >
                  Remove
                </button>
              </li>
            ))}
          </ol>

          {item.keyframes.length === 0 ? (
            <p className="text-muted-foreground">This item is not animated.</p>
          ) : null}
        </>
      )}
    </section>
  )
}
