import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  /** Needed when `label` is an icon. */
  accessibleName?: string
}

/**
 * A small set of mutually exclusive choices, shown all at once.
 *
 * Each choice is a button with `aria-pressed`, so every option is one Tab away and a test
 * or a screen reader finds the chosen one by its pressed state. The chosen option is
 * marked with lime text on the raised surface, never a second lime fill.
 */
export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
  size = 'md',
  className,
}: {
  label: string
  value: T | null
  options: ReadonlyArray<SegmentedOption<T>>
  onChange: (value: T) => void
  size?: 'sm' | 'md'
  className?: string
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn('inline-flex items-center gap-0.5 rounded-md border border-border bg-background p-0.5', className)}
    >
      {options.map((option) => {
        const pressed = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={pressed}
            aria-label={option.accessibleName}
            onClick={() => onChange(option.value)}
            className={cn(
              'inline-flex items-center justify-center gap-1.5 rounded-[3px] px-2.5 font-medium transition-colors duration-fast ease-signal [&_svg]:size-4',
              size === 'sm' ? 'h-7 text-caption' : 'h-8 text-small',
              pressed ? 'bg-secondary text-primary' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
