'use client'

import { useRef, type KeyboardEvent } from 'react'

import { cn } from '@/lib/utils'

import { CAPTION_FONT_FAMILIES, captionFontStack, type CaptionFontFamily } from './caption-fonts'

const PREVIOUS = new Set(['ArrowLeft', 'ArrowUp'])
const NEXT = new Set(['ArrowRight', 'ArrowDown'])

/** Every caption font, each name drawn in the face it names. */
export function FontPicker({
  value,
  onChange,
}: {
  value: CaptionFontFamily
  onChange: (family: CaptionFontFamily) => void
}) {
  const group = useRef<HTMLDivElement>(null)

  /** Arrow keys move through the fonts and choose, as a radio group does. */
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    const direction = PREVIOUS.has(event.key) ? -1 : NEXT.has(event.key) ? 1 : 0
    if (direction === 0) return
    event.preventDefault()
    const count = CAPTION_FONT_FAMILIES.length
    const index = CAPTION_FONT_FAMILIES.indexOf(value)
    const next = CAPTION_FONT_FAMILIES[(index + direction + count) % count]!
    onChange(next)
    group.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]')[
      CAPTION_FONT_FAMILIES.indexOf(next)
    ]?.focus()
  }

  return (
    <div
      ref={group}
      role="radiogroup"
      aria-label="Caption font"
      onKeyDown={onKeyDown}
      className="grid grid-cols-2 gap-1.5"
    >
      {CAPTION_FONT_FAMILIES.map((family) => (
        <button
          key={family}
          type="button"
          role="radio"
          aria-checked={family === value}
          tabIndex={family === value ? 0 : -1}
          onClick={() => onChange(family)}
          style={{ fontFamily: captionFontStack(family) }}
          className={cn(
            'h-9 truncate rounded-md border px-2 text-left text-body transition-colors duration-fast ease-signal',
            family === value
              ? 'border-primary text-primary'
              : 'border-border text-foreground hover:border-line-strong',
          )}
        >
          {family}
        </button>
      ))}
    </div>
  )
}
