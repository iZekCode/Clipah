'use client'

import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NumberScrub } from '@/components/ui/number-scrub'
import { Select } from '@/components/ui/select'
import { TimecodeInput } from '@/components/ui/timecode-input'
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

/** What each placement is called in the picker. */
const PLACEMENT_LABELS: Record<string, string> = {
  top: 'Top',
  center: 'Centre',
  lowerThird: 'Lower third',
  pictureInPicture: 'Picture in picture',
  cover: 'Cover',
}

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
    <section aria-label="Text" className="space-y-3">
      <h2 className="text-title">Text</h2>

      <div className="flex items-center gap-2">
        <Input
          type="text"
          aria-label="New text"
          value={draft}
          onChange={(event) => setDraft(event.currentTarget.value)}
          placeholder="Say something on screen"
          className="flex-1"
        />
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            onAdd(draft)
            setDraft('')
          }}
        >
          Add text
        </Button>
      </div>

      <ol className="divide-y divide-border">
        {written.map((overlay) => (
          <li key={overlay.id} className="space-y-2 py-3">
            <Input
              type="text"
              aria-label={`Text of ${overlay.id}`}
              value={overlay.text}
              onChange={(event) => onUpdate(overlay.id, { text: event.currentTarget.value })}
            />
            <div className="grid grid-cols-2 gap-2">
              <TimecodeInput
                label="From"
                accessibleName={`Start of ${overlay.id}`}
                valueMs={overlay.timelineStartMs}
                onCommit={(ms) => onMove(overlay.id, ms, overlay.timelineEndMs)}
              />
              <TimecodeInput
                label="To"
                accessibleName={`End of ${overlay.id}`}
                valueMs={overlay.timelineEndMs}
                onCommit={(ms) => onMove(overlay.id, overlay.timelineStartMs, ms)}
              />
            </div>
            <NumberScrub
              label="Size"
              accessibleName={`Size of ${overlay.id}`}
              value={overlay.style.fontSize}
              min={12}
              max={200}
              step={1}
              unit="px"
              onCommit={(fontSize) => onUpdate(overlay.id, { style: { fontSize } })}
            />
            <div className="flex items-center gap-2">
              <span className="w-24 shrink-0 text-caption text-muted-foreground">Where</span>
              <Select
                aria-label={`Placement of ${overlay.id}`}
                value={overlay.placement}
                onChange={(event) =>
                  onUpdate(overlay.id, {
                    placement: event.currentTarget.value as TextOverlay['placement'],
                  })
                }
                controlSize="sm"
              >
                {PLACEMENTS.map((placement) => (
                  <option key={placement} value={placement}>
                    {PLACEMENT_LABELS[placement] ?? placement}
                  </option>
                ))}
              </Select>
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto"
                onClick={() => onRemove(overlay.id)}
              >
                Remove {overlay.id}
              </Button>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}
