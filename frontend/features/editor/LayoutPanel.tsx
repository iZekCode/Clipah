'use client'

import { SegmentedControl } from '@/components/ui/segmented-control'
import type { CompositionV1 } from '@/lib/api/generated/model'

import { centreCrop } from './store'

type TrackItem = CompositionV1['tracks'][number]['items'][number]

/**
 * How the source fills the canvas; a custom crop is adjusted on the stage.
 *
 * With no crop the renderer scales the source to cover the canvas and keeps its middle, so
 * that choice is named for what it exports. A custom crop starts at the same window and is
 * then moved or resized by hand.
 */
export function LayoutPanel({
  item,
  sourceAspect,
  canvasAspect,
  onCrop,
}: {
  item: TrackItem | null
  sourceAspect: number
  canvasAspect: number
  onCrop: (crop: TrackItem['crop']) => void
}) {
  return (
    <section aria-label="Layout" className="space-y-3">
      <h2 className="text-title">Layout</h2>
      {item === null ? (
        <p className="text-small text-muted-foreground">
          Select a video item on the timeline to frame it. The crop appears on the stage.
        </p>
      ) : (
        <>
          <SegmentedControl
            label="Framing"
            value={item.crop === null ? 'centred' : 'custom'}
            options={[
              { value: 'centred', label: 'Centred' },
              { value: 'custom', label: 'Custom' },
            ]}
            onChange={(choice) =>
              choice === 'centred'
                ? onCrop(null)
                : item.crop === null
                  ? onCrop(centreCrop(sourceAspect, canvasAspect))
                  : undefined
            }
          />
          <p className="text-small text-muted-foreground">
            {item.crop === null
              ? 'Centred fills the canvas from the middle of the frame. Choose Custom to move the crop yourself.'
              : 'Drag the crop on the stage, or focus it and use the arrow keys. Shift moves further.'}
          </p>
        </>
      )}
    </section>
  )
}
