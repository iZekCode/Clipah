'use client'

import { useState } from 'react'

import type { CompositionV1 } from '@/lib/api/generated/model'

type Overlay = CompositionV1['overlays'][number]
type TextOverlay = Extract<Overlay, { type: 'text' }>
type TextStyle = TextOverlay['style']

/** The placements a text overlay may take, in the order Section 9 lists them. */
const PLACEMENTS: Array<TextOverlay['placement']> = [
  'top',
  'center',
  'lowerThird',
  'pictureInPicture',
  'cover',
]

/**
 * Type a member wrote, rather than words the transcript produced.
 *
 * Everything here is rendered as React children: the text is a member's own, but a clip
 * is reviewed by other people, and an editor that interpreted `<b>` as markup would be
 * an editor that could be made to draw something nobody typed.
 */
export function TextPanel({
  overlays,
  onAdd,
  onUpdate,
  onMove,
  onRemove,
}: {
  overlays: Overlay[]
  onAdd: (text: string) => void
  onUpdate: (
    overlayId: string,
    patch: {
      text?: string
      style?: Partial<TextStyle>
      placement?: TextOverlay['placement']
    },
  ) => void
  onMove: (overlayId: string, startMs: number, endMs: number) => void
  onRemove: (overlayId: string) => void
}) {
  const [draft, setDraft] = useState('')
  const written = overlays.filter((overlay): overlay is TextOverlay => overlay.type === 'text')

  return (
    <section aria-label="Text" className="flex flex-col gap-2 rounded-lg border p-3">
      <h2 className="text-sm font-medium">Text</h2>

      <div className="flex items-center gap-2 text-xs">
        <input
          type="text"
          aria-label="New text"
          value={draft}
          onChange={(event) => setDraft(event.currentTarget.value)}
          placeholder="Say something on screen"
          className="flex-1 rounded border px-2 py-1"
        />
        <button
          type="button"
          onClick={() => {
            onAdd(draft)
            setDraft('')
          }}
          className="rounded border px-2 py-1"
        >
          Add text
        </button>
      </div>

      <ol className="flex flex-col gap-2">
        {written.map((overlay) => (
          <li key={overlay.id} className="flex flex-col gap-1 rounded border p-2 text-xs">
            <input
              type="text"
              aria-label={`Text of ${overlay.id}`}
              value={overlay.text}
              onChange={(event) => onUpdate(overlay.id, { text: event.currentTarget.value })}
              className="rounded border px-2 py-1"
            />
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-1">
                From
                <input
                  type="number"
                  aria-label={`Start of ${overlay.id}`}
                  step={100}
                  value={overlay.timelineStartMs}
                  onChange={(event) =>
                    onMove(overlay.id, Number(event.currentTarget.value), overlay.timelineEndMs)
                  }
                  className="w-24 rounded border px-2 py-1"
                />
              </label>
              <label className="flex items-center gap-1">
                To
                <input
                  type="number"
                  aria-label={`End of ${overlay.id}`}
                  step={100}
                  value={overlay.timelineEndMs}
                  onChange={(event) =>
                    onMove(overlay.id, overlay.timelineStartMs, Number(event.currentTarget.value))
                  }
                  className="w-24 rounded border px-2 py-1"
                />
              </label>
              <label className="flex items-center gap-1">
                Size
                <input
                  type="number"
                  aria-label={`Size of ${overlay.id}`}
                  min={12}
                  max={200}
                  value={overlay.style.fontSize}
                  onChange={(event) =>
                    onUpdate(overlay.id, { style: { fontSize: Number(event.currentTarget.value) } })
                  }
                  className="w-20 rounded border px-2 py-1"
                />
              </label>
              <label className="flex items-center gap-1">
                Where
                <select
                  aria-label={`Placement of ${overlay.id}`}
                  value={overlay.placement}
                  onChange={(event) =>
                    onUpdate(overlay.id, {
                      placement: event.currentTarget.value as TextOverlay['placement'],
                    })
                  }
                  className="rounded border px-1 py-1"
                >
                  {PLACEMENTS.map((placement) => (
                    <option key={placement} value={placement}>
                      {placement}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                onClick={() => onRemove(overlay.id)}
                className="ml-auto rounded border px-2 py-1"
              >
                Remove {overlay.id}
              </button>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}
