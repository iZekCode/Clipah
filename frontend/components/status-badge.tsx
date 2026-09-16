import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** What a status means for the creator, which decides how loudly it is shown. */
export type StatusTone = 'neutral' | 'progress' | 'success' | 'attention' | 'danger' | 'accent'

const TONES: Record<StatusTone, string> = {
  neutral: 'bg-secondary text-secondary-foreground',
  progress: 'bg-info-soft text-info',
  success: 'bg-success-soft text-success',
  attention: 'bg-warning-soft text-warning',
  danger: 'bg-destructive/10 text-destructive',
  accent: 'bg-accent text-accent-foreground',
}

const DOTS: Record<StatusTone, string> = {
  neutral: 'bg-muted-foreground/60',
  progress: 'bg-info motion-safe:animate-pulse',
  success: 'bg-success',
  attention: 'bg-warning',
  danger: 'bg-destructive',
  accent: 'bg-primary',
}

/**
 * A short, readable state label.
 *
 * Colour is never the only signal: the words carry the meaning, and the dot only helps a
 * creator scan a grid for the one card that needs them.
 */
export function StatusBadge({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: StatusTone
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium',
        TONES[tone],
        className,
      )}
    >
      <span aria-hidden="true" className={cn('size-1.5 rounded-full', DOTS[tone])} />
      {children}
    </span>
  )
}
