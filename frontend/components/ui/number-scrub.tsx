'use client'

import { useId, useRef, useState, type PointerEvent } from 'react'

/**
 * A number a member can type, nudge with arrow keys, or drag by its label.
 *
 * Dragging previews in the field and commits once on release, so a drag is one undo step
 * and one save rather than dozens.
 */
export function NumberScrub({
  label,
  accessibleName,
  value,
  onCommit,
  min,
  max,
  step,
  precision = 0,
  unit,
}: {
  label: string
  accessibleName?: string
  value: number
  onCommit: (value: number) => void
  min: number
  max: number
  step: number
  precision?: number
  unit?: string
}) {
  const id = useId()
  const [draft, setDraft] = useState<string | null>(null)
  const drag = useRef<{ startX: number; startValue: number; preview: string | null } | null>(
    null,
  )

  const clamp = (next: number) =>
    Number(Math.min(max, Math.max(min, Math.round(next / step) * step)).toFixed(precision))

  function commit(text: string): void {
    setDraft(null)
    const parsed = Number(text)
    if (text.trim() === '' || !Number.isFinite(parsed)) return
    const next = clamp(parsed)
    if (next !== value) onCommit(next)
  }

  function onPointerDown(event: PointerEvent<HTMLLabelElement>): void {
    drag.current = { startX: event.clientX, startValue: value, preview: null }
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }

  function onPointerMove(event: PointerEvent<HTMLLabelElement>): void {
    if (drag.current === null) return
    const steps = Math.round((event.clientX - drag.current.startX) / 4)
    const preview = String(clamp(drag.current.startValue + steps * step))
    drag.current.preview = preview
    setDraft(preview)
  }

  function onPointerUp(): void {
    const ended = drag.current
    drag.current = null
    if (ended?.preview != null) commit(ended.preview)
  }

  return (
    <div className="flex items-center gap-2">
      <label
        htmlFor={id}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        className="w-24 shrink-0 cursor-ew-resize touch-none select-none text-caption text-muted-foreground"
      >
        {label}
      </label>
      <div className="relative min-w-0 flex-1">
        <input
          id={id}
          type="text"
          inputMode="decimal"
          aria-label={accessibleName}
          value={draft ?? String(value)}
          onChange={(event) => setDraft(event.currentTarget.value)}
          onBlur={(event) => {
            if (draft !== null) commit(event.currentTarget.value)
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              commit(event.currentTarget.value)
            } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
              event.preventDefault()
              const direction = event.key === 'ArrowUp' ? 1 : -1
              const next = clamp(value + direction * step * (event.shiftKey ? 10 : 1))
              setDraft(null)
              if (next !== value) onCommit(next)
            }
          }}
          className="tabular h-8 w-full rounded-md border border-input bg-secondary px-2 pr-8 font-mono text-small text-foreground"
        />
        {unit === undefined ? null : (
          <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-caption text-subtle-foreground">
            {unit}
          </span>
        )}
      </div>
    </div>
  )
}
