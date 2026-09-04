'use client'

import { useState } from 'react'

import { MOTIONS, motionDefinition, motionFits } from './templates'
import type { CompositionV1 } from '@/lib/api/generated/model'

type Overlay = CompositionV1['overlays'][number]
type MovingOverlay = Exclude<Overlay, { type: 'citation' }>
type MotionPreset = MovingOverlay['motion']

/**
 * The movement each element carries, and the window it has to be legible within.
 *
 * The bounds are the backend's own, read from the published definitions, so an element a
 * member could animate here is an element the renderer will accept. Choosing a movement
 * an element is too short to show is refused with the reason, rather than accepted now
 * and refused at export.
 */
export function MotionPanel({
  composition,
  onMotion,
}: {
  composition: CompositionV1
  onMotion: (targetId: string, preset: MotionPreset) => void
}) {
  const [refused, setRefused] = useState<string | null>(null)
  const targets = [
    ...composition.tracks.flatMap((track) =>
      track.items.map((item) => ({
        id: item.id,
        motion: item.motion,
        durationMs: item.sourceOutMs - item.sourceInMs,
      })),
    ),
    ...composition.overlays
      .filter((overlay): overlay is MovingOverlay => overlay.type !== 'citation')
      .map((overlay) => ({
        id: overlay.id,
        motion: overlay.motion,
        durationMs: overlay.timelineEndMs - overlay.timelineStartMs,
      })),
  ]

  /** Apply one movement, or say why this element has no room for it. */
  function choose(targetId: string, preset: MotionPreset, durationMs: number): void {
    if (!motionFits(preset, durationMs)) {
      const definition = motionDefinition(preset)
      setRefused(
        `${preset} needs at least ${definition.minDurationMs}ms and at most ` +
          `${definition.maxDurationMs}ms; ${targetId} is on screen for ${durationMs}ms.`,
      )
      return
    }
    setRefused(null)
    onMotion(targetId, preset)
  }

  return (
    <section aria-label="Motion" className="flex flex-col gap-2 rounded-lg border p-3 text-xs">
      <h2 className="text-sm font-medium">Motion</h2>

      <ul className="flex flex-col gap-1">
        {targets.map((target) => (
          <li key={target.id} className="flex items-center gap-2">
            <span className="font-mono">{target.id}</span>
            <label className="ml-auto flex items-center gap-1">
              Movement
              <select
                aria-label={`Movement of ${target.id}`}
                value={target.motion}
                onChange={(event) =>
                  choose(target.id, event.currentTarget.value as MotionPreset, target.durationMs)
                }
                className="rounded border px-1 py-1"
              >
                {MOTIONS.map((motion) => (
                  <option key={motion.preset} value={motion.preset}>
                    {motion.preset}
                  </option>
                ))}
              </select>
            </label>
          </li>
        ))}
      </ul>

      {refused === null ? null : (
        <p role="alert" className="text-destructive">
          {refused}
        </p>
      )}
    </section>
  )
}
