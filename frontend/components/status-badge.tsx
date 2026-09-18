import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** What a status means for the creator, which decides how loudly it is shown. */
export type StatusTone = 'neutral' | 'progress' | 'success' | 'attention' | 'danger' | 'accent'

const DOTS: Record<StatusTone, string> = {
  neutral: 'bg-subtle-foreground',
  progress: 'bg-muted-foreground motion-safe:animate-pulse',
  success: 'bg-primary',
  attention: 'bg-warning',
  danger: 'bg-destructive',
  accent: 'bg-primary',
}

/** The decorative dot that helps a creator scan a grid; the words beside it carry the meaning. */
export function StatusDot({ tone, className }: { tone: StatusTone; className?: string }) {
  return <span aria-hidden="true" className={cn('size-1.5 shrink-0 rounded-full', DOTS[tone], className)} />
}

/**
 * A short, readable state label with its dot.
 *
 * `overlay` sets the label on a solid graphite plate so it stays legible over a picture.
 */
export function StatusBadge({
  tone = 'neutral',
  appearance = 'plain',
  children,
  className,
}: {
  tone?: StatusTone
  appearance?: 'plain' | 'overlay'
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 text-caption font-medium',
        tone === 'danger' ? 'text-destructive' : tone === 'attention' ? 'text-warning' : 'text-muted-foreground',
        appearance === 'overlay' && 'rounded-sm bg-background/85 px-1.5 py-0.5 text-foreground',
        className,
      )}
    >
      <StatusDot tone={tone} />
      {children}
    </span>
  )
}
