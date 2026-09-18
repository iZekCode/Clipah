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

  const current = value.toUpperCase()
  const brandHexes = new Set(brandColors.map((color) => color.hex.toUpperCase()))
  const swatches = [
    ...brandColors.map((color) => ({ name: color.name, hex: color.hex.toUpperCase() })),
    ...recent.filter((hex) => !brandHexes.has(hex)).map((hex) => ({ name: 'Recent', hex })),
  ]
  const draftMatch = HEX.exec(draft.trim())

  return (
    <div role="group" aria-label={accessibleName} className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-caption text-muted-foreground">{label}</span>
        <span className="font-mono text-caption text-foreground">{current}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {swatches.map((swatch) => (
          <button
            key={`${swatch.name}-${swatch.hex}`}
            type="button"
            aria-label={`${swatch.name} ${swatch.hex}`}
            aria-pressed={swatch.hex === current}
            onClick={() => choose(swatch.hex)}
            className={cn(
              'size-6 rounded-sm border border-input transition-shadow duration-fast ease-signal',
              swatch.hex === current && 'ring-2 ring-primary ring-offset-2 ring-offset-card',
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
          <PopoverContent className="w-56 space-y-2">
            <input
              type="color"
              aria-label="Pick a colour"
              value={draftMatch === null ? value : `#${draftMatch[1]}`}
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
    return Array.isArray(stored)
      ? stored.filter((entry): entry is string => typeof entry === 'string' && HEX.test(entry))
      : []
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
