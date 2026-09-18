'use client'

import { Move, Scaling } from 'lucide-react'
import type { KeyboardEvent, PointerEvent } from 'react'

import type { CompositionV1 } from '@/lib/api/generated/model'

type Crop = NonNullable<CompositionV1['tracks'][number]['items'][number]['crop']>

const STEP = 0.01
const BIG_STEP = 0.1
const MIN_SIZE = 0.1
const EPSILON = 1e-9

/**
 * The crop drawn over the whole source frame, moved and resized in place.
 *
 * Resizing keeps the crop's own shape — the canvas shape in source units — and its centre,
 * so the export never stretches. Every change stays inside the frame, and both handles work
 * from the keyboard. A drag previews while the pointer moves and commits once on release,
 * so one drag is one undo step.
 */
export function CropOverlay({
  crop,
  onCommit,
  onPreview,
}: {
  crop: Crop
  onCommit: (crop: Crop) => void
  onPreview?: (crop: Crop) => void
}) {
  function commit(next: Crop): void {
    if (
      next.x < -EPSILON ||
      next.y < -EPSILON ||
      next.x + next.width > 1 + EPSILON ||
      next.y + next.height > 1 + EPSILON
    ) {
      return
    }
    onCommit(round(next))
  }

  function resized(from: Crop, delta: number): Crop | null {
    const ratio = from.height / from.width
    const width = Math.min(1, Math.max(MIN_SIZE, from.width + delta))
    const height = width * ratio
    if (height > 1 || height < MIN_SIZE) return null
    const centreX = from.x + from.width / 2
    const centreY = from.y + from.height / 2
    return { x: centreX - width / 2, y: centreY - height / 2, width, height }
  }

  function onMoveKey(event: KeyboardEvent<HTMLButtonElement>): void {
    const step = event.shiftKey ? BIG_STEP : STEP
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    }
    const delta = moves[event.key]
    if (delta === undefined) return
    event.preventDefault()
    commit({ ...crop, x: crop.x + delta[0], y: crop.y + delta[1] })
  }

  function onResizeKey(event: KeyboardEvent<HTMLButtonElement>): void {
    const step = (event.shiftKey ? BIG_STEP : STEP) * 2
    const grow = event.key === 'ArrowUp' || event.key === 'ArrowRight'
    const shrink = event.key === 'ArrowDown' || event.key === 'ArrowLeft'
    if (!grow && !shrink) return
    event.preventDefault()
    const next = resized(crop, grow ? step : -step)
    if (next !== null) commit(next)
  }

  function drag(kind: 'move' | 'resize') {
    return (event: PointerEvent<HTMLButtonElement>) => {
      const frame = event.currentTarget.closest('[data-crop-frame]')?.getBoundingClientRect()
      if (frame === undefined || frame.width === 0 || frame.height === 0) return
      const startX = event.clientX
      const startY = event.clientY
      const origin = crop
      let last: Crop | null = null
      const at = (clientX: number, clientY: number): Crop | null => {
        const dx = (clientX - startX) / frame.width
        const dy = (clientY - startY) / frame.height
        if (kind === 'move') {
          const x = Math.min(1 - origin.width, Math.max(0, origin.x + dx))
          const y = Math.min(1 - origin.height, Math.max(0, origin.y + dy))
          return round({ ...origin, x, y })
        }
        const next = resized(origin, dx * 2)
        return next === null ? null : round(next)
      }
      const onMovePointer = (moveEvent: globalThis.PointerEvent) => {
        const next = at(moveEvent.clientX, moveEvent.clientY)
        if (next === null) return
        last = next
        onPreview?.(next)
      }
      const onUp = (upEvent: globalThis.PointerEvent) => {
        window.removeEventListener('pointermove', onMovePointer)
        window.removeEventListener('pointerup', onUp)
        const next = at(upEvent.clientX, upEvent.clientY) ?? last
        if (next !== null) commit(next)
      }
      window.addEventListener('pointermove', onMovePointer)
      window.addEventListener('pointerup', onUp)
    }
  }

  return (
    <div data-crop-frame className="absolute inset-0">
      <div
        role="group"
        aria-label="Crop"
        className="absolute border-2 border-primary shadow-[0_0_0_9999px_rgb(10_10_11/0.65)]"
        style={{
          left: `${crop.x * 100}%`,
          top: `${crop.y * 100}%`,
          width: `${crop.width * 100}%`,
          height: `${crop.height * 100}%`,
        }}
      >
        <button
          type="button"
          aria-label="Move crop"
          onKeyDown={onMoveKey}
          onPointerDown={drag('move')}
          className="absolute inset-0 flex cursor-move touch-none items-center justify-center text-primary opacity-0 hover:opacity-100 focus-visible:opacity-100"
        >
          <Move aria-hidden="true" strokeWidth={1.75} className="size-6" />
        </button>
        <button
          type="button"
          aria-label="Resize crop"
          onKeyDown={onResizeKey}
          onPointerDown={drag('resize')}
          className="absolute -bottom-1.5 -right-1.5 flex size-5 cursor-nwse-resize touch-none items-center justify-center rounded-sm bg-primary text-primary-foreground"
        >
          <Scaling aria-hidden="true" strokeWidth={2} className="size-3" />
        </button>
      </div>
    </div>
  )
}

function round(crop: Crop): Crop {
  const fix = (value: number) => Math.round(value * 10_000) / 10_000
  return { x: fix(crop.x), y: fix(crop.y), width: fix(crop.width), height: fix(crop.height) }
}
