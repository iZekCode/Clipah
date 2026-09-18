'use client'

import { useId, useState } from 'react'

import { formatTimecode, parseTimecode } from '@/lib/time/timecode'
import { cn } from '@/lib/utils'

/**
 * A time field that reads and writes `m:ss.cc` and stores milliseconds.
 *
 * A half-typed time is not a time, so it commits on Enter or when focus leaves. Arrow keys
 * nudge by a hundredth of a second, and by a second with Shift.
 */
export function TimecodeInput({
  label,
  accessibleName,
  valueMs,
  onCommit,
  minMs = 0,
  maxMs,
  hideLabel = false,
  className,
}: {
  label: string
  accessibleName?: string
  valueMs: number
  onCommit: (ms: number) => void
  minMs?: number
  maxMs?: number
  hideLabel?: boolean
  className?: string
}) {
  const id = useId()
  const messageId = `${id}-message`
  const [draft, setDraft] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  function commit(text: string): void {
    const parsed = parseTimecode(text)
    if (parsed === null) {
      setProblem('Use m:ss.cc, for example 0:21.50')
      return
    }
    if (parsed < minMs || (maxMs !== undefined && parsed > maxMs)) {
      setProblem(
        `Choose a time between ${formatTimecode(minMs)} and ${formatTimecode(maxMs ?? parsed)}`,
      )
      return
    }
    setProblem(null)
    setDraft(null)
    if (parsed !== valueMs) onCommit(parsed)
  }

  function nudge(deltaMs: number): void {
    const raised = valueMs + deltaMs
    const next = Math.max(minMs, maxMs === undefined ? raised : Math.min(maxMs, raised))
    setDraft(null)
    setProblem(null)
    if (next !== valueMs) onCommit(next)
  }

  return (
    <div className={cn('space-y-1', className)}>
      <label
        htmlFor={id}
        className={cn('block text-caption text-muted-foreground', hideLabel && 'sr-only')}
      >
        {label}
      </label>
      <input
        id={id}
        type="text"
        inputMode="decimal"
        autoComplete="off"
        spellCheck={false}
        aria-label={accessibleName}
        value={draft ?? formatTimecode(valueMs)}
        aria-invalid={problem !== null}
        aria-describedby={problem === null ? undefined : messageId}
        onChange={(event) => {
          setDraft(event.currentTarget.value)
          setProblem(null)
        }}
        onBlur={(event) => {
          if (draft !== null) commit(event.currentTarget.value)
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault()
            commit(event.currentTarget.value)
          } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
            event.preventDefault()
            nudge((event.key === 'ArrowUp' ? 1 : -1) * (event.shiftKey ? 1_000 : 10))
          } else if (event.key === 'Escape') {
            setDraft(null)
            setProblem(null)
          }
        }}
        className="tabular h-8 w-full rounded-md border border-input bg-secondary px-2 font-mono text-small text-foreground aria-[invalid=true]:border-destructive"
      />
      {problem === null ? null : (
        <p id={messageId} role="alert" className="text-caption text-destructive">
          {problem}
        </p>
      )}
    </div>
  )
}
