# Signal Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (the repository owner requires inline execution without subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the editor from a stack of forms into a studio: a dark stage with a transport, a timeline with a draggable playhead, filmstrip and waveform tracks, a transcript-style caption editor, visual style controls, crop on the stage, timecodes instead of milliseconds — and captions previewed in the same font files the renderer draws.

**Architecture:** The composition reducer (`features/editor/store.ts`), autosave, revision-conflict handling, B-roll decisions, and mounted tool panels are untouched. New input primitives live in `components/ui/`; the editor's screen is reorganised around them. Accessible names that tests and browser scenarios already depend on are kept where the control's job is unchanged, and tests are updated in the same task wherever a control's shape changes (a millisecond field becoming a timecode field, a checkbox becoming a toggle button).

**Tech Stack:** Next.js 15 (`next/font/local`), React 19, Tailwind CSS 3, Radix Popover/Sheet/Switch, Vitest + Testing Library, Playwright; Python 3.13 readiness module, Debian fontconfig, Docker.

**Spec:** `redesign-plan-v2.md` → Screens → Editor. Plan index: `docs/superpowers/plans/2026-09-17-signal-studio-redesign.md`.

## Global Constraints

- Everything in the plan index's "Global constraints" applies.
- Depends on Plans 1–3 (primitives, `Filmstrip`, `WaveformCanvas`, `useStoryboard`, `useWaveform`).
- Preserved behaviour: the reducer, undo/redo history, autosave and conflict banner, B-roll decisions, export binding, every existing shortcut (`⌘Z`, `⇧⌘Z`, `⌘S`, `Space`, `S`, `Delete`/`Backspace`, `+`/`=`, `-`), and panels staying mounted across tool switches.
- New shortcuts: `←`/`→` one frame (1/30 s — the proxy's nominal rate is not exposed), `⇧←`/`⇧→` one second, `M` add a marker, `?` the shortcut sheet. No shortcut fires in a text field.
- Editable times display and accept `m:ss.cc`; the ruler shows `m:ss`; stored values stay milliseconds.
- The caption composition has no vertical-position field, so the style panel offers no position control (the spec's position control is dropped rather than invented).
- Letter spacing and line height use `NumberScrub` (typeable and draggable) rather than sliders, so exact values stay enterable.
- Caption fonts: the eight `FontFamily` values (Inter, Montserrat, Poppins, Roboto, Open Sans, Bebas Neue, Anton, Nunito) are vendored once under `frontend/features/editor/fonts/`, loaded in the editor with `next/font/local`, copied into the media image, and verified by media readiness.
- Owner commit message for this plan: `feat: rebuild the editor as a studio`.

## File map

| File | Responsibility |
| --- | --- |
| `frontend/lib/time/timecode.ts` | `formatTimecode`, `parseTimecode`, `formatRuler` |
| `frontend/components/ui/timecode-input.tsx`, `number-scrub.tsx`, `swatch-picker.tsx` | Input primitives |
| `frontend/features/editor/fonts/*`, `frontend/features/editor/caption-fonts.ts` | Vendored caption fonts and their loaders |
| `infra/docker/backend.Dockerfile`, `backend/src/clipah/runtime/readiness.py` | Fonts in the media image and their readiness check |
| `frontend/features/editor/use-editor-keys.ts`, `ShortcutSheet.tsx`, `TransportBar.tsx` | Keyboard map, its sheet, the transport |
| `frontend/features/editor/EditorScreen.tsx` | Studio layout |
| `frontend/features/editor/Timeline.tsx`, `TimelineToolbar.tsx`, `caption-phrases.ts` | Timeline and its tools |
| `frontend/features/editor/Inspector.tsx` | Contextual inspector |
| `frontend/features/editor/CaptionsPanel.tsx`, `KaraokePanel.tsx` | Word editor and timing mode |
| `frontend/features/editor/StylePanel.tsx`, `FontPicker.tsx`, `LookCard.tsx`, `use-brand-colors.ts` | Style tool |
| `frontend/features/editor/CropOverlay.tsx`, `LayoutPanel.tsx` | Crop on the stage |
| `frontend/features/editor/{TextPanel,AudioPanel,AssetsPanel,SourceMonitor,SceneList,MotionPanel,KeyframeEditor,AccessibilityPanel}.tsx`, `frontend/features/broll/*` | Signal restyle and timecode fields |

---

### Task 1: Timecodes, `TimecodeInput`, and `NumberScrub`

**Files:**
- Create: `frontend/lib/time/timecode.ts`, `frontend/components/ui/timecode-input.tsx`, `frontend/components/ui/number-scrub.tsx`
- Create: `frontend/tests/timecode.test.tsx`

**Interfaces:**
- Produces:
  - `formatTimecode(ms: number): string` → `m:ss.cc`; `parseTimecode(text: string): number | null` (accepts `m:ss.cc`, `m:ss`, `s.cc`, `s`); `formatRuler(ms: number): string` → `m:ss`.
  - `TimecodeInput({ label: string; accessibleName?: string; valueMs: number; onCommit: (ms: number) => void; minMs?: number; maxMs?: number; hideLabel?: boolean; className?: string })` — `accessibleName`, when given, names the field and must contain the visible label's words or stand in for a hidden label.
  - `NumberScrub({ label: string; accessibleName?: string; value: number; onCommit: (value: number) => void; min: number; max: number; step: number; precision?: number; unit?: string })`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/timecode.test.tsx`:

```tsx
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { NumberScrub } from '@/components/ui/number-scrub'
import { TimecodeInput } from '@/components/ui/timecode-input'
import { formatRuler, formatTimecode, parseTimecode } from '@/lib/time/timecode'

describe('timecodes', () => {
  test('format minutes, seconds, and hundredths', () => {
    expect(formatTimecode(0)).toBe('0:00.00')
    expect(formatTimecode(1_500)).toBe('0:01.50')
    expect(formatTimecode(21_000)).toBe('0:21.00')
    expect(formatTimecode(725_555)).toBe('12:05.56')
    expect(formatRuler(65_900)).toBe('1:05')
  })

  test('read back every form a member types, and refuse the rest', () => {
    expect(parseTimecode('0:21.00')).toBe(21_000)
    expect(parseTimecode('12:05.56')).toBe(725_560)
    expect(parseTimecode('1:05')).toBe(65_000)
    expect(parseTimecode('21')).toBe(21_000)
    expect(parseTimecode('1.5')).toBe(1_500)
    expect(parseTimecode(' 0:01.05 ')).toBe(1_050)
    expect(parseTimecode('1:75')).toBeNull()
    expect(parseTimecode('abc')).toBeNull()
    expect(parseTimecode('')).toBeNull()
  })
})

describe('TimecodeInput', () => {
  test('shows a timecode and commits milliseconds when the member leaves the field', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<TimecodeInput label="End" valueMs={31_000} onCommit={onCommit} />)

    const field = screen.getByRole('textbox', { name: 'End' })
    expect(field).toHaveValue('0:31.00')
    await user.clear(field)
    await user.type(field, '0:21.00')
    expect(onCommit).not.toHaveBeenCalled()
    await user.tab()

    expect(onCommit).toHaveBeenCalledWith(21_000)
  })

  test('arrow keys nudge by a hundredth, and by a second with Shift', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<TimecodeInput label="Start" valueMs={1_000} onCommit={onCommit} />)

    screen.getByRole('textbox', { name: 'Start' }).focus()
    await user.keyboard('{ArrowUp}')
    await user.keyboard('{Shift>}{ArrowDown}{/Shift}')

    expect(onCommit).toHaveBeenNthCalledWith(1, 1_010)
    expect(onCommit).toHaveBeenNthCalledWith(2, 0)
  })

  test('an accessible name can stand in for a hidden label', () => {
    render(<TimecodeInput label="From" hideLabel accessibleName="Start of w000002" valueMs={1_000} onCommit={vi.fn()} />)

    expect(screen.getByRole('textbox', { name: 'Start of w000002' })).toHaveValue('0:01.00')
  })

  test('explains an unreadable or out-of-range time and keeps the value', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<TimecodeInput label="End" valueMs={5_000} maxMs={10_000} onCommit={onCommit} />)
    const field = screen.getByRole('textbox', { name: 'End' })

    await user.clear(field)
    await user.type(field, 'soon{Enter}')
    expect(screen.getByRole('alert')).toHaveTextContent('Use m:ss.cc, for example 0:21.50')
    expect(field).toHaveAttribute('aria-invalid', 'true')

    await user.clear(field)
    await user.type(field, '0:12.00{Enter}')
    expect(screen.getByRole('alert')).toHaveTextContent('Choose a time between 0:00.00 and 0:10.00')
    expect(onCommit).not.toHaveBeenCalled()
  })
})

describe('NumberScrub', () => {
  test('commits a typed value clamped to its range, named by its accessible name', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<NumberScrub label="Letter spacing" accessibleName="Caption letter spacing" value={0} min={-10} max={40} step={0.5} precision={1} onCommit={onCommit} />)

    const field = screen.getByRole('textbox', { name: 'Caption letter spacing' })
    await user.clear(field)
    await user.type(field, '99')
    await user.tab()

    expect(onCommit).toHaveBeenCalledWith(40)
  })

  test('dragging the label previews and commits once on release', () => {
    const onCommit = vi.fn()
    render(<NumberScrub label="Size" value={64} min={12} max={200} step={1} onCommit={onCommit} />)
    const label = screen.getByText('Size')

    fireEvent.pointerDown(label, { clientX: 0, pointerId: 1 })
    fireEvent.pointerMove(label, { clientX: 40, pointerId: 1 })
    expect(screen.getByRole('textbox', { name: 'Size' })).toHaveValue('74')
    expect(onCommit).not.toHaveBeenCalled()
    fireEvent.pointerUp(label, { clientX: 40, pointerId: 1 })

    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith(74)
  })
})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/timecode.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement `frontend/lib/time/timecode.ts`**

```ts
const WITH_MINUTES = /^(\d+):([0-5]?\d)(?:\.(\d{1,3}))?$/
const SECONDS_ONLY = /^(\d+)(?:\.(\d{1,3}))?$/

/** `m:ss.cc` — the precision captions and trims are edited at. */
export function formatTimecode(ms: number): string {
  const centiseconds = Math.max(0, Math.round(ms / 10))
  const minutes = Math.floor(centiseconds / 6_000)
  const seconds = Math.floor(centiseconds / 100) % 60
  const hundredths = centiseconds % 100
  return `${minutes}:${String(seconds).padStart(2, '0')}.${String(hundredths).padStart(2, '0')}`
}

/** Read `m:ss.cc`, `m:ss`, `s.cc`, or `s` back to milliseconds, or nothing. */
export function parseTimecode(text: string): number | null {
  const value = text.trim()
  const withMinutes = WITH_MINUTES.exec(value)
  if (withMinutes !== null) {
    return Number(withMinutes[1]) * 60_000 + Number(withMinutes[2]) * 1_000 + fraction(withMinutes[3])
  }
  const secondsOnly = SECONDS_ONLY.exec(value)
  if (secondsOnly !== null) {
    return Number(secondsOnly[1]) * 1_000 + fraction(secondsOnly[2])
  }
  return null
}

/** `m:ss` for ruler ticks. */
export function formatRuler(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1_000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

function fraction(digits: string | undefined): number {
  return digits === undefined ? 0 : Number(digits.padEnd(3, '0'))
}
```

- [ ] **Step 4: Implement `frontend/components/ui/timecode-input.tsx`**

```tsx
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
      setProblem(`Choose a time between ${formatTimecode(minMs)} and ${formatTimecode(maxMs ?? parsed)}`)
      return
    }
    setProblem(null)
    setDraft(null)
    if (parsed !== valueMs) onCommit(parsed)
  }

  function nudge(deltaMs: number): void {
    const next = Math.max(minMs, maxMs === undefined ? valueMs + deltaMs : Math.min(maxMs, valueMs + deltaMs))
    setDraft(null)
    setProblem(null)
    if (next !== valueMs) onCommit(next)
  }

  return (
    <div className={cn('space-y-1', className)}>
      <label htmlFor={id} className={cn('block text-caption text-muted-foreground', hideLabel && 'sr-only')}>
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
        className="h-8 w-full rounded-md border border-input bg-secondary px-2 font-mono text-small tabular text-foreground aria-[invalid=true]:border-destructive"
      />
      {problem === null ? null : (
        <p id={messageId} role="alert" className="text-caption text-destructive">
          {problem}
        </p>
      )}
    </div>
  )
}
```

The second nudge test starts from `valueMs={1_000}` both times because the test does not re-render with the committed value; `1_000 − 1_000 = 0`.

- [ ] **Step 5: Implement `frontend/components/ui/number-scrub.tsx`**

```tsx
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
  const drag = useRef<{ startX: number; startValue: number } | null>(null)

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
    drag.current = { startX: event.clientX, startValue: value }
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }

  function onPointerMove(event: PointerEvent<HTMLLabelElement>): void {
    if (drag.current === null) return
    const steps = Math.round((event.clientX - drag.current.startX) / 4)
    setDraft(String(clamp(drag.current.startValue + steps * step)))
  }

  function onPointerUp(): void {
    if (drag.current === null) return
    drag.current = null
    if (draft !== null) commit(draft)
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
              const next = clamp(value + (event.key === 'ArrowUp' ? 1 : -1) * step * (event.shiftKey ? 10 : 1))
              setDraft(null)
              if (next !== value) onCommit(next)
            }
          }}
          className="h-8 w-full rounded-md border border-input bg-secondary px-2 pr-8 font-mono text-small tabular text-foreground"
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
```

`aria-label` overrides the visible label as the accessible name; `accessibleName` must always contain the visible label's words (WCAG 2.5.3), as "Caption letter spacing" contains "Letter spacing".

- [ ] **Step 6: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/timecode.test.tsx` → PASS.

---

### Task 2: `SwatchPicker`

**Files:**
- Create: `frontend/components/ui/swatch-picker.tsx`
- Create: `frontend/tests/swatch-picker.test.tsx`

**Interfaces:**
- Produces: `SwatchPicker({ label: string; accessibleName: string; value: string; onChange: (hex: string) => void; brandColors?: ReadonlyArray<{ name: string; hex: string }> })`; recent colours are remembered under `clipah.recent-colours` in `localStorage` (max 6, `try`/`catch`).

- [ ] **Step 1: Write the failing tests**

`frontend/tests/swatch-picker.test.tsx`:

```tsx
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { SwatchPicker } from '@/components/ui/swatch-picker'

beforeEach(() => {
  window.localStorage.clear()
})

describe('SwatchPicker', () => {
  test('offers brand colours first and marks the current one', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <SwatchPicker
        label="Colour"
        accessibleName="Caption colour"
        value="#FFFFFF"
        onChange={onChange}
        brandColors={[
          { name: 'Paper', hex: '#FFFFFF' },
          { name: 'Signal lime', hex: '#C6FF3D' },
        ]}
      />,
    )

    const group = screen.getByRole('group', { name: 'Caption colour' })
    expect(within(group).getByRole('button', { name: 'Paper #FFFFFF' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(group).getByText('#FFFFFF')).toBeInTheDocument()
    await user.click(within(group).getByRole('button', { name: 'Signal lime #C6FF3D' }))

    expect(onChange).toHaveBeenCalledWith('#C6FF3D')
  })

  test('a custom hex is validated, applied, and remembered as recent', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<SwatchPicker label="Colour" accessibleName="Caption colour" value="#FFFFFF" onChange={onChange} />)

    await user.click(screen.getByRole('button', { name: 'Custom colour' }))
    const hex = await screen.findByRole('textbox', { name: 'Hex colour' })
    await user.clear(hex)
    await user.type(hex, '12ab{Enter}')
    expect(screen.getByRole('alert')).toHaveTextContent('Use six hex digits, for example #FFB020')

    await user.clear(hex)
    await user.type(hex, '#ffb020{Enter}')

    expect(onChange).toHaveBeenCalledWith('#FFB020')
    expect(JSON.parse(window.localStorage.getItem('clipah.recent-colours') ?? '[]')).toEqual(['#FFB020'])
  })
})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/swatch-picker.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `frontend/components/ui/swatch-picker.tsx`**

```tsx
'use client'

import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'

const RECENT_KEY = 'clipah.recent-colours'
const HEX = /^#?([0-9A-F]{6})$/i

/**
 * Choose a colour: the brand's first, then recent ones, then any hex.
 *
 * The current value is always shown as text, because a swatch alone is not an answer for
 * someone who cannot tell two greens apart.
 */
export function SwatchPicker({
  label,
  accessibleName,
  value,
  onChange,
  brandColors = [],
}: {
  label: string
  accessibleName: string
  value: string
  onChange: (hex: string) => void
  brandColors?: ReadonlyArray<{ name: string; hex: string }>
}) {
  const [recent, setRecent] = useState<string[]>([])
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(value)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    setRecent(readRecent())
  }, [])

  function choose(hex: string): void {
    const normal = hex.toUpperCase()
    onChange(normal)
    const next = [normal, ...recent.filter((entry) => entry !== normal)].slice(0, 6)
    setRecent(next)
    writeRecent(next)
  }

  function applyDraft(): void {
    const match = HEX.exec(draft.trim())
    if (match === null) {
      setProblem('Use six hex digits, for example #FFB020')
      return
    }
    setProblem(null)
    choose(`#${match[1]}`)
    setOpen(false)
  }

  const brandHexes = new Set(brandColors.map((color) => color.hex.toUpperCase()))
  const swatches = [
    ...brandColors.map((color) => ({ name: color.name, hex: color.hex.toUpperCase() })),
    ...recent.filter((hex) => !brandHexes.has(hex)).map((hex) => ({ name: 'Recent', hex })),
  ]

  return (
    <div role="group" aria-label={accessibleName} className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-caption text-muted-foreground">{label}</span>
        <span className="font-mono text-caption text-foreground">{value.toUpperCase()}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {swatches.map((swatch) => (
          <button
            key={`${swatch.name}-${swatch.hex}`}
            type="button"
            aria-label={`${swatch.name} ${swatch.hex}`}
            aria-pressed={swatch.hex === value.toUpperCase()}
            onClick={() => choose(swatch.hex)}
            className={cn(
              'size-6 rounded-sm border border-input transition-shadow duration-fast ease-signal',
              swatch.hex === value.toUpperCase() && 'ring-2 ring-primary ring-offset-2 ring-offset-card',
            )}
            style={{ backgroundColor: swatch.hex }}
          />
        ))}
        <Popover
          open={open}
          onOpenChange={(next) => {
            setOpen(next)
            setDraft(value)
            setProblem(null)
          }}
        >
          <PopoverTrigger asChild>
            <button
              type="button"
              aria-label="Custom colour"
              className="flex size-6 items-center justify-center rounded-sm border border-dashed border-input text-muted-foreground hover:text-foreground"
            >
              <Plus aria-hidden="true" strokeWidth={1.75} className="size-3.5" />
            </button>
          </PopoverTrigger>
          <PopoverContent className="w-56 space-y-2 border-line-strong bg-popover p-3">
            <input
              type="color"
              aria-label="Pick a colour"
              value={HEX.test(draft) ? `#${HEX.exec(draft)![1]}` : value}
              onChange={(event) => setDraft(event.currentTarget.value)}
              className="h-24 w-full cursor-pointer rounded-md border border-input bg-transparent"
            />
            <input
              type="text"
              aria-label="Hex colour"
              value={draft}
              onChange={(event) => {
                setDraft(event.currentTarget.value)
                setProblem(null)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  applyDraft()
                }
              }}
              className="h-8 w-full rounded-md border border-input bg-secondary px-2 font-mono text-small uppercase"
            />
            {problem === null ? null : (
              <p role="alert" className="text-caption text-destructive">
                {problem}
              </p>
            )}
          </PopoverContent>
        </Popover>
      </div>
    </div>
  )
}

function readRecent(): string[] {
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(RECENT_KEY) ?? '[]')
    return Array.isArray(stored) ? stored.filter((entry): entry is string => typeof entry === 'string' && HEX.test(entry)) : []
  } catch {
    return []
  }
}

function writeRecent(colours: string[]): void {
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(colours))
  } catch {
    // Recent colours are a convenience; blocked storage costs nothing else.
  }
}
```

Confirm `components/ui/popover.tsx` exists and restyle its content class to `z-50 rounded-lg border border-line-strong bg-popover p-3 text-popover-foreground shadow-xl outline-none`. Radix Popover in jsdom needs the `ResizeObserver` stub already in `vitest.setup.ts`; if it also needs `DOMRect`, add `globalThis.DOMRect ??= class { … }` to the setup file.

- [ ] **Step 4: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/swatch-picker.test.tsx` → PASS.

---

### Task 3: Caption font parity

**Files:**
- Create: `frontend/features/editor/fonts/` (eight families' font files, their licences, `SOURCES.md`)
- Create: `frontend/features/editor/caption-fonts.ts`
- Modify: `frontend/vitest.setup.ts` (mock `next/font/local`)
- Modify: `frontend/features/editor/Player.tsx` (preview uses the vendored faces)
- Modify: `infra/docker/backend.Dockerfile` (`media-root` copies the files and rebuilds the font cache)
- Modify: `backend/src/clipah/runtime/readiness.py` (every caption family must be installed)
- Modify: `backend/tests/unit/test_runtime_readiness.py`
- Modify: `backend/tests/contract/test_runtime_deployment.py`
- Create: `frontend/tests/caption-fonts.test.ts`

**Interfaces:**
- Produces: `CAPTION_FONT_VARIABLES: string` (all eight `next/font` variable classes); `captionFontStack(family: CaptionFontFamily): string` → `var(--caption-font-…), sans-serif`; `CAPTION_FONT_FAMILIES: readonly CaptionFontFamily[]`; backend `CAPTION_FONT_FAMILIES = frozenset(family.value for family in FontFamily)`.

- [ ] **Step 1: Write the failing backend tests**

In `backend/tests/unit/test_runtime_readiness.py`, extend `valid_outputs()` with:

```python
        ("fc-list", "--format", "%{family}\n"): (
            "Noto Sans\nInter,Inter Medium\nMontserrat\nPoppins\nRoboto\n"
            "Open Sans\nBebas Neue\nAnton\nNunito\n"
        ),
```

and add:

```python
@pytest.mark.unit
def test_media_readiness_refuses_an_image_missing_a_caption_font() -> None:
    """A caption drawn in a fallback face exports differently from its preview."""
    outputs = valid_outputs()
    outputs[("fc-list", "--format", "%{family}\n")] = "Noto Sans\nInter\n"

    with pytest.raises(RuntimeReadinessError):
        MediaCapabilityVerifier(runner=RecordingRunner(outputs)).verify()
```

In `backend/tests/contract/test_runtime_deployment.py`, add:

```python
@pytest.mark.unit
def test_the_media_image_installs_every_caption_font_the_editor_previews() -> None:
    """The preview and the renderer must draw captions from the same font files."""
    dockerfile = (REPOSITORY_ROOT / "infra/docker/backend.Dockerfile").read_text()
    media_root = dockerfile.split("AS media-root")[1].split("FROM ")[0]
    fonts = REPOSITORY_ROOT / "frontend/features/editor/fonts"

    assert "COPY frontend/features/editor/fonts/ /usr/local/share/fonts/clipah/" in media_root
    assert "fc-cache" in media_root
    vendored = {path.name for path in fonts.iterdir()}
    for family in FontFamily:
        stem = family.value.replace(" ", "")
        assert any(name.startswith(stem) for name in vendored), family.value
```

(import `FontFamily` from `clipah.editor.models`; `REPOSITORY_ROOT` already exists in that file.)

- [ ] **Step 2: Write the failing frontend test**

`frontend/tests/caption-fonts.test.ts`:

```ts
import { describe, expect, test } from 'vitest'

import { CAPTION_FONT_FAMILIES, CAPTION_FONT_VARIABLES, captionFontStack } from '@/features/editor/caption-fonts'

describe('caption fonts', () => {
  test('every family the composition allows has a vendored face and a CSS variable', () => {
    expect(CAPTION_FONT_FAMILIES).toEqual(['Inter', 'Montserrat', 'Poppins', 'Roboto', 'Open Sans', 'Bebas Neue', 'Anton', 'Nunito'])
    expect(captionFontStack('Bebas Neue')).toBe('var(--caption-font-bebas-neue), sans-serif')
    expect(CAPTION_FONT_VARIABLES.split(' ')).toHaveLength(8)
  })
})
```

- [ ] **Step 3: Run the tests and watch them fail**

Run from `backend/`: `uv run pytest -q tests/unit/test_runtime_readiness.py tests/contract/test_runtime_deployment.py -k "caption or accepts_only"` → FAIL.
Run: `pnpm --dir frontend exec vitest run tests/caption-fonts.test.ts` → FAIL.

- [ ] **Step 4: Vendor the fonts**

Run from the repository root:

```bash
COMMIT=$(git ls-remote https://github.com/google/fonts HEAD | cut -f1)
API="https://api.github.com/repos/google/fonts/contents"
RAW="https://raw.githubusercontent.com/google/fonts/$COMMIT"
DEST=frontend/features/editor/fonts
mkdir -p "$DEST"
for dir in ofl/inter ofl/montserrat ofl/poppins ofl/roboto ofl/opensans ofl/bebasneue ofl/anton ofl/nunito; do
  curl -fsSL "$API/$dir?ref=$COMMIT" | python3 -c 'import json,sys; [print(e["path"]) for e in json.load(sys.stdin) if e["name"].endswith(".ttf") or e["name"] in ("OFL.txt","LICENSE.txt")]'
done
```

From the listing, download for each family: the variable `.ttf` where one exists (Inter, Montserrat, Roboto, Open Sans, Nunito — name them `Inter-Variable.ttf`, `Montserrat-Variable.ttf`, `Roboto-Variable.ttf`, `OpenSans-Variable.ttf`, `Nunito-Variable.ttf`), otherwise the static files (`Poppins-Light.ttf`, `Poppins-Regular.ttf`, `Poppins-Medium.ttf`, `Poppins-SemiBold.ttf`, `Poppins-Bold.ttf`, `Poppins-ExtraBold.ttf`, `Poppins-Black.ttf`, `BebasNeue-Regular.ttf`, `Anton-Regular.ttf`), plus each family's licence as `<Family>-OFL.txt`. For every file:

```bash
curl -fL "$RAW/<path from the listing>" -o "$DEST/<target name>"
```

If `ofl/roboto` does not exist at that commit, use the directory the listing of `apache` shows for Roboto and its `LICENSE.txt`. Then record in `frontend/features/editor/fonts/SOURCES.md`: the commit, each source path, each target name, `shasum -a 256` of each file, and the licence of each family. Every file name must start with the family name without spaces (the contract test checks this).

- [ ] **Step 5: Write the loaders and the test mock**

`frontend/features/editor/caption-fonts.ts`:

```ts
import localFont from 'next/font/local'

import type { CompositionV1 } from '@/lib/api/generated/model'

export type CaptionFontFamily = CompositionV1['captions']['style']['fontFamily']

// Each face is the file the render image installs, so what the preview draws is what the
// renderer draws. Variable fonts cover their whole weight range; static families list files.
const inter = localFont({ src: './fonts/Inter-Variable.ttf', variable: '--caption-font-inter', weight: '100 900', display: 'swap' })
const montserrat = localFont({ src: './fonts/Montserrat-Variable.ttf', variable: '--caption-font-montserrat', weight: '100 900', display: 'swap' })
const poppins = localFont({
  src: [
    { path: './fonts/Poppins-Light.ttf', weight: '300' },
    { path: './fonts/Poppins-Regular.ttf', weight: '400' },
    { path: './fonts/Poppins-Medium.ttf', weight: '500' },
    { path: './fonts/Poppins-SemiBold.ttf', weight: '600' },
    { path: './fonts/Poppins-Bold.ttf', weight: '700' },
    { path: './fonts/Poppins-ExtraBold.ttf', weight: '800' },
    { path: './fonts/Poppins-Black.ttf', weight: '900' },
  ],
  variable: '--caption-font-poppins',
  display: 'swap',
})
const roboto = localFont({ src: './fonts/Roboto-Variable.ttf', variable: '--caption-font-roboto', weight: '100 900', display: 'swap' })
const openSans = localFont({ src: './fonts/OpenSans-Variable.ttf', variable: '--caption-font-open-sans', weight: '300 800', display: 'swap' })
const bebasNeue = localFont({ src: './fonts/BebasNeue-Regular.ttf', variable: '--caption-font-bebas-neue', weight: '400', display: 'swap' })
const anton = localFont({ src: './fonts/Anton-Regular.ttf', variable: '--caption-font-anton', weight: '400', display: 'swap' })
const nunito = localFont({ src: './fonts/Nunito-Variable.ttf', variable: '--caption-font-nunito', weight: '200 1000', display: 'swap' })

const FACES = {
  Inter: inter,
  Montserrat: montserrat,
  Poppins: poppins,
  Roboto: roboto,
  'Open Sans': openSans,
  'Bebas Neue': bebasNeue,
  Anton: anton,
  Nunito: nunito,
} satisfies Record<CaptionFontFamily, { variable: string }>

/** The families a composition may name, in the order the schema lists them. */
export const CAPTION_FONT_FAMILIES = Object.keys(FACES) as CaptionFontFamily[]

/** Every caption face's variable class, for the editor's root element. */
export const CAPTION_FONT_VARIABLES = Object.values(FACES)
  .map((face) => face.variable)
  .join(' ')

/** The CSS font stack that draws one composition family with its vendored face. */
export function captionFontStack(family: CaptionFontFamily): string {
  const slug = family.toLowerCase().replaceAll(' ', '-')
  return `var(--caption-font-${slug}), sans-serif`
}
```

Append to `frontend/vitest.setup.ts`:

```ts
import { vi } from 'vitest'

// `next/font` is a build-time transform; under Vitest a font is just its variable name.
vi.mock('next/font/local', () => ({
  default: (options: { variable?: string }) => ({
    className: 'font',
    variable: options.variable ?? 'font-variable',
    style: { fontFamily: 'sans-serif' },
  }),
}))
```

(If `import { vi }` conflicts with `globals: true`, drop the import — `vi` is global.)

- [ ] **Step 6: Use the faces in the preview**

In `features/editor/Player.tsx`, set the caption style's `fontFamily` to `captionFontStack(composition.captions.style.fontFamily)`, and add `fontSize`, `fontStyle: italic ? 'italic' : 'normal'`, `letterSpacing: `${letterSpacing}px``, `lineHeight`, `textDecoration`, and background (`backgroundColor` when `backgroundEnabled`) so the preview reflects every style field. The font size is canvas pixels, so scale it: wrap the caption paragraph in the canvas's own width via `style={{ fontSize: `calc(${style.fontSize} / ${composition.canvas.width} * 100cqw)` }}` and give the canvas `container-type: inline-size` (`[container-type:inline-size]` class).

In `EditorScreen.tsx`, add `CAPTION_FONT_VARIABLES` to the root `main` element's `className`.

- [ ] **Step 7: Install the fonts in the media image and verify them**

In `infra/docker/backend.Dockerfile` `media-root`, after the `apt-get` layer and before the readiness `RUN`:

```dockerfile
COPY frontend/features/editor/fonts/ /usr/local/share/fonts/clipah/
RUN rm -f /usr/local/share/fonts/clipah/*.txt /usr/local/share/fonts/clipah/*.md \
    && fc-cache --force /usr/local/share/fonts/clipah
```

In `backend/src/clipah/runtime/readiness.py`:

```python
from clipah.editor.models import FontFamily

CAPTION_FONT_FAMILIES = frozenset(family.value for family in FontFamily)
```

inside `verify()`'s `try`, add `caption_fonts = self._runner.run(("fc-list", "--format", "%{family}\n"))`, and before the final comparison:

```python
        installed = {
            name.strip() for line in caption_fonts.splitlines() for name in line.split(",")
        }
```

adding `or not installed >= CAPTION_FONT_FAMILIES` to the refusal condition.

- [ ] **Step 8: Run the tests and prove the image**

Run from `backend/`: `uv run pytest -q tests/unit/test_runtime_readiness.py tests/contract/test_runtime_deployment.py` → PASS.
Run: `pnpm --dir frontend exec vitest run tests/caption-fonts.test.ts` → PASS; `pnpm build` → PASS.
Run: `docker compose -f infra/compose.yaml build worker-render && docker compose -f infra/compose.yaml run --rm --no-deps worker-render python -m clipah.runtime.readiness media` → prints `ready`. Then `docker compose -f infra/compose.yaml run --rm --no-deps worker-render fc-list --format '%{family}\n' | sort -u` lists all eight families.

---

### Task 4: Studio layout, transport, and keyboard map

**Files:**
- Create: `frontend/features/editor/use-editor-keys.ts`, `frontend/features/editor/ShortcutSheet.tsx`, `frontend/features/editor/TransportBar.tsx`
- Modify: `frontend/features/editor/EditorScreen.tsx` (layout; `useShortcuts` moves out), `frontend/features/editor/Player.tsx` (the play button and time readout move to the transport)
- Modify: `frontend/app/globals.css` (timeline scrub skin, used in Task 5)
- Create: `frontend/tests/editor-studio.test.tsx`

**Interfaces:**
- Produces:
  - `useEditorKeys(handlers: { onPlayPause; onUndo; onRedo; onSplit; onDelete; onZoomIn; onZoomOut; onSave; onStep: (deltaMs: number) => void; onAddMarker; onHelp }): void`.
  - `FRAME_MS = 1000 / 30`.
  - `TransportBar({ playing: boolean; playheadMs: number; durationMs: number; loop: boolean; onPlayingChange: (playing: boolean) => void; onSeek: (ms: number) => void; onLoop: (loop: boolean) => void })`.
  - `ShortcutSheet({ open: boolean; onOpenChange: (open: boolean) => void })`.
  - Layout landmarks: `nav "Editing tools"` (tool rail, `role="tablist"` inside), `aside "Tool panel"`, `section "Stage"` (contains `group "Canvas shape"`, the preview, the transport), `aside "Properties"` (contains the `Inspector` region), `section "Editing lanes"` (the timeline dock with a `separator "Resize the timeline"`).

- [ ] **Step 1: Write the failing tests**

`frontend/tests/editor-studio.test.tsx` (reuse the stubs from `editor-basic.test.tsx` — copy its `ME`, `WORKSPACES`, `SHOW_EDIT`, `PROXY`, `SAVE_EDIT` constants and `stubApi` block):

```tsx
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { EditorScreen } from '@/features/editor/EditorScreen'
import type { CompositionV1 } from '@/lib/api/generated/model'

import { renderWithApi, stubApi } from './support/api'
import { currentUser, edit, workspace } from './support/fixtures'

const EDIT_ID = edit().id
const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const SHOW_EDIT = `GET /api/v1/edits/${EDIT_ID}`
const SAVE_EDIT = `PUT /api/v1/edits/${EDIT_ID}`
const PROXY = `GET /api/v1/projects/${edit().projectId}/proxy`

vi.mock('next/navigation', () => ({
  usePathname: () => `/editor/${EDIT_ID}`,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}))

beforeEach(() => {
  window.sessionStorage.clear()
  stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [SHOW_EDIT]: { body: edit() },
    [PROXY]: { body: { url: 'https://storage.test/proxy.mp4', expiresAt: '2026-02-01T00:05:00+00:00', contentType: 'video/mp4', durationMs: 60_000, width: 1920, height: 1080 } },
    [SAVE_EDIT]: (request) => ({ body: { ...edit(), currentRevision: 2, composition: (request.body as { composition: CompositionV1 }).composition } }),
  })
})

async function openEditor() {
  renderWithApi(<EditorScreen editId={EDIT_ID} />)
  await screen.findByRole('region', { name: /^timeline$/i })
}

describe('the studio layout', () => {
  test('has a tool rail, a stage with its canvas shape and transport, properties, and the lanes', async () => {
    await openEditor()

    expect(within(screen.getByRole('navigation', { name: 'Editing tools' })).getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Captions', 'Style', 'Layout', 'Media', 'Audio', 'Text', 'Review',
    ])
    const stage = screen.getByRole('region', { name: 'Stage' })
    expect(within(stage).getByRole('group', { name: 'Canvas shape' })).toBeInTheDocument()
    expect(within(stage).getByRole('group', { name: 'Transport' })).toBeInTheDocument()
    expect(within(screen.getByRole('complementary', { name: 'Properties' })).getByRole('region', { name: 'Inspector' })).toBeInTheDocument()
    expect(screen.getByRole('separator', { name: 'Resize the timeline' })).toHaveAttribute('aria-valuenow')
  })

  test('the transport plays, steps, and reads the playhead as a timecode', async () => {
    const user = userEvent.setup()
    await openEditor()
    const transport = screen.getByRole('group', { name: 'Transport' })

    await user.click(within(transport).getByRole('button', { name: 'Next frame' }))
    expect(within(transport).getByText('0:00.03 / 0:30.00')).toBeInTheDocument()
    await user.click(within(transport).getByRole('button', { name: 'Jump to end' }))
    expect(within(transport).getByText('0:30.00 / 0:30.00')).toBeInTheDocument()
    expect(within(transport).getByRole('button', { name: 'Play' })).toBeInTheDocument()
  })

  test('arrow keys step frames and seconds, and M leaves a marker', async () => {
    const user = userEvent.setup()
    await openEditor()
    const transport = screen.getByRole('group', { name: 'Transport' })

    await user.keyboard('{Shift>}{ArrowRight}{/Shift}{ArrowRight}')
    expect(within(transport).getByText(/^0:01\.03 \//)).toBeInTheDocument()

    await user.keyboard('m')
    expect(await screen.findByRole('button', { name: /^0:01 Marker/ })).toBeInTheDocument()
  })

  test('? opens the shortcut sheet', async () => {
    const user = userEvent.setup()
    await openEditor()

    await user.keyboard('?')

    const sheet = await screen.findByRole('dialog', { name: 'Editor shortcuts' })
    for (const key of ['Space', 'S', '⌘Z', '⇧⌘Z', '⌘S', '← →', '⇧← ⇧→', 'M', '+ −']) {
      expect(within(sheet).getByText(key)).toBeInTheDocument()
    }
  })

  test('the timeline dock resizes from the keyboard', async () => {
    await openEditor()
    const handle = screen.getByRole('separator', { name: 'Resize the timeline' })
    const before = Number(handle.getAttribute('aria-valuenow'))

    fireEvent.keyDown(handle, { key: 'ArrowUp' })

    await waitFor(() => expect(Number(handle.getAttribute('aria-valuenow'))).toBe(before + 24))
  })
})
```

The marker label format (`0:01 Marker 1`) is decided in Task 5; this test asserts the prefix only.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/editor-studio.test.tsx`
Expected: FAIL — no Stage region, transport, separator, or sheet.

- [ ] **Step 3: Implement `use-editor-keys.ts`**

Move `useShortcuts` and `isTextEntry` from `EditorScreen.tsx` into this file as `useEditorKeys`, keeping every existing branch, and add before the `' '` branch:

```ts
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault()
        const direction = event.key === 'ArrowRight' ? 1 : -1
        current.current.onStep(direction * (event.shiftKey ? 1_000 : FRAME_MS))
        return
      }
      if (event.key === 'm' || event.key === 'M') {
        current.current.onAddMarker()
        return
      }
      if (event.key === '?') {
        current.current.onHelp()
        return
      }
```

with `export const FRAME_MS = 1000 / 30`. Ignore keys while any `[role="dialog"]` is open, like review mode does. Timeline items already use `Alt`/`Shift` + arrows for moving and stretching while focused; those handlers call `preventDefault`, so guard the window handler with `if (event.defaultPrevented) return` at the top.

- [ ] **Step 4: Implement `TransportBar.tsx` and `ShortcutSheet.tsx`**

`TransportBar.tsx`:

```tsx
'use client'

import { ChevronLeft, ChevronRight, Pause, Play, Repeat, SkipBack, SkipForward } from 'lucide-react'

import { IconButton } from '@/components/ui/icon-button'
import { formatTimecode } from '@/lib/time/timecode'

import { FRAME_MS } from './use-editor-keys'

/** Play, step, jump, loop, and the playhead as a timecode. */
export function TransportBar({
  playing,
  playheadMs,
  durationMs,
  loop,
  onPlayingChange,
  onSeek,
  onLoop,
}: {
  playing: boolean
  playheadMs: number
  durationMs: number
  loop: boolean
  onPlayingChange: (playing: boolean) => void
  onSeek: (ms: number) => void
  onLoop: (loop: boolean) => void
}) {
  const clamp = (ms: number) => Math.min(durationMs, Math.max(0, Math.round(ms)))
  return (
    <div role="group" aria-label="Transport" className="flex items-center justify-center gap-1 rounded-md border border-border bg-card px-2 py-1">
      <IconButton label="Jump to start" icon={<SkipBack strokeWidth={1.75} />} size="sm" onClick={() => onSeek(0)} />
      <IconButton label="Previous frame" shortcut="←" icon={<ChevronLeft strokeWidth={1.75} />} size="sm" onClick={() => onSeek(clamp(playheadMs - FRAME_MS))} />
      <IconButton
        label={playing ? 'Pause' : 'Play'}
        shortcut="Space"
        variant="secondary"
        icon={playing ? <Pause strokeWidth={1.75} /> : <Play strokeWidth={1.75} />}
        onClick={() => onPlayingChange(!playing)}
      />
      <IconButton label="Next frame" shortcut="→" icon={<ChevronRight strokeWidth={1.75} />} size="sm" onClick={() => onSeek(clamp(playheadMs + FRAME_MS))} />
      <IconButton label="Jump to end" icon={<SkipForward strokeWidth={1.75} />} size="sm" onClick={() => onSeek(durationMs)} />
      <output aria-label="Playhead" className="mx-2 font-mono text-small tabular text-foreground">
        {formatTimecode(playheadMs)} / {formatTimecode(durationMs)}
      </output>
      <IconButton label="Loop the clip" aria-pressed={loop} icon={<Repeat strokeWidth={1.75} />} size="sm" onClick={() => onLoop(!loop)} />
    </div>
  )
}
```

The Task 4 test expects `0:00.03` after one frame: `Math.round(33.33) = 33` ms → `0:00.03`.

`ShortcutSheet.tsx`:

```tsx
'use client'

import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'

const SHORTCUTS = [
  ['Space', 'Play or pause'],
  ['S', 'Split at the playhead'],
  ['Delete', 'Delete the selected item'],
  ['⌘Z', 'Undo'],
  ['⇧⌘Z', 'Redo'],
  ['⌘S', 'Save now'],
  ['← →', 'Step one frame'],
  ['⇧← ⇧→', 'Step one second'],
  ['M', 'Add a marker at the playhead'],
  ['+ −', 'Zoom the timeline'],
  ['?', 'Show these shortcuts'],
] as const

/** Every editor shortcut in one place. */
export function ShortcutSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-sm">
        <SheetHeader>
          <SheetTitle>Editor shortcuts</SheetTitle>
        </SheetHeader>
        <dl className="mt-6 grid grid-cols-[5rem_minmax(0,1fr)] gap-x-4 gap-y-2.5">
          {SHORTCUTS.map(([key, action]) => (
            <div key={action} className="contents">
              <dt><kbd className="rounded-sm border border-line-strong px-1.5 py-0.5 font-mono text-caption">{key}</kbd></dt>
              <dd className="text-small">{action}</dd>
            </div>
          ))}
        </dl>
      </SheetContent>
    </Sheet>
  )
}
```

- [ ] **Step 5: Lay out the studio in `EditorScreen.tsx`**

Replace `TOOLS` with the new order and `LoadedEditor`'s `return (…)` with the layout below; keep every handler, `ToolPanel`, `RevisionQualityPanels`, `compositionChangeFor`, and the conflict banner as they are. Add state: `const [loop, setLoop] = useState(false)`, `const [helpOpen, setHelpOpen] = useState(false)`, `const [laneHeight, setLaneHeight] = useState(240)`, `const [propertiesOpen, setPropertiesOpen] = useState(false)`, and replace `useShortcuts({...})` with `useEditorKeys({ ...existing, onStep: (delta) => dispatch({ type: 'seek', ms: Math.min(composition.durationMs, Math.max(0, Math.round(state.playheadMs + delta))) }), onAddMarker: () => dispatch({ type: 'addBookmark', label: `Marker ${composition.bookmarks.length + 1}` }), onHelp: () => setHelpOpen(true) })`.

```tsx
const TOOLS = [
  { id: 'captions', label: 'Captions', icon: Captions },
  { id: 'style', label: 'Style', icon: Palette },
  { id: 'layout', label: 'Layout', icon: Crop },
  { id: 'media', label: 'Media', icon: Film },
  { id: 'audio', label: 'Audio', icon: AudioLines },
  { id: 'text', label: 'Text', icon: Type },
  { id: 'review', label: 'Review', icon: ClipboardCheck },
] as const
```

```tsx
  return (
    <main className={cn('flex min-h-screen flex-col bg-background md:h-screen', CAPTION_FONT_VARIABLES)}>
      <header className="flex h-12 shrink-0 items-center gap-3 border-b bg-card px-3">
        <Link href={`/dashboard/projects/${edit.projectId}`} className="inline-flex min-w-0 items-center gap-1.5 rounded-md px-2 py-1.5 text-small font-medium text-muted-foreground hover:bg-secondary hover:text-foreground">
          <ArrowLeft aria-hidden="true" strokeWidth={1.75} className="size-4 shrink-0" />
          <span className="max-w-48 truncate">{project.data?.name ?? 'Back to project'}</span>
        </Link>
        <div className="min-w-0">
          <h1 className="truncate text-small font-semibold">Editing clip</h1>
          <p className="font-mono text-caption text-subtle-foreground">Revision {autosave.expectedRevision}</p>
        </div>
        <p role="status" className={cn('text-caption font-medium', status === 'conflict' || status === 'offline' ? 'text-warning' : 'text-muted-foreground')}>
          {saveLabel}
        </p>
        <div className="ml-auto flex items-center gap-1">
          <IconButton label="Undo" shortcut="⌘Z" icon={<Undo2 strokeWidth={1.75} />} disabled={!canUndo(state)} onClick={() => dispatch({ type: 'undo' })} />
          <IconButton label="Redo" shortcut="⇧⌘Z" icon={<Redo2 strokeWidth={1.75} />} disabled={!canRedo(state)} onClick={() => dispatch({ type: 'redo' })} />
          <IconButton label="Editor shortcuts" shortcut="?" icon={<Keyboard strokeWidth={1.75} />} onClick={() => setHelpOpen(true)} />
          <IconButton label="Properties" icon={<SlidersHorizontal strokeWidth={1.75} />} className="lg:hidden" aria-expanded={propertiesOpen} onClick={() => setPropertiesOpen(true)} />
          <Button variant="ghost" size="sm" onClick={save}>Save</Button>
          <Button size="sm" onClick={() => setExporting(true)}>
            <Upload aria-hidden="true" strokeWidth={1.75} /> Export
          </Button>
        </div>
      </header>

      {conflict === null ? null : (/* existing conflict banner, restyled: border-b border-warning/40 bg-warning-soft; buttons as Button variant="secondary" size="sm" */)}
      {proxyError === null ? null : <div className="px-4 pt-3"><ErrorNotice error={proxyError} /></div>}

      <p className="mx-4 mt-3 rounded-md border border-line-strong bg-card p-3 text-small text-muted-foreground md:hidden">
        This is a preview. Captions, timing, and layout are edited on a larger screen — open this clip on a tablet or computer to continue. Export and download work here.
      </p>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <nav aria-label="Editing tools" className="hidden w-14 shrink-0 border-r bg-card md:flex md:flex-col md:items-center md:gap-1 md:py-2">
          <div role="tablist" aria-orientation="vertical" aria-label="Editing tools" className="flex flex-col gap-1">
            {TOOLS.map((entry) => {
              const Icon = entry.icon
              return (
                <button
                  key={entry.id}
                  type="button"
                  role="tab"
                  id={`editor-tool-${entry.id}`}
                  aria-selected={tool === entry.id}
                  aria-controls={`editor-panel-${entry.id}`}
                  onClick={() => setTool(entry.id)}
                  className={cn(
                    'flex w-12 flex-col items-center gap-0.5 rounded-md py-2 text-[11px] font-medium transition-colors duration-fast ease-signal',
                    tool === entry.id ? 'bg-secondary text-primary' : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
                  )}
                >
                  <Icon aria-hidden="true" strokeWidth={1.75} className="size-5" />
                  {entry.label}
                </button>
              )
            })}
          </div>
        </nav>

        <aside aria-label="Tool panel" className="hidden w-80 shrink-0 overflow-y-auto border-r bg-card md:block">
          {/* The existing ToolPanel blocks, in TOOLS order. Task 7 replaces the captions
              panel's contents, Task 8 moves CaptionsPanel style controls, TemplatesPanel,
              MotionPanel, and KeyframeEditor into the Style panel, and Task 9 fills Layout. */}
        </aside>

        <section aria-label="Stage" className="flex min-w-0 flex-1 flex-col gap-3 bg-stage p-3 lg:p-4">
          <div className="flex items-center justify-center">
            <SegmentedControl
              label="Canvas shape"
              size="sm"
              value={currentAspect(composition)}
              options={(Object.keys(ASPECT_CANVAS) as Aspect[]).map((aspect) => ({ value: aspect, label: aspect }))}
              onChange={(aspect) => dispatch({ type: 'aspect', aspect, sourceAspect })}
            />
          </div>
          <div className="flex min-h-0 flex-1 items-center justify-center">
            {proxy === null ? (
              <p role="status" className="text-caption text-muted-foreground">Loading the preview…</p>
            ) : (
              <Player composition={composition} source={proxy} playheadMs={state.playheadMs} playing={playing} loop={loop} engine={engine} onSeek={(ms) => dispatch({ type: 'seek', ms })} onPlayingChange={onPlaying} />
            )}
          </div>
          <TransportBar playing={playing} playheadMs={state.playheadMs} durationMs={composition.durationMs} loop={loop} onPlayingChange={onPlaying} onSeek={(ms) => dispatch({ type: 'seek', ms })} onLoop={setLoop} />
        </section>

        <aside aria-label="Properties" className="hidden w-72 shrink-0 overflow-y-auto border-l bg-card p-3 lg:block">
          {inspector}
        </aside>
      </div>

      <section aria-label="Editing lanes" className="hidden shrink-0 flex-col border-t bg-card md:flex" style={{ height: laneHeight }}>
        <div
          role="separator"
          aria-label="Resize the timeline"
          aria-orientation="horizontal"
          aria-valuemin={160}
          aria-valuemax={520}
          aria-valuenow={laneHeight}
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === 'ArrowUp') setLaneHeight((height) => Math.min(520, height + 24))
            if (event.key === 'ArrowDown') setLaneHeight((height) => Math.max(160, height - 24))
          }}
          onPointerDown={(event) => {
            const startY = event.clientY
            const startHeight = laneHeight
            const move = (moveEvent: PointerEvent) => setLaneHeight(Math.min(520, Math.max(160, startHeight + startY - moveEvent.clientY)))
            const up = () => {
              window.removeEventListener('pointermove', move)
              window.removeEventListener('pointerup', up)
            }
            window.addEventListener('pointermove', move)
            window.addEventListener('pointerup', up)
          }}
          className="h-1.5 shrink-0 cursor-row-resize bg-border hover:bg-line-strong focus-visible:bg-primary"
        />
        {/* TimelineToolbar and Timeline, unchanged props until Task 5 */}
      </section>

      <Sheet open={propertiesOpen} onOpenChange={setPropertiesOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-sm lg:hidden">
          <SheetHeader><SheetTitle>Properties</SheetTitle></SheetHeader>
          <div className="mt-4">{propertiesOpen ? inspector : null}</div>
        </SheetContent>
      </Sheet>
      <ShortcutSheet open={helpOpen} onOpenChange={setHelpOpen} />
      {/* existing ExportDialog */}
    </main>
  )
```

where `const inspector = <Inspector … />` is the existing `Inspector` element built once above the return (the `onAspect` prop is removed in Task 6; until then pass it through). The Sheet renders its inspector only while open so the page never holds two `Inspector` regions in the DOM. Move `currentAspect` from `Inspector.tsx` to `store.ts` as an export so both files use it.

In `Player.tsx`: delete the play button and `<output aria-label="Playhead">` row (the transport owns them); accept `loop: boolean` and, in `follow`, when `clipMs >= composition.durationMs` seek to 0 and keep playing if `loop` is true instead of stopping; set the canvas `maxWidth` to `min(100%, calc((100vh - 26rem) * ${aspect}))`, remove `rounded-lg shadow-md`, and add `[container-type:inline-size]`.

Append to `frontend/app/globals.css` in `@layer components` (Task 5 uses it):

```css
  /* The timeline ruler is a native range: the track is invisible, the thumb is the playhead. */
  .timeline-scrub {
    appearance: none;
    background: transparent;
    cursor: pointer;
  }
  .timeline-scrub::-webkit-slider-runnable-track {
    height: 100%;
    background: transparent;
  }
  .timeline-scrub::-webkit-slider-thumb {
    appearance: none;
    width: 11px;
    height: 32px;
    border-radius: 2px;
    background: rgb(var(--primary));
    clip-path: polygon(0 0, 100% 0, 100% 40%, 50% 60%, 0 40%);
  }
  .timeline-scrub::-moz-range-track {
    background: transparent;
  }
  .timeline-scrub::-moz-range-thumb {
    width: 11px;
    height: 32px;
    border: 0;
    border-radius: 2px;
    background: rgb(var(--primary));
  }
```

- [ ] **Step 6: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/editor-studio.test.tsx tests/editor-basic.test.tsx tests/editor-advanced.test.tsx tests/editor-styling.test.tsx tests/creator-studio.test.tsx`
Expected: the studio tests PASS. In the existing editor suites, only assertions on the removed Play button or the moved aspect buttons may fail — the aspect buttons keep their names (`16:9`, `1:1`) and `aria-pressed`, so those pass; fix any remaining failure by querying the same control in its new place, not by weakening the assertion.

---

### Task 5: Timeline

**Files:**
- Create: `frontend/features/editor/caption-phrases.ts`
- Modify: `frontend/features/editor/Timeline.tsx` (rewrite), `frontend/features/editor/TimelineToolbar.tsx` (rewrite), `frontend/features/editor/EditorScreen.tsx` (pass media and toolbar props)
- Modify: `frontend/tests/editor-advanced.test.tsx`, `frontend/tests/editor-basic.test.tsx` (toggle queries)
- Create: `frontend/tests/timeline.test.tsx`

**Interfaces:**
- Consumes: `Filmstrip`, `WaveformCanvas`, `Slider`, `IconButton`, `formatRuler`, `formatTimecode`, `useStoryboard`, `useWaveform`.
- Produces:
  - `captionPhrases(words: CaptionWord[], { maxWords?: number; maxGapMs?: number }): Array<{ id: string; startMs: number; endMs: number; text: string }>` (defaults 6 words, 600 ms).
  - `fitZoom(containerPx: number, durationMs: number): number` (largest `ZOOM_LEVELS` entry whose whole clip fits, else the smallest).
  - `Timeline` props: existing minus `onZoom` (zoom controls move to the toolbar) plus `storyboard: StoryboardResponse | null`, `peaks: Uint8Array | null`, `peaksPerSecond: number | null`, `sourceAssetId: string`.
  - `TimelineToolbar` props: existing (`snapping`, `ripple`, `hasSelection`, `markerCount`, handlers) plus `zoom: number`, `onZoom: (zoom: number) => void`, `onFit: () => void`.

- [ ] **Step 1: Write the failing tests and update the toggle queries**

`frontend/tests/timeline.test.tsx`:

```tsx
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { captionPhrases } from '@/features/editor/caption-phrases'
import { Timeline, fitZoom } from '@/features/editor/Timeline'
import { TimelineToolbar } from '@/features/editor/TimelineToolbar'

import { composition } from './support/fixtures'

function renderTimeline(overrides: Partial<Parameters<typeof Timeline>[0]> = {}) {
  const handlers = { onSelect: vi.fn(), onSelectTrack: vi.fn(), onSeek: vi.fn(), onMove: vi.fn(), onResize: vi.fn(), onRemoveMarker: vi.fn() }
  render(
    <Timeline
      composition={composition({ bookmarks: [{ id: 'b1', label: 'Payoff', timelineMs: 12_000 }] })}
      selectedItemId={null}
      selectedTrackId={null}
      playheadMs={5_000}
      zoom={16}
      snapping
      lockedTrackIds={[]}
      storyboard={null}
      peaks={null}
      peaksPerSecond={null}
      sourceAssetId={composition().sourceAssetId}
      {...handlers}
      {...overrides}
    />,
  )
  return handlers
}

describe('caption phrases', () => {
  test('group words into short phrases, breaking at pauses', () => {
    const words = composition().captions.words
    expect(captionPhrases(words).map((phrase) => phrase.text)).toEqual(['Ini cara', 'kerja', 'editornya'])
  })
})

describe('Timeline', () => {
  test('the ruler is the scrub control and names the playhead position', () => {
    const { onSeek } = renderTimeline()

    const scrub = screen.getByRole('slider', { name: 'Scrub the clip' })
    expect(scrub).toHaveAttribute('aria-valuetext', '0:05.00')
    fireEvent.change(scrub, { target: { value: '12000' } })

    expect(onSeek).toHaveBeenCalledWith(12_000)
    expect(screen.getByTestId('editor-playhead')).toHaveStyle({ left: '80px' })
  })

  test('each lane has a label column, and markers sit on the ruler as flags', async () => {
    const user = userEvent.setup()
    const { onSeek, onRemoveMarker } = renderTimeline()

    const timeline = screen.getByRole('region', { name: 'Timeline' })
    expect(within(timeline).getByText('Video')).toBeInTheDocument()
    expect(within(timeline).getByRole('group', { name: 'Captions lane' })).toBeInTheDocument()
    await user.click(within(timeline).getByRole('button', { name: '0:12 Payoff' }))
    await user.click(within(timeline).getByRole('button', { name: 'Remove the marker Payoff' }))

    expect(onSeek).toHaveBeenCalledWith(12_000)
    expect(onRemoveMarker).toHaveBeenCalledWith('b1')
  })

  test('video items show frames when a storyboard exists', () => {
    renderTimeline({
      storyboard: { version: 1, intervalMs: 2_000, tileWidth: 160, tileHeight: 90, columns: 10, rows: 10, durationMs: 60_000, expiresAt: '2026-02-01T00:05:00+00:00', sheets: [{ index: 0, startMs: 0, tileCount: 30, url: 'https://media.test/0.jpg' }] },
    })

    const item = screen.getByRole('button', { name: 'Select scene-1' })
    expect(within(item).getAllByTestId('sprite').length).toBeGreaterThan(0)
  })

  test('fit picks the closest zoom that shows the whole clip', () => {
    expect(fitZoom(1_000, 30_000)).toBe(32)
    expect(fitZoom(100, 600_000)).toBe(4)
  })
})

describe('TimelineToolbar', () => {
  test('tools are named icon buttons and snap and ripple are pressed toggles', async () => {
    const user = userEvent.setup()
    const onRipple = vi.fn()
    render(
      <TimelineToolbar
        snapping
        ripple={false}
        hasSelection
        markerCount={0}
        zoom={16}
        onSnapping={vi.fn()}
        onRipple={onRipple}
        onSplit={vi.fn()}
        onSplitAwayLeft={vi.fn()}
        onSplitAwayRight={vi.fn()}
        onDuplicate={vi.fn()}
        onDelete={vi.fn()}
        onAddMarker={vi.fn()}
        onPreviousMarker={vi.fn()}
        onNextMarker={vi.fn()}
        onAddTrack={vi.fn()}
        onZoom={vi.fn()}
        onFit={vi.fn()}
      />,
    )

    for (const name of ['Split', 'Split away the left', 'Split away the right', 'Duplicate', 'Delete', 'Add marker', 'Add a music lane', 'Add an audio lane', 'Zoom in', 'Zoom out', 'Fit the clip']) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument()
    }
    expect(screen.getByRole('button', { name: 'Snap to edges' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'Ripple edits' }))
    expect(onRipple).toHaveBeenCalledWith(true)
  })
})
```

Check the expectations: playhead 5 s at 16 px/s → 80 px; `fitZoom(1000, 30000)`: 32 px/s × 30 s = 960 ≤ 1000, 64 → 1920 > 1000 → 32; `fitZoom(100, 600000)`: even 4 px/s gives 2400 > 100 → smallest, 4. Caption fixture words start at 0, 1,000, 12,000, 20,000 (900 ms each): "Ini" and "cara" are 100 ms apart → one phrase; 12,000 is 10.1 s after 1,900 → new phrase; 20,000 → new phrase.

In `frontend/tests/editor-advanced.test.tsx` and `frontend/tests/editor-basic.test.tsx`, replace `getByRole('checkbox', { name: /ripple/i })` with `getByRole('button', { name: /ripple/i })` (clicking toggles `aria-pressed`), and any snap checkbox query likewise. `getByLabelText(/scrub the clip/i)` with `fireEvent.change` keeps working because the scrub is still a native range. Replace `getByLabelText(/marker label/i)` typing with the unchanged `Marker label` field (it stays in the toolbar).

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/timeline.test.tsx`
Expected: FAIL — `caption-phrases` and `fitZoom` missing, no lane labels, toggles are checkboxes.

- [ ] **Step 3: Implement `caption-phrases.ts`**

```ts
import type { CompositionV1 } from '@/lib/api/generated/model'

type CaptionWord = CompositionV1['captions']['words'][number]

/** Words grouped the way a caption lane draws them: a few words, broken at pauses. */
export function captionPhrases(
  words: readonly CaptionWord[],
  { maxWords = 6, maxGapMs = 600 }: { maxWords?: number; maxGapMs?: number } = {},
): Array<{ id: string; startMs: number; endMs: number; text: string }> {
  const phrases: Array<{ id: string; startMs: number; endMs: number; text: string }> = []
  let current: CaptionWord[] = []
  const flush = () => {
    const first = current[0]
    const last = current.at(-1)
    if (first !== undefined && last !== undefined) {
      phrases.push({ id: first.id, startMs: first.startMs, endMs: last.endMs, text: current.map((word) => word.text).join(' ') })
    }
    current = []
  }
  for (const word of words) {
    const last = current.at(-1)
    if (last !== undefined && (word.startMs - last.endMs > maxGapMs || current.length >= maxWords)) flush()
    current.push(word)
  }
  flush()
  return phrases
}
```

- [ ] **Step 4: Rewrite `Timeline.tsx`**

Keep the gesture logic (`resolve`, the window `pointerup` effect, `begin`, `onItemKeyDown`), `overlayLabel`, `itemOf`, and `ZOOM_LEVELS`/`KEYBOARD_STEP_MS`. Replace the render with a two-column layout and add `fitZoom`:

```tsx
/** The largest zoom that fits the whole clip in a container, or the smallest zoom. */
export function fitZoom(containerPx: number, durationMs: number): number {
  const fitting = [...ZOOM_LEVELS].reverse().find((level) => (durationMs / 1000) * level <= containerPx)
  return fitting ?? ZOOM_LEVELS[0]
}

const LANE_NAMES: Record<string, string> = { video: 'Video', audio: 'Audio', music: 'Music', extractedAudio: 'Extracted audio' }
const LANE_HEIGHT = 'h-12'
```

```tsx
  const width = Math.max(composition.durationMs * pixelsPerMs, 1)
  const ticks = rulerTicks(composition.durationMs, zoom)
  const phrases = useMemo(() => captionPhrases(composition.captions.words), [composition.captions.words])

  return (
    <section aria-label="Timeline" className="flex min-h-0 flex-1 overflow-auto">
      <div className="sticky left-0 z-20 w-28 shrink-0 border-r bg-card">
        <div className="h-8 border-b" />
        {composition.tracks.map((track, index) => (
          <div key={track.id} className={`${LANE_HEIGHT} flex items-center justify-between gap-1 border-b px-2`}>
            <span className="truncate text-caption text-muted-foreground">
              {LANE_NAMES[track.type] ?? track.type}
              {composition.tracks.filter((other) => other.type === track.type).length > 1 ? ` ${index + 1}` : ''}
            </span>
            <button
              type="button"
              aria-label={`Select the lane ${track.id}`}
              aria-pressed={track.id === selectedTrackId}
              onClick={() => onSelectTrack(track.id)}
              className={cn('size-4 rounded-sm border', track.id === selectedTrackId ? 'border-primary bg-primary' : 'border-input')}
            />
          </div>
        ))}
        <div className={`${LANE_HEIGHT} flex items-center border-b px-2 text-caption text-muted-foreground`}>Captions</div>
        {composition.overlays.length === 0 ? null : (
          <div className={`${LANE_HEIGHT} flex items-center border-b px-2 text-caption text-muted-foreground`}>Overlays</div>
        )}
      </div>

      <div className="relative shrink-0" style={{ width }}>
        <div className="relative h-8 border-b bg-card">
          <ol aria-hidden="true" className="absolute inset-0">
            {ticks.map((tick) => (
              <li key={tick} className="absolute top-0 h-full border-l border-line-strong pl-1 font-mono text-[10px] leading-4 text-subtle-foreground" style={{ left: tick * pixelsPerMs }}>
                {formatRuler(tick)}
              </li>
            ))}
          </ol>
          <Slider
            aria-label="Scrub the clip"
            aria-valuetext={formatTimecode(playheadMs)}
            min={0}
            max={composition.durationMs}
            step={10}
            value={playheadMs}
            onChange={(event) => onSeek(Number(event.currentTarget.value))}
            className="timeline-scrub absolute inset-0 z-10 h-8"
          />
          <ul aria-label="Markers" className="absolute inset-x-0 bottom-0 z-20 h-3.5">
            {composition.bookmarks.map((bookmark) => (
              <li key={bookmark.id} className="group absolute bottom-0 flex items-center" style={{ left: bookmark.timelineMs * pixelsPerMs }}>
                <button type="button" onClick={() => onSeek(bookmark.timelineMs)} className="rounded-t-sm bg-warning px-1 font-mono text-[10px] leading-3.5 text-background">
                  {formatRuler(bookmark.timelineMs)} {bookmark.label}
                </button>
                <button
                  type="button"
                  aria-label={`Remove the marker ${bookmark.label}`}
                  onClick={() => onRemoveMarker(bookmark.id)}
                  className="ml-0.5 hidden rounded-sm bg-popover px-1 text-[10px] leading-3.5 group-focus-within:block group-hover:block"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </div>

        {composition.tracks.map((track) => (
          <div key={track.id} role="group" aria-label={`${track.type} lane ${track.id}`} className={`relative ${LANE_HEIGHT} border-b`}>
            {[...track.items].sort((left, right) => left.timelineStartMs - right.timelineStartMs).map((item) => {
              const lengthMs = item.sourceOutMs - item.sourceInMs
              const itemPx = Math.max(lengthMs * pixelsPerMs, 32)
              const locked = lockedTrackIds.includes(track.id)
              const chosen = item.id === selectedItemId
              return (
                <div key={item.id} className="absolute inset-y-1 flex" style={{ left: item.timelineStartMs * pixelsPerMs, width: itemPx }}>
                  <button type="button" aria-label={`Trim the start of ${item.id}`} aria-disabled={locked} onPointerDown={(event) => begin(item, track, 'start', event.clientX)} className="w-1.5 shrink-0 cursor-ew-resize rounded-l-sm bg-line-strong hover:bg-primary" />
                  <button
                    type="button"
                    aria-label={`Select ${item.id}`}
                    aria-pressed={chosen}
                    aria-disabled={locked}
                    onPointerDown={(event) => begin(item, track, 'move', event.clientX)}
                    onKeyDown={(event) => onItemKeyDown(event, item, track)}
                    onClick={() => onSelect(item.id)}
                    className={cn(
                      'relative min-w-0 flex-1 overflow-hidden border-y text-left',
                      chosen ? 'border-primary' : 'border-line-strong',
                      dragging === item.id && 'opacity-70',
                      locked && 'cursor-not-allowed opacity-60',
                    )}
                  >
                    {track.type === 'video' ? (
                      <Filmstrip storyboard={storyboard} startMs={item.sourceInMs} endMs={item.sourceOutMs} tileCount={Math.max(1, Math.round(itemPx / 64))} className="absolute inset-0" />
                    ) : item.sourceAssetId === sourceAssetId ? (
                      <WaveformCanvas peaks={peaks} peaksPerSecond={peaksPerSecond} startMs={item.sourceInMs} endMs={item.sourceOutMs} className="absolute inset-0" />
                    ) : (
                      <span aria-hidden="true" className="absolute inset-0 bg-secondary" />
                    )}
                    <span className="absolute left-1 top-0.5 max-w-[calc(100%-0.5rem)] truncate rounded-sm bg-background/80 px-1 font-mono text-[10px] text-foreground">
                      {item.id}
                    </span>
                  </button>
                  <button type="button" aria-label={`Trim the end of ${item.id}`} aria-disabled={locked} onPointerDown={(event) => begin(item, track, 'end', event.clientX)} className="w-1.5 shrink-0 cursor-ew-resize rounded-r-sm bg-line-strong hover:bg-primary" />
                </div>
              )
            })}
          </div>
        ))}

        <div role="group" aria-label="Captions lane" className={`relative ${LANE_HEIGHT} border-b`}>
          {phrases.map((phrase) => (
            <button
              key={phrase.id}
              type="button"
              onClick={() => onSeek(phrase.startMs)}
              className="absolute inset-y-2 truncate rounded-sm bg-secondary px-1.5 text-left text-caption text-foreground hover:bg-line-strong"
              style={{ left: phrase.startMs * pixelsPerMs, width: Math.max((phrase.endMs - phrase.startMs) * pixelsPerMs, 24) }}
            >
              {phrase.text}
            </button>
          ))}
        </div>

        {composition.overlays.length === 0 ? null : (/* the existing overlays lane, restyled: rounded-sm border border-dashed border-line-strong bg-secondary text-caption, inset-y-2 */)}

        <div data-testid="editor-playhead" aria-hidden="true" className="pointer-events-none absolute bottom-0 top-8 z-10 w-px bg-primary" style={{ left: playheadMs * pixelsPerMs }} />
      </div>
    </section>
  )
```

Update `rulerTicks` to space ticks so labels never overlap: every 1 s at 64 px/s, 2 s at 32, 5 s at 16, 10 s at 8, 30 s at 4.

- [ ] **Step 5: Rewrite `TimelineToolbar.tsx`**

```tsx
'use client'

import {
  ArrowLeftToLine,
  ArrowRightToLine,
  AudioLines,
  ChevronLeft,
  ChevronRight,
  Copy,
  Flag,
  Magnet,
  Maximize2,
  Minus,
  Music,
  Plus,
  Scissors,
  Trash2,
  WrapText,
} from 'lucide-react'
import { useState } from 'react'

import { IconButton } from '@/components/ui/icon-button'
import { Slider } from '@/components/ui/slider'

import type { SoundTrackKind } from './store'
import { ZOOM_LEVELS } from './Timeline'

/** Every timeline operation as a named icon button, so the keyboard reaches what a drag does. */
export function TimelineToolbar({
  snapping,
  ripple,
  hasSelection,
  markerCount,
  zoom,
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
  onZoom,
  onFit,
}: {
  snapping: boolean
  ripple: boolean
  hasSelection: boolean
  markerCount: number
  zoom: number
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
  onZoom: (zoom: number) => void
  onFit: () => void
}) {
  const [label, setLabel] = useState('')
  const zoomIndex = Math.max(0, ZOOM_LEVELS.indexOf(zoom as (typeof ZOOM_LEVELS)[number]))
  const divider = <span aria-hidden="true" className="mx-1 h-5 w-px bg-border" />

  return (
    <div role="toolbar" aria-label="Timeline tools" className="flex shrink-0 flex-wrap items-center gap-0.5 border-b px-2 py-1">
      <IconButton label="Split" shortcut="S" icon={<Scissors strokeWidth={1.75} />} size="sm" disabled={!hasSelection} onClick={onSplit} />
      <IconButton label="Split away the left" icon={<ArrowLeftToLine strokeWidth={1.75} />} size="sm" disabled={!hasSelection} onClick={onSplitAwayLeft} />
      <IconButton label="Split away the right" icon={<ArrowRightToLine strokeWidth={1.75} />} size="sm" disabled={!hasSelection} onClick={onSplitAwayRight} />
      <IconButton label="Duplicate" icon={<Copy strokeWidth={1.75} />} size="sm" disabled={!hasSelection} onClick={onDuplicate} />
      <IconButton label="Delete" shortcut="Del" icon={<Trash2 strokeWidth={1.75} />} size="sm" disabled={!hasSelection} onClick={onDelete} />
      {divider}
      <IconButton label="Snap to edges" aria-pressed={snapping} icon={<Magnet strokeWidth={1.75} />} size="sm" onClick={() => onSnapping(!snapping)} />
      <IconButton label="Ripple edits" aria-pressed={ripple} icon={<WrapText strokeWidth={1.75} />} size="sm" onClick={() => onRipple(!ripple)} />
      {divider}
      <input
        type="text"
        aria-label="Marker label"
        value={label}
        onChange={(event) => setLabel(event.currentTarget.value)}
        placeholder="Marker"
        className="h-8 w-28 rounded-md border border-input bg-secondary px-2 text-caption"
      />
      <IconButton label="Add marker" shortcut="M" icon={<Flag strokeWidth={1.75} />} size="sm" onClick={() => { onAddMarker(label.trim() === '' ? 'Marker' : label); setLabel('') }} />
      <IconButton label="Previous marker" icon={<ChevronLeft strokeWidth={1.75} />} size="sm" disabled={markerCount === 0} onClick={onPreviousMarker} />
      <IconButton label="Next marker" icon={<ChevronRight strokeWidth={1.75} />} size="sm" disabled={markerCount === 0} onClick={onNextMarker} />
      {divider}
      <IconButton label="Add a music lane" icon={<Music strokeWidth={1.75} />} size="sm" onClick={() => onAddTrack('music')} />
      <IconButton label="Add an audio lane" icon={<AudioLines strokeWidth={1.75} />} size="sm" onClick={() => onAddTrack('extractedAudio')} />
      <div className="ml-auto flex items-center gap-1">
        <IconButton label="Zoom out" shortcut="−" icon={<Minus strokeWidth={1.75} />} size="sm" onClick={() => onZoom(ZOOM_LEVELS[Math.max(0, zoomIndex - 1)] ?? zoom)} />
        <Slider aria-label="Zoom" min={0} max={ZOOM_LEVELS.length - 1} step={1} value={zoomIndex} onChange={(event) => onZoom(ZOOM_LEVELS[Number(event.currentTarget.value)] ?? zoom)} className="w-24" />
        <IconButton label="Zoom in" shortcut="+" icon={<Plus strokeWidth={1.75} />} size="sm" onClick={() => onZoom(ZOOM_LEVELS[Math.min(ZOOM_LEVELS.length - 1, zoomIndex + 1)] ?? zoom)} />
        <IconButton label="Fit the clip" icon={<Maximize2 strokeWidth={1.75} />} size="sm" onClick={onFit} />
      </div>
    </div>
  )
}
```

Remove the zoom `−`/`+` buttons and the `onZoom` prop from `Timeline` (they moved to the toolbar); `EditorScreen` stops passing it. In `EditorScreen.tsx`, read `const storyboard = useStoryboard(edit.projectId, { enabled: true })` and `const waveform = useWaveform(edit.projectId, { enabled: true })`, pass `storyboard={storyboard.data ?? null}`, `peaks={waveform.peaks}`, `peaksPerSecond={waveform.peaksPerSecond}`, `sourceAssetId={composition.sourceAssetId}` to `Timeline`, and `zoom`, `onZoom`, `onFit={() => onZoom(fitZoom(laneElement.current?.clientWidth ?? 1200, composition.durationMs))}` to the toolbar (hold a ref on the lanes section).

- [ ] **Step 6: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/timeline.test.tsx tests/editor-advanced.test.tsx tests/editor-basic.test.tsx tests/editor-studio.test.tsx` → PASS.

---

### Task 6: Contextual inspector with timecodes

**Files:**
- Modify: `frontend/features/editor/Inspector.tsx` (rewrite), `frontend/features/editor/EditorScreen.tsx` (inspector target)
- Modify: `frontend/tests/editor-basic.test.tsx` (inspector trim tests), `frontend/e2e/editor-basic.spec.ts`
- Create: `frontend/tests/inspector.test.tsx`

**Interfaces:**
- Produces:
  - `type InspectorTarget = { kind: 'item'; id: string } | { kind: 'word'; id: string } | { kind: 'overlay'; id: string } | null`.
  - `Inspector` props: `{ composition: CompositionV1; target: InspectorTarget; playheadMs: number; onTrim(sourceInMs, sourceOutMs); onCrop(crop); onSplit(); onDelete(); onRetimeWord(wordId, startMs, endMs); onWordText(wordId, text); onMoveOverlay(overlayId, startMs, endMs) }`.
  - `EditorScreen` derives `target`: a selected timeline item wins; otherwise the word chosen in the captions panel (Task 7) or the overlay chosen on the timeline.

- [ ] **Step 1: Write the failing tests and update existing ones**

`frontend/tests/inspector.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { Inspector } from '@/features/editor/Inspector'

import { composition } from './support/fixtures'

function handlers() {
  return { onTrim: vi.fn(), onCrop: vi.fn(), onSplit: vi.fn(), onDelete: vi.fn(), onRetimeWord: vi.fn(), onWordText: vi.fn(), onMoveOverlay: vi.fn() }
}

describe('Inspector', () => {
  test('with nothing selected it describes the canvas and how to select', () => {
    render(<Inspector composition={composition()} target={null} playheadMs={0} {...handlers()} />)

    const inspector = screen.getByRole('region', { name: 'Inspector' })
    expect(inspector).toHaveTextContent('1080 × 1920')
    expect(inspector).toHaveTextContent('Select an item on the timeline, a caption word, or a text overlay.')
  })

  test('a selected item is trimmed with timecodes', async () => {
    const user = userEvent.setup()
    const actions = handlers()
    render(<Inspector composition={composition()} target={{ kind: 'item', id: 'scene-1' }} playheadMs={0} {...actions} />)

    expect(screen.getByRole('textbox', { name: 'Start' })).toHaveValue('0:01.00')
    const end = screen.getByRole('textbox', { name: 'End' })
    expect(end).toHaveValue('0:31.00')
    expect(screen.getByText('Duration 0:30.00')).toBeInTheDocument()
    await user.clear(end)
    await user.type(end, '0:21.00{Enter}')

    expect(actions.onTrim).toHaveBeenCalledWith(1_000, 21_000)
  })

  test('a selected caption word is retimed and retyped', async () => {
    const user = userEvent.setup()
    const actions = handlers()
    render(<Inspector composition={composition()} target={{ kind: 'word', id: 'w000002' }} playheadMs={0} {...actions} />)

    await user.clear(screen.getByRole('textbox', { name: 'Word' }))
    await user.type(screen.getByRole('textbox', { name: 'Word' }), 'karya{Enter}')
    await user.clear(screen.getByRole('textbox', { name: 'Start' }))
    await user.type(screen.getByRole('textbox', { name: 'Start' }), '0:01.20{Enter}')

    expect(actions.onWordText).toHaveBeenCalledWith('w000002', 'karya')
    expect(actions.onRetimeWord).toHaveBeenCalledWith('w000002', 1_200, 1_900)
  })
})
```

In `frontend/tests/editor-basic.test.tsx`:
- "trimming through the inspector saves one new revision": first click the `Select scene-1` timeline button, then `const end = within(screen.getByRole('region', { name: 'Inspector' })).getByRole('textbox', { name: 'End' })`, clear, type `0:21.00`, then click Save.
- "taking the newer version…": select `scene-1` and assert `toHaveValue('0:19.00')`.

In `frontend/e2e/editor-basic.spec.ts`: before trimming, click `page.getByRole('region', { name: /timeline/i }).getByRole('button', { name: 'Select scene-1' })`; replace `getByRole('spinbutton', { name: /clip ends at/i })` with `getByRole('textbox', { name: 'End' })`, `fill('0:20.00')`, and after reload select `scene-1` again and expect `toHaveValue('0:20.00')`.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/inspector.test.tsx`
Expected: FAIL — the inspector has no target prop and uses millisecond fields.

- [ ] **Step 3: Rewrite `Inspector.tsx`**

```tsx
'use client'

import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { TimecodeInput } from '@/components/ui/timecode-input'
import type { CompositionV1 } from '@/lib/api/generated/model'
import { formatTimecode } from '@/lib/time/timecode'

import { MIN_ITEM_MS, MIN_WORD_MS } from './store'

type TrackItem = CompositionV1['tracks'][number]['items'][number]

export type InspectorTarget =
  | { kind: 'item'; id: string }
  | { kind: 'word'; id: string }
  | { kind: 'overlay'; id: string }
  | null

/** The exact values behind whatever is selected, as fields a keyboard and a screen reader can use. */
export function Inspector({
  composition,
  target,
  playheadMs,
  onTrim,
  onCrop,
  onSplit,
  onDelete,
  onRetimeWord,
  onWordText,
  onMoveOverlay,
}: {
  composition: CompositionV1
  target: InspectorTarget
  playheadMs: number
  onTrim: (sourceInMs: number, sourceOutMs: number) => void
  onCrop: (crop: TrackItem['crop']) => void
  onSplit: () => void
  onDelete: () => void
  onRetimeWord: (wordId: string, startMs: number, endMs: number) => void
  onWordText: (wordId: string, text: string) => void
  onMoveOverlay: (overlayId: string, startMs: number, endMs: number) => void
}) {
  return (
    <section aria-label="Inspector" className="space-y-4">
      <h2 className="text-caption font-medium uppercase tracking-wide text-subtle-foreground">Inspector</h2>
      {target === null ? (
        <CanvasFacts composition={composition} />
      ) : target.kind === 'item' ? (
        <ItemFields composition={composition} itemId={target.id} playheadMs={playheadMs} onTrim={onTrim} onCrop={onCrop} onSplit={onSplit} onDelete={onDelete} />
      ) : target.kind === 'word' ? (
        <WordFields composition={composition} wordId={target.id} onRetimeWord={onRetimeWord} onWordText={onWordText} />
      ) : (
        <OverlayFields composition={composition} overlayId={target.id} onMoveOverlay={onMoveOverlay} />
      )}
    </section>
  )
}

function CanvasFacts({ composition }: { composition: CompositionV1 }) {
  return (
    <div className="space-y-2 text-small">
      <p className="font-mono text-foreground">
        {composition.canvas.width} × {composition.canvas.height}
      </p>
      <p className="text-muted-foreground">Length {formatTimecode(composition.durationMs)}</p>
      <p className="text-muted-foreground">Select an item on the timeline, a caption word, or a text overlay.</p>
    </div>
  )
}

function ItemFields({
  composition,
  itemId,
  playheadMs,
  onTrim,
  onCrop,
  onSplit,
  onDelete,
}: {
  composition: CompositionV1
  itemId: string
  playheadMs: number
  onTrim: (sourceInMs: number, sourceOutMs: number) => void
  onCrop: (crop: TrackItem['crop']) => void
  onSplit: () => void
  onDelete: () => void
}) {
  const item = composition.tracks.flatMap((track) => track.items).find((candidate) => candidate.id === itemId)
  if (item === undefined) return <CanvasFacts composition={composition} />
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <TimecodeInput label="Start" valueMs={item.sourceInMs} maxMs={item.sourceOutMs - MIN_ITEM_MS} onCommit={(ms) => onTrim(ms, item.sourceOutMs)} />
        <TimecodeInput label="End" valueMs={item.sourceOutMs} minMs={item.sourceInMs + MIN_ITEM_MS} onCommit={(ms) => onTrim(item.sourceInMs, ms)} />
      </div>
      <p className="font-mono text-caption text-muted-foreground">Duration {formatTimecode(item.sourceOutMs - item.sourceInMs)}</p>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" onClick={onSplit}>Split at playhead</Button>
        <Button variant="secondary" size="sm" onClick={onDelete}>Delete item</Button>
      </div>
      <p className="font-mono text-caption text-subtle-foreground">Playhead {formatTimecode(playheadMs)}</p>
      <div className="flex items-center justify-between gap-2">
        <span className="text-caption text-muted-foreground">
          {item.crop === null ? 'Full frame' : `Cropped to ${Math.round(item.crop.width * 100)}% × ${Math.round(item.crop.height * 100)}%`}
        </span>
        <Button variant="ghost" size="sm" disabled={item.crop === null} onClick={() => onCrop(null)}>Clear crop</Button>
      </div>
    </div>
  )
}

function WordFields({
  composition,
  wordId,
  onRetimeWord,
  onWordText,
}: {
  composition: CompositionV1
  wordId: string
  onRetimeWord: (wordId: string, startMs: number, endMs: number) => void
  onWordText: (wordId: string, text: string) => void
}) {
  const words = composition.captions.words
  const index = words.findIndex((word) => word.id === wordId)
  const word = words[index]
  if (word === undefined) return <CanvasFacts composition={composition} />
  const previousEnd = words[index - 1]?.endMs ?? 0
  const nextStart = words[index + 1]?.startMs ?? composition.durationMs
  return (
    <div className="space-y-3">
      <WordText key={word.id + word.text} text={word.text} onCommit={(text) => onWordText(word.id, text)} />
      <div className="grid grid-cols-2 gap-2">
        <TimecodeInput label="Start" valueMs={word.startMs} minMs={previousEnd} maxMs={word.endMs - MIN_WORD_MS} onCommit={(ms) => onRetimeWord(word.id, ms, word.endMs)} />
        <TimecodeInput label="End" valueMs={word.endMs} minMs={word.startMs + MIN_WORD_MS} maxMs={nextStart} onCommit={(ms) => onRetimeWord(word.id, word.startMs, ms)} />
      </div>
    </div>
  )
}

function WordText({ text, onCommit }: { text: string; onCommit: (text: string) => void }) {
  const [draft, setDraft] = useState(text)
  const commit = () => {
    if (draft.trim() !== '' && draft !== text) onCommit(draft.trim())
  }
  return (
    <label className="block space-y-1">
      <span className="text-caption text-muted-foreground">Word</span>
      <input
        type="text"
        value={draft}
        onChange={(event) => setDraft(event.currentTarget.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') commit()
        }}
        className="h-8 w-full rounded-md border border-input bg-secondary px-2 text-small"
      />
    </label>
  )
}

function OverlayFields({
  composition,
  overlayId,
  onMoveOverlay,
}: {
  composition: CompositionV1
  overlayId: string
  onMoveOverlay: (overlayId: string, startMs: number, endMs: number) => void
}) {
  const overlay = composition.overlays.find((entry) => entry.id === overlayId)
  if (overlay === undefined) return <CanvasFacts composition={composition} />
  return (
    <div className="space-y-3">
      <p className="text-small">{overlay.type === 'text' ? overlay.text : 'B-roll'}</p>
      <div className="grid grid-cols-2 gap-2">
        <TimecodeInput label="Start" valueMs={overlay.timelineStartMs} maxMs={overlay.timelineEndMs - 100} onCommit={(ms) => onMoveOverlay(overlay.id, ms, overlay.timelineEndMs)} />
        <TimecodeInput label="End" valueMs={overlay.timelineEndMs} minMs={overlay.timelineStartMs + 100} maxMs={composition.durationMs} onCommit={(ms) => onMoveOverlay(overlay.id, overlay.timelineStartMs, ms)} />
      </div>
    </div>
  )
}
```

Check the fixture for the word test: `w000002` starts at 1,000 and ends at 1,900; its neighbours end at 900 and start at 12,000; retiming its start to 1,200 keeps its end at 1,900.

- [ ] **Step 4: Wire the target in `EditorScreen.tsx`**

Add `const [focus, setFocus] = useState<InspectorTarget>(null)`. Derive:

```tsx
const target: InspectorTarget = state.selectedItemId !== null ? { kind: 'item', id: state.selectedItemId } : focus
```

Clear `focus` whenever a timeline item is selected (in the `onSelect` passed to `Timeline`), and pass `onWordText={(wordId, text) => dispatch({ type: 'captionText', wordId, text })}`, `onRetimeWord`, and `onMoveOverlay` dispatches. Remove the `onAspect` prop (the stage owns canvas shape since Task 4). The overlays lane's click (Task 5's restyled lane) calls `setFocus({ kind: 'overlay', id })` and seeks; to select an overlay while an item is selected, also dispatch `{ type: 'select', itemId: null }`.

- [ ] **Step 5: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/inspector.test.tsx tests/editor-basic.test.tsx` → PASS.

---

### Task 7: Transcript-style caption editor

**Files:**
- Modify: `frontend/features/editor/CaptionsPanel.tsx` (rewrite), `frontend/features/editor/KaraokePanel.tsx` (timecodes, `SegmentedControl` mode), `frontend/features/editor/EditorScreen.tsx`
- Modify: `frontend/tests/editor-basic.test.tsx`, `frontend/tests/editor-styling.test.tsx`
- Create: `frontend/tests/captions-panel.test.tsx`

**Interfaces:**
- Produces:
  - `CaptionsPanel({ captions; playheadMs: number; selectedWordId: string | null; onText: (wordId, text) => void; onSeek: (ms: number) => void; onSelectWord: (wordId: string) => void; timing: ReactNode })` — region `Captions`; a `SegmentedControl` "Captions view" (`Words` | `Timing`); `Timing` shows the `timing` node (the Karaoke panel).
  - Each word is a button named `Word at <m:ss.cc>`; editing it shows a textbox with the same name.

- [ ] **Step 1: Write the failing tests and update the existing ones**

`frontend/tests/captions-panel.test.tsx`:

```tsx
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { CaptionsPanel } from '@/features/editor/CaptionsPanel'

import { composition } from './support/fixtures'

function renderPanel(overrides: Partial<Parameters<typeof CaptionsPanel>[0]> = {}) {
  const actions = { onText: vi.fn(), onSeek: vi.fn(), onSelectWord: vi.fn() }
  render(
    <CaptionsPanel captions={composition().captions} playheadMs={1_200} selectedWordId={null} timing={<p>Timing body</p>} {...actions} {...overrides} />,
  )
  return actions
}

describe('CaptionsPanel', () => {
  test('reads as text, lights the word being said, and seeks when a word is clicked', async () => {
    const user = userEvent.setup()
    const actions = renderPanel()

    const panel = screen.getByRole('region', { name: 'Captions' })
    const words = within(panel).getAllByRole('button', { name: /^word at/i })
    expect(words.map((word) => word.textContent)).toEqual(['Ini', 'cara', 'kerja', 'editornya'])
    expect(within(panel).getByRole('button', { name: 'Word at 0:01.00' })).toHaveAttribute('aria-current', 'true')

    await user.click(within(panel).getByRole('button', { name: 'Word at 0:12.00' }))
    expect(actions.onSeek).toHaveBeenCalledWith(12_000)
    expect(actions.onSelectWord).toHaveBeenCalledWith('w000003')
  })

  test('double-click edits a word in place without moving its timing', async () => {
    const user = userEvent.setup()
    const actions = renderPanel()

    await user.dblClick(screen.getByRole('button', { name: 'Word at 0:01.00' }))
    const field = screen.getByRole('textbox', { name: 'Word at 0:01.00' })
    expect(field).toHaveFocus()
    await user.clear(field)
    await user.type(field, 'karya{Enter}')

    expect(actions.onText).toHaveBeenCalledWith('w000002', 'karya')
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  test('Escape abandons an edit, and an empty word is never committed', async () => {
    const user = userEvent.setup()
    const actions = renderPanel()

    await user.dblClick(screen.getByRole('button', { name: 'Word at 0:00.00' }))
    await user.clear(screen.getByRole('textbox', { name: 'Word at 0:00.00' }))
    await user.keyboard('{Enter}')
    await user.dblClick(screen.getByRole('button', { name: 'Word at 0:00.00' }))
    await user.type(screen.getByRole('textbox', { name: 'Word at 0:00.00' }), 'x{Escape}')

    expect(actions.onText).not.toHaveBeenCalled()
  })

  test('Timing view shows the karaoke controls', async () => {
    const user = userEvent.setup()
    renderPanel()

    await user.click(screen.getByRole('button', { name: 'Timing' }))

    expect(screen.getByText('Timing body')).toBeInTheDocument()
  })
})
```

Update `frontend/tests/editor-basic.test.tsx`:
- "the timeline shows every item and the captions panel every word": `within(captions).getAllByRole('button', { name: /word at/i })` has length 4.
- "editing a caption word and undoing it leaves the original text": double-click `Word at 0:01.00`, clear the textbox, type `karya{Enter}`, expect the button `Word at 0:01.00` to have text `karya`, click Undo, expect it to have text `cara`.
- "keyboard shortcuts drive the editor but leave text fields alone": replace the `getByDisplayValue('cara')` steps with a double-click on `Word at 0:01.00` and typing into its textbox; assert its value is `sesi`.

Update `frontend/tests/editor-styling.test.tsx` "the karaoke panel retimes one word…": first click the `Timing` button in the Captions region; type into `Start of w000002` as `0:01.50` instead of `1500`.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/captions-panel.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Rewrite `CaptionsPanel.tsx`**

```tsx
'use client'

import { useRef, useState, type ReactNode } from 'react'

import { SegmentedControl } from '@/components/ui/segmented-control'
import type { CompositionV1 } from '@/lib/api/generated/model'
import { formatTimecode } from '@/lib/time/timecode'
import { cn } from '@/lib/utils'

import { activeWordAt } from './store'

type CaptionWord = CompositionV1['captions']['words'][number]

/**
 * Captions as the words they are.
 *
 * The word under the playhead is lit, clicking a word jumps to it, and double-clicking (or
 * Enter) edits its text in place. Timing never changes here: transcription measured it, and
 * retiming is the Timing view's explicit job.
 */
export function CaptionsPanel({
  captions,
  playheadMs,
  selectedWordId,
  onText,
  onSeek,
  onSelectWord,
  timing,
}: {
  captions: CompositionV1['captions']
  playheadMs: number
  selectedWordId: string | null
  onText: (wordId: string, text: string) => void
  onSeek: (ms: number) => void
  onSelectWord: (wordId: string) => void
  timing: ReactNode
}) {
  const [view, setView] = useState<'words' | 'timing'>('words')
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  // Enter and Escape finish an edit themselves; the blur that can follow when the field
  // unmounts must not commit a second time or undo an Escape.
  const settled = useRef(false)
  const active = activeWordAt(captions.words, playheadMs)

  function start(word: CaptionWord): void {
    settled.current = false
    setEditing(word.id)
    setDraft(word.text)
  }

  function commit(word: CaptionWord): void {
    if (settled.current) return
    settled.current = true
    setEditing(null)
    const text = draft.trim()
    if (text !== '' && text !== word.text) onText(word.id, text)
  }

  function cancel(): void {
    settled.current = true
    setEditing(null)
  }

  return (
    <section aria-label="Captions" className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-title">Captions</h2>
        <SegmentedControl
          label="Captions view"
          size="sm"
          value={view}
          options={[
            { value: 'words', label: 'Words' },
            { value: 'timing', label: 'Timing' },
          ]}
          onChange={setView}
        />
      </div>
      {view === 'timing' ? (
        timing
      ) : (
        <p className="flex flex-wrap gap-x-1 gap-y-1.5 text-body leading-relaxed">
          {captions.words.map((word) => {
            const name = `Word at ${formatTimecode(word.startMs)}`
            if (editing === word.id) {
              return (
                <input
                  key={word.id}
                  type="text"
                  aria-label={name}
                  autoFocus
                  value={draft}
                  size={Math.max(2, draft.length)}
                  onChange={(event) => setDraft(event.currentTarget.value)}
                  onBlur={() => commit(word)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      commit(word)
                    } else if (event.key === 'Escape') {
                      event.preventDefault()
                      cancel()
                    }
                  }}
                  className="rounded-sm border border-primary bg-secondary px-1 text-body text-foreground"
                />
              )
            }
            return (
              <button
                key={word.id}
                type="button"
                aria-label={name}
                aria-current={active?.id === word.id ? 'true' : undefined}
                aria-pressed={selectedWordId === word.id}
                onClick={() => {
                  onSeek(word.startMs)
                  onSelectWord(word.id)
                }}
                onDoubleClick={() => start(word)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === 'F2') {
                    event.preventDefault()
                    start(word)
                  }
                }}
                className={cn(
                  'rounded-sm px-0.5 transition-colors duration-fast ease-signal',
                  active?.id === word.id ? 'bg-primary text-primary-foreground' : 'text-foreground hover:bg-secondary',
                  selectedWordId === word.id && active?.id !== word.id && 'outline outline-1 outline-primary',
                )}
              >
                {word.text}
              </button>
            )
          })}
        </p>
      )}
    </section>
  )
}
```

The lit word is a lime fill; it is the caption highlight, one of the four uses the spec allows lime for. Pressing Enter on a focused word button edits it — the Enter handler calls `preventDefault`, so the button's own click does not also fire. The test's `aria-current` expectation uses the fixture's playhead 1,200, inside `cara` (1,000–1,900).

- [ ] **Step 4: Move the style controls out and adapt `KaraokePanel`**

Delete the style controls and `FONTS`/`WEIGHTS` from the old panel (Task 8 rebuilds them in the Style panel). In `KaraokePanel.tsx`, replace the `Caption mode` select with a `SegmentedControl label="Caption mode"` (Off, Block, Karaoke), and replace each number field with `<TimecodeInput label="From" hideLabel accessibleName={`Start of ${word.id}`} … />` and `<TimecodeInput label="To" hideLabel accessibleName={`End of ${word.id}`} … />`, so the per-word names the tests use stay the same. Commit through `onRetime` exactly as the old `commit` did.

In `EditorScreen.tsx`'s captions `ToolPanel`, render:

```tsx
<CaptionsPanel
  captions={composition.captions}
  playheadMs={state.playheadMs}
  selectedWordId={focus?.kind === 'word' ? focus.id : null}
  onText={(wordId, text) => dispatch({ type: 'captionText', wordId, text })}
  onSeek={(ms) => dispatch({ type: 'seek', ms })}
  onSelectWord={(wordId) => {
    dispatch({ type: 'select', itemId: null })
    setFocus({ kind: 'word', id: wordId })
  }}
  timing={<KaraokePanel captions={composition.captions} playheadMs={state.playheadMs} onRetime={(wordId, startMs, endMs) => dispatch({ type: 'retimeWord', wordId, startMs, endMs })} onMode={(mode) => dispatch({ type: 'captionMode', mode })} />}
/>
```

- [ ] **Step 5: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/captions-panel.test.tsx tests/timecode.test.tsx tests/editor-basic.test.tsx tests/editor-styling.test.tsx` → PASS for everything except the style-control tests Task 8 moves.

---

### Task 8: Style panel

**Files:**
- Create: `frontend/features/editor/StylePanel.tsx`, `frontend/features/editor/FontPicker.tsx`, `frontend/features/editor/LookCard.tsx`, `frontend/features/editor/use-brand-colors.ts`
- Delete: `frontend/features/editor/TemplatesPanel.tsx` (its behaviour moves into `StylePanel`)
- Modify: `frontend/features/editor/EditorScreen.tsx` (Style tool contents; Layout tool no longer hosts motion and keyframes)
- Modify: `frontend/tests/editor-styling.test.tsx`
- Create: `frontend/tests/style-panel.test.tsx`

**Interfaces:**
- Consumes: `TEMPLATES`, `captionFontStack`, `CAPTION_FONT_FAMILIES`, `NumberScrub`, `SwatchPicker`, `SegmentedControl`, `Switch`, `readVersionApiV1BrandKitsBrandKitIdVersionsVersionGet`.
- Produces:
  - `useBrandColors(brandKit: CompositionV1['brandKit']): Array<{ name: string; hex: string }>`.
  - `FontPicker({ value: CaptionFontFamily; onChange: (family: CaptionFontFamily) => void })` — `radiogroup "Caption font"`.
  - `LookCard({ template: TemplateDefinition; applied: boolean; onApply: () => void })` — a button named by the template name, `aria-pressed`.
  - `StylePanel({ composition: CompositionV1; onStyle: (patch: Partial<CaptionStyle>) => void; onApplyTemplate: (template: TemplateDefinition) => void; motion: ReactNode })` — regions `Templates` and `Caption style`, followed by the `motion` node.

- [ ] **Step 1: Write the failing tests and update the styling suite**

`frontend/tests/style-panel.test.tsx`:

```tsx
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { StylePanel } from '@/features/editor/StylePanel'
import { TEMPLATES } from '@/features/editor/templates'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi } from './support/api'
import { composition, currentUser, workspace } from './support/fixtures'

function renderStyle(onStyle = vi.fn(), onApplyTemplate = vi.fn()) {
  stubApi({ 'GET /api/v1/me': { body: currentUser() }, 'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } } })
  renderWithApi(
    <WorkspaceProvider>
      <StylePanel composition={composition()} onStyle={onStyle} onApplyTemplate={onApplyTemplate} motion={<p>Motion body</p>} />
    </WorkspaceProvider>,
  )
  return { onStyle, onApplyTemplate }
}

describe('StylePanel', () => {
  test('templates are visual cards that draw their own look', async () => {
    const user = userEvent.setup()
    const { onApplyTemplate } = renderStyle()
    const template = TEMPLATES[0]!

    const templates = await screen.findByRole('region', { name: 'Templates' })
    const card = within(templates).getByRole('button', { name: new RegExp(template.name, 'i') })
    expect(within(card).getByTestId('look-sample')).toHaveStyle({ fontFamily: `var(--caption-font-${template.captionStyle.fontFamily.toLowerCase().replaceAll(' ', '-')}), sans-serif` })
    await user.click(card)

    expect(onApplyTemplate).toHaveBeenCalledWith(template)
  })

  test('fonts are named in their own faces', async () => {
    const user = userEvent.setup()
    const { onStyle } = renderStyle()

    const fonts = await screen.findByRole('radiogroup', { name: 'Caption font' })
    expect(within(fonts).getByRole('radio', { name: 'Montserrat' })).toHaveAttribute('aria-checked', 'true')
    expect(within(fonts).getByRole('radio', { name: 'Anton' })).toHaveStyle({ fontFamily: 'var(--caption-font-anton), sans-serif' })
    await user.click(within(fonts).getByRole('radio', { name: 'Anton' }))

    expect(onStyle).toHaveBeenCalledWith({ fontFamily: 'Anton' })
  })

  test('weight, alignment, and decoration are segmented choices', async () => {
    const user = userEvent.setup()
    const { onStyle } = renderStyle()
    const style = await screen.findByRole('region', { name: 'Caption style' })

    await user.click(within(within(style).getByRole('group', { name: 'Caption weight' })).getByRole('button', { name: '800' }))
    await user.click(within(within(style).getByRole('group', { name: 'Caption alignment' })).getByRole('button', { name: 'Left' }))
    await user.click(within(within(style).getByRole('group', { name: 'Caption decoration' })).getByRole('button', { name: 'Underline' }))

    expect(onStyle).toHaveBeenCalledWith({ weight: 800 })
    expect(onStyle).toHaveBeenCalledWith({ align: 'left' })
    expect(onStyle).toHaveBeenCalledWith({ decoration: 'underline' })
  })

  test('the motion tools sit at the end of the style panel', async () => {
    renderStyle()

    expect(await screen.findByText('Motion body')).toBeInTheDocument()
  })
})
```

Update `frontend/tests/editor-styling.test.tsx`:
- The two letter-spacing/line-height tests: query `screen.getByRole('region', { name: /caption style/i })` instead of `/captions/i`; the field names (`Caption letter spacing`, `Caption line height`) are unchanged; the typed values and `user.tab()` stay.
- "sets weight, italic, and the decoration": in the `caption style` region, click button `800` in group `Caption weight`, click the `Caption italic` switch via `getByRole('switch', { name: /caption italic/i })`, and click button `Underline` in group `Caption decoration`.
- "a template is applied by name": the region is still `Templates`.
- Keyframe and motion tests: unchanged queries; both regions are now inside the Style tool panel, still mounted.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/style-panel.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement `use-brand-colors.ts`, `FontPicker.tsx`, `LookCard.tsx`**

`use-brand-colors.ts`:

```ts
'use client'

import { useQuery } from '@tanstack/react-query'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { readVersionApiV1BrandKitsBrandKitIdVersionsVersionGet } from '@/lib/api/generated/brand-kits/brand-kits'
import type { BrandKitVersionResponse, CompositionV1 } from '@/lib/api/generated/model'

/** The colours of the brand kit version this clip was built with, or none. */
export function useBrandColors(brandKit: CompositionV1['brandKit']): Array<{ name: string; hex: string }> {
  const { active } = useWorkspaceScope()
  const version = useQuery<BrandKitVersionResponse, ApiError>({
    queryKey: ['/api/v1/brand-kits/version', active.id, brandKit?.id, brandKit?.version],
    queryFn: ({ signal }) =>
      readVersionApiV1BrandKitsBrandKitIdVersionsVersionGet(brandKit!.id, brandKit!.version, { workspace_id: active.id }, { signal }),
    enabled: brandKit !== null,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  })
  return version.data?.definition.colors ?? []
}
```

`FontPicker.tsx`:

```tsx
'use client'

import { cn } from '@/lib/utils'

import { CAPTION_FONT_FAMILIES, captionFontStack, type CaptionFontFamily } from './caption-fonts'

/** Every caption font, each name drawn in the face it names. */
export function FontPicker({ value, onChange }: { value: CaptionFontFamily; onChange: (family: CaptionFontFamily) => void }) {
  return (
    <div role="radiogroup" aria-label="Caption font" className="grid grid-cols-2 gap-1.5">
      {CAPTION_FONT_FAMILIES.map((family) => (
        <button
          key={family}
          type="button"
          role="radio"
          aria-checked={family === value}
          onClick={() => onChange(family)}
          style={{ fontFamily: captionFontStack(family) }}
          className={cn(
            'h-9 truncate rounded-md border px-2 text-left text-body transition-colors duration-fast ease-signal',
            family === value ? 'border-primary text-primary' : 'border-border text-foreground hover:border-line-strong',
          )}
        >
          {family}
        </button>
      ))}
    </div>
  )
}
```

Give the group roving arrow-key navigation: `onKeyDown` on the group moves focus to the previous/next radio on `ArrowLeft`/`ArrowUp`/`ArrowRight`/`ArrowDown` and chooses it.

`LookCard.tsx`:

```tsx
import { cn } from '@/lib/utils'

import { captionFontStack } from './caption-fonts'
import type { TemplateDefinition } from './templates'

/** One template drawn as the caption it produces, on a small 9:16 frame. */
export function LookCard({ template, applied, onApply }: { template: TemplateDefinition; applied: boolean; onApply: () => void }) {
  const style = template.captionStyle
  return (
    <button
      type="button"
      aria-pressed={applied}
      onClick={onApply}
      className={cn('group flex flex-col gap-1.5 rounded-md border p-1.5 text-left transition-colors duration-fast ease-signal', applied ? 'border-primary' : 'border-border hover:border-line-strong')}
    >
      <span className="relative flex aspect-[9/16] items-end justify-center overflow-hidden rounded-sm bg-stage p-2">
        <span
          data-testid="look-sample"
          style={{
            fontFamily: captionFontStack(style.fontFamily),
            fontWeight: style.weight,
            fontStyle: style.italic ? 'italic' : 'normal',
            color: style.color,
            textAlign: style.align,
            letterSpacing: `${style.letterSpacing / 4}px`,
            backgroundColor: style.backgroundEnabled ? style.backgroundColor : undefined,
          }}
          className="px-1 text-[15px] leading-tight"
        >
          Say it <span style={{ color: style.highlightColor }}>loud</span>
        </span>
      </span>
      <span className="truncate text-caption font-semibold text-foreground">{template.name}</span>
      <span className="line-clamp-2 text-[11px] text-muted-foreground">{template.description}</span>
    </button>
  )
}
```

- [ ] **Step 4: Implement `StylePanel.tsx`**

```tsx
'use client'

import type { ReactNode } from 'react'

import { NumberScrub } from '@/components/ui/number-scrub'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Switch } from '@/components/ui/switch'
import { SwatchPicker } from '@/components/ui/swatch-picker'
import type { CompositionV1 } from '@/lib/api/generated/model'

import { FontPicker } from './FontPicker'
import { LookCard } from './LookCard'
import { TEMPLATES, type TemplateDefinition } from './templates'
import { useBrandColors } from './use-brand-colors'

type CaptionStyle = CompositionV1['captions']['style']
const WEIGHTS = [300, 400, 500, 600, 700, 800, 900] as const

/** The look of the captions: templates first, then every style field, then motion. */
export function StylePanel({
  composition,
  onStyle,
  onApplyTemplate,
  motion,
}: {
  composition: CompositionV1
  onStyle: (patch: Partial<CaptionStyle>) => void
  onApplyTemplate: (template: TemplateDefinition) => void
  motion: ReactNode
}) {
  const style = composition.captions.style
  const brandColors = useBrandColors(composition.brandKit)
  const applied = composition.template

  return (
    <div className="space-y-6">
      <section aria-label="Templates" className="space-y-3">
        <h2 className="text-title">Templates</h2>
        <div className="grid grid-cols-3 gap-2">
          {TEMPLATES.map((template) => (
            <LookCard
              key={`${template.id}-${template.version}`}
              template={template}
              applied={applied !== null && applied.id === template.id && applied.version === template.version}
              onApply={() => onApplyTemplate(template)}
            />
          ))}
        </div>
      </section>

      <section aria-label="Caption style" className="space-y-4">
        <h2 className="text-title">Caption style</h2>
        <FontPicker value={style.fontFamily} onChange={(fontFamily) => onStyle({ fontFamily })} />
        <NumberScrub label="Size" accessibleName="Caption size" value={style.fontSize} min={12} max={200} step={1} unit="px" onCommit={(fontSize) => onStyle({ fontSize })} />
        <SegmentedControl label="Caption weight" size="sm" value={String(style.weight)} options={WEIGHTS.map((weight) => ({ value: String(weight), label: String(weight) }))} onChange={(weight) => onStyle({ weight: Number(weight) })} className="flex-wrap" />
        <div className="flex items-center justify-between">
          <span className="text-caption text-muted-foreground">Italic</span>
          <Switch aria-label="Caption italic" checked={style.italic} onCheckedChange={(italic) => onStyle({ italic })} />
        </div>
        <SegmentedControl label="Caption alignment" size="sm" value={style.align} options={[{ value: 'left', label: 'Left' }, { value: 'center', label: 'Centre' }, { value: 'right', label: 'Right' }]} onChange={(align) => onStyle({ align })} />
        <SegmentedControl label="Caption decoration" size="sm" value={style.decoration} options={[{ value: 'none', label: 'None' }, { value: 'underline', label: 'Underline' }, { value: 'strikethrough', label: 'Strikethrough' }]} onChange={(decoration) => onStyle({ decoration })} />
        <SwatchPicker label="Colour" accessibleName="Caption colour" value={style.color} brandColors={brandColors} onChange={(color) => onStyle({ color })} />
        <SwatchPicker label="Highlight" accessibleName="Caption highlight colour" value={style.highlightColor} brandColors={brandColors} onChange={(highlightColor) => onStyle({ highlightColor })} />
        <div className="flex items-center justify-between">
          <span className="text-caption text-muted-foreground">Background</span>
          <Switch aria-label="Caption background" checked={style.backgroundEnabled} onCheckedChange={(backgroundEnabled) => onStyle({ backgroundEnabled })} />
        </div>
        {style.backgroundEnabled ? (
          <SwatchPicker label="Background colour" accessibleName="Caption background colour" value={style.backgroundColor} brandColors={brandColors} onChange={(backgroundColor) => onStyle({ backgroundColor })} />
        ) : null}
        <NumberScrub label="Letter spacing" accessibleName="Caption letter spacing" value={style.letterSpacing} min={-10} max={40} step={0.5} precision={1} unit="px" onCommit={(letterSpacing) => onStyle({ letterSpacing })} />
        <NumberScrub label="Line height" accessibleName="Caption line height" value={style.lineHeight} min={0.5} max={3} step={0.1} precision={1} onCommit={(lineHeight) => onStyle({ lineHeight })} />
      </section>

      {motion}
    </div>
  )
}
```

If the generated type makes `align` or `decoration` a string union narrower than `SegmentedControl`'s generic inference, pass the generic explicitly (`SegmentedControl<CaptionStyle['align']>`).

In `EditorScreen.tsx`'s Style tool panel render:

```tsx
<StylePanel
  composition={composition}
  onStyle={(patch) => dispatch({ type: 'captionStyle', patch })}
  onApplyTemplate={(template) => dispatch({ type: 'applyTemplate', template })}
  motion={<>{/* existing KeyframeEditor and MotionPanel elements, moved here from the Layout panel */}</>}
/>
```

- [ ] **Step 5: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/style-panel.test.tsx tests/editor-styling.test.tsx` → PASS.

---

### Task 9: Crop on the stage

**Files:**
- Create: `frontend/features/editor/CropOverlay.tsx`, `frontend/features/editor/LayoutPanel.tsx`
- Modify: `frontend/features/editor/Player.tsx` (`showFullFrame` prop and an overlay slot), `frontend/features/editor/EditorScreen.tsx`
- Create: `frontend/tests/crop-overlay.test.tsx`

**Interfaces:**
- Produces:
  - `CropOverlay({ crop: Crop; onCommit: (crop: Crop) => void })` — a `group "Crop"` with a focusable `button "Move crop"` and `button "Resize crop"`; resizing keeps the crop's own ratio, which Fill already set to the canvas shape.
  - `LayoutPanel({ item: TrackItem | null; sourceAspect: number; canvasAspect: number; onCrop: (crop: Crop | null) => void })` — region `Layout` with `SegmentedControl "Framing"` (`Fill`, `Fit`).
  - `Player` props gain `showFullFrame?: boolean` and `overlay?: ReactNode`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/crop-overlay.test.tsx`:

```tsx
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { CropOverlay } from '@/features/editor/CropOverlay'
import { LayoutPanel } from '@/features/editor/LayoutPanel'

describe('CropOverlay', () => {
  test('arrow keys move the crop inside the frame', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<CropOverlay crop={{ x: 0.34, y: 0, width: 0.32, height: 1 }} onCommit={onCommit} />)

    screen.getByRole('button', { name: 'Move crop' }).focus()
    await user.keyboard('{ArrowRight}')
    expect(onCommit).toHaveBeenLastCalledWith({ x: 0.35, y: 0, width: 0.32, height: 1 })

    await user.keyboard('{Shift>}{ArrowRight}{/Shift}')
    expect(onCommit).toHaveBeenLastCalledWith({ x: 0.44, y: 0, width: 0.32, height: 1 })
  })

  test('the crop never leaves the frame', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<CropOverlay crop={{ x: 0.68, y: 0, width: 0.32, height: 1 }} onCommit={onCommit} />)

    screen.getByRole('button', { name: 'Move crop' }).focus()
    await user.keyboard('{ArrowRight}')

    expect(onCommit).not.toHaveBeenCalled()
  })

  test('resizing keeps the canvas shape and stays centred on the crop', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<CropOverlay crop={{ x: 0.25, y: 0.25, width: 0.5, height: 0.5 }} onCommit={onCommit} />)

    screen.getByRole('button', { name: 'Resize crop' }).focus()
    await user.keyboard('{ArrowDown}')

    const next = onCommit.mock.calls.at(-1)![0]
    expect(next.width).toBeCloseTo(0.48)
    expect(next.height).toBeCloseTo(0.48)
    expect(next.x).toBeCloseTo(0.26)
    expect(next.y).toBeCloseTo(0.26)
  })
})

describe('LayoutPanel', () => {
  test('Fill crops to the canvas shape and Fit shows the whole frame', () => {
    const onCrop = vi.fn()
    render(
      <LayoutPanel
        item={{ id: 'scene-1', crop: null } as never}
        sourceAspect={16 / 9}
        canvasAspect={9 / 16}
        onCrop={onCrop}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Fill' }))
    expect(onCrop).toHaveBeenLastCalledWith(expect.objectContaining({ y: 0, height: 1 }))
    fireEvent.click(screen.getByRole('button', { name: 'Fit' }))
    expect(onCrop).toHaveBeenLastCalledWith(null)
  })
})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/crop-overlay.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement `CropOverlay.tsx`**

```tsx
'use client'

import { Move, Scaling } from 'lucide-react'
import type { KeyboardEvent, PointerEvent } from 'react'

import type { CompositionV1 } from '@/lib/api/generated/model'

type Crop = NonNullable<CompositionV1['tracks'][number]['items'][number]['crop']>

const STEP = 0.01
const BIG_STEP = 0.1
const MIN_SIZE = 0.1

/**
 * The crop drawn over the whole source frame, moved and resized in place.
 *
 * Resizing keeps the canvas shape and the crop's centre, so the export never stretches.
 * Every change is clamped inside the frame, and both handles work from the keyboard.
 */
export function CropOverlay({ crop, onCommit }: { crop: Crop; onCommit: (crop: Crop) => void }) {
  function commit(next: Crop): void {
    if (next.x < -1e-9 || next.y < -1e-9 || next.x + next.width > 1 + 1e-9 || next.y + next.height > 1 + 1e-9) return
    onCommit(round(next))
  }

  function move(dx: number, dy: number): void {
    commit({ ...crop, x: crop.x + dx, y: crop.y + dy })
  }

  function resize(delta: number): void {
    // The crop keeps its own width-to-height ratio, which is the canvas shape in source units.
    const ratio = crop.height / crop.width
    const width = Math.min(1, Math.max(MIN_SIZE, crop.width + delta))
    const height = width * ratio
    if (height > 1 || height < MIN_SIZE) return
    const centreX = crop.x + crop.width / 2
    const centreY = crop.y + crop.height / 2
    commit({ x: centreX - width / 2, y: centreY - height / 2, width, height })
  }

  function onMoveKey(event: KeyboardEvent<HTMLButtonElement>): void {
    const step = event.shiftKey ? BIG_STEP : STEP
    const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }
    const delta = moves[event.key]
    if (delta === undefined) return
    event.preventDefault()
    move(delta[0], delta[1])
  }

  function onResizeKey(event: KeyboardEvent<HTMLButtonElement>): void {
    const step = (event.shiftKey ? BIG_STEP : STEP) * 2
    if (event.key === 'ArrowUp' || event.key === 'ArrowRight') {
      event.preventDefault()
      resize(step)
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowLeft') {
      event.preventDefault()
      resize(-step)
    }
  }

  function drag(kind: 'move' | 'resize') {
    return (event: PointerEvent<HTMLButtonElement>) => {
      const frame = event.currentTarget.closest('[data-crop-frame]')?.getBoundingClientRect()
      if (frame === undefined || frame.width === 0) return
      const startX = event.clientX
      const startY = event.clientY
      const origin = crop
      const onMovePointer = (moveEvent: globalThis.PointerEvent) => {
        const dx = (moveEvent.clientX - startX) / frame.width
        const dy = (moveEvent.clientY - startY) / frame.height
        if (kind === 'move') {
          const x = Math.min(1 - origin.width, Math.max(0, origin.x + dx))
          const y = Math.min(1 - origin.height, Math.max(0, origin.y + dy))
          onCommit(round({ ...origin, x, y }))
        }
      }
      const onUp = (upEvent: globalThis.PointerEvent) => {
        window.removeEventListener('pointermove', onMovePointer)
        window.removeEventListener('pointerup', onUp)
        if (kind === 'resize') {
          const dx = (upEvent.clientX - startX) / frame.width
          resize(dx * 2)
        }
      }
      window.addEventListener('pointermove', onMovePointer)
      window.addEventListener('pointerup', onUp)
    }
  }

  return (
    <div data-crop-frame className="absolute inset-0">
      <div role="group" aria-label="Crop" className="absolute border-2 border-primary shadow-[0_0_0_9999px_rgb(10_10_11/0.65)]" style={{ left: `${crop.x * 100}%`, top: `${crop.y * 100}%`, width: `${crop.width * 100}%`, height: `${crop.height * 100}%` }}>
        <button type="button" aria-label="Move crop" onKeyDown={onMoveKey} onPointerDown={drag('move')} className="absolute inset-0 flex cursor-move items-center justify-center text-primary opacity-0 focus-visible:opacity-100 hover:opacity-100">
          <Move aria-hidden="true" strokeWidth={1.75} className="size-6" />
        </button>
        <button type="button" aria-label="Resize crop" onKeyDown={onResizeKey} onPointerDown={drag('resize')} className="absolute -bottom-1.5 -right-1.5 flex size-5 cursor-nwse-resize items-center justify-center rounded-sm bg-primary text-primary-foreground">
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
```

Check the resize test: width 0.5 − 0.02 = 0.48, ratio 1 → height 0.48, centre 0.5 → x = y = 0.26.

- [ ] **Step 4: Implement `LayoutPanel.tsx` and wire the stage**

```tsx
'use client'

import { SegmentedControl } from '@/components/ui/segmented-control'
import type { CompositionV1 } from '@/lib/api/generated/model'

import { centreCrop } from './store'

type TrackItem = CompositionV1['tracks'][number]['items'][number]

/** How the source fills the canvas; the crop itself is adjusted on the stage. */
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
        <p className="text-small text-muted-foreground">Select a video item on the timeline to frame it. The crop appears on the stage.</p>
      ) : (
        <>
          <SegmentedControl
            label="Framing"
            value={item.crop === null ? 'fit' : 'fill'}
            options={[
              { value: 'fill', label: 'Fill' },
              { value: 'fit', label: 'Fit' },
            ]}
            onChange={(choice) => onCrop(choice === 'fit' ? null : centreCrop(sourceAspect, canvasAspect))}
          />
          <p className="text-small text-muted-foreground">Drag the crop on the stage, or focus it and use the arrow keys. Shift moves further.</p>
        </>
      )}
    </section>
  )
}
```

In `Player.tsx`, when `showFullFrame` is true skip `cropStyle`, set the canvas `aspectRatio` to the source's (`source.width / source.height` when known), and render `overlay` inside the canvas box. In `EditorScreen.tsx`, when `tool === 'layout'` and the selected item (or the first video item) is a video item with a crop, pass `showFullFrame` and `overlay={<CropOverlay crop={item.crop} onCommit={(crop) => dispatch({ type: 'crop', itemId: item.id, crop })} />}`; render `LayoutPanel` in the Layout tool panel.

A drag commits on every pointer move, which is many history entries. Batch it: in `EditorScreen`, keep the dragged crop in local state while the pointer is down and dispatch once on release — give `CropOverlay` an `onPreview` prop used during `move` pointer moves and call `onCommit` from `onUp` with the last previewed crop.

- [ ] **Step 5: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/crop-overlay.test.tsx tests/editor-basic.test.tsx` → PASS.

---

### Task 10: Remaining panels, overlay timing, and handover

**Files:**
- Modify: `frontend/features/editor/{TextPanel,AudioPanel,AssetsPanel,SourceMonitor,SceneList,MotionPanel,KeyframeEditor,AccessibilityPanel,ExportDialog}.tsx`, `frontend/features/broll/{BrollPanel,BrollSuggestionCard,CoverageControl,BrollProvenanceList}.tsx`, `frontend/features/reviews/ReviewPanel.tsx`
- Modify: `frontend/tests/editor-styling.test.tsx` (overlay timing), `frontend/e2e/editor-advanced.spec.ts`
- Modify: `PROGRESS.md`

- [ ] **Step 1: Update the overlay-timing test**

In `frontend/tests/editor-styling.test.tsx` "the motion panel refuses a movement the element is too short to show": replace the two `fireEvent.change` calls with

```tsx
    const end = within(text).getByRole('textbox', { name: /end of text-1/i })
    fireEvent.change(end, { target: { value: '0:00.70' } })
    fireEvent.blur(end)
    const start = within(text).getByRole('textbox', { name: /start of text-1/i })
    fireEvent.change(start, { target: { value: '0:00.00' } })
    fireEvent.blur(start)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pnpm --dir frontend exec vitest run tests/editor-styling.test.tsx -t "too short"`
Expected: FAIL — the overlay fields are still number inputs.

- [ ] **Step 3: Restyle the panels**

For each panel file, apply the same rules and nothing else:

- The outer `section` keeps its `aria-label`; its class becomes `space-y-3` (no `surface` box — the tool panel is already the surface) and its `h2` becomes `text-title`.
- Buttons become `Button` (`secondary` for actions, `ghost` for tertiary, `size="sm"`); icon-only controls become `IconButton` with the existing accessible name.
- Every millisecond field becomes `TimecodeInput` with `accessibleName` equal to the existing `aria-label` (`TextPanel` start/end, `SourceMonitor` marks if shown as numbers). `TextPanel`'s size stays a number via `NumberScrub accessibleName={`Size of ${overlay.id}`}`; placement becomes a `Select` already (Plan 1) with `controlSize="sm"`.
- Lists become `divide-y divide-border` rows, not bordered cards.
- `SceneList` rows show `formatTimecode` start times in mono; `AssetsPanel` rows show kind, content type, and duration (`formatClock`) in mono; `BrollSuggestionCard` shows its picture in a `relative aspect-video overflow-hidden rounded-sm bg-stage` box.

- [ ] **Step 4: Update the browser scenario**

In `frontend/e2e/editor-advanced.spec.ts`, the split scenario keeps `getByRole('slider', { name: /scrub the clip/i })` and `getByRole('button', { name: /^Split$/ })`. The text scenario keeps its queries. Run the editor scenarios and fix any selector broken by the layout (tool tabs are unchanged in name).

- [ ] **Step 5: Run every gate**

From the repository root: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build` → PASS.
From `backend/` (disposable database): `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest -q --cov=clipah --cov-fail-under=90` → PASS.

- [ ] **Step 6: Browser suite, screenshots, and a real render check**

Rebuild the `frontend` and media services. From `frontend/`:

```bash
CLIPAH_E2E_BASE_URL=http://localhost:3000 pnpm exec playwright test e2e/editor-basic.spec.ts e2e/editor-advanced.spec.ts --project=chromium --project=webkit
CLIPAH_CAPTURE_SCREENS=editor CLIPAH_E2E_BASE_URL=http://localhost:3000 pnpm exec playwright test e2e/design-screens.spec.ts --project=chromium
```

Then, on a Project with real media (the owner's, with permission), apply the Bold karaoke template (Anton), export one clip, and compare a frame of the export with the preview at the same playhead: the caption glyphs must be Anton in both. Record the outcome; if the export shows a fallback face, stop and report — the font task is not done.

- [ ] **Step 7: Record progress**

Append "Signal Studio redesign — Plan 5, editor" to `PROGRESS.md`: layout, transport, timeline, inspector, caption editor, style panel, crop, font parity (with the render comparison result), the deliberate refinements (no caption position control; `NumberScrub` for spacing and height; 1/30 s frame step), which tests changed shape and why, gate output, and the owner commit message `feat: rebuild the editor as a studio`. Do not run `git commit`.
