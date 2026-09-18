'use client'

import { Activity } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

import { useOutsideClose } from './account-menu'

/**
 * Running work, one click away from every page, docked at the bottom right.
 *
 * The panel is hidden rather than unmounted when closed, so the job center inside it keeps
 * its one live connection and its history while the member moves around.
 */
export function RenderQueue({ count, children }: { count: number; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const container = useOutsideClose(open, () => setOpen(false))
  const panelId = useId()

  return (
    <div ref={container}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={count === 0 ? 'Activity' : `Activity, ${count} running`}
        onClick={() => setOpen((current) => !current)}
        className={cn(
          'flex h-9 items-center gap-2 rounded-md border px-2.5 text-small font-medium transition-colors duration-fast ease-signal',
          count === 0 ? 'border-border text-muted-foreground hover:text-foreground' : 'border-line-strong text-foreground',
        )}
      >
        <Activity aria-hidden="true" strokeWidth={1.75} className={cn('size-4', count > 0 && 'text-primary motion-safe:animate-pulse')} />
        {count === 0 ? null : <span className="font-mono text-caption tabular">{count} running</span>}
      </button>
      <div
        id={panelId}
        hidden={!open}
        className="fixed bottom-16 right-4 z-40 max-h-[70vh] w-[min(360px,calc(100vw-2rem))] overflow-y-auto rounded-lg border border-line-strong bg-popover p-4 shadow-2xl md:bottom-4"
      >
        {children}
      </div>
    </div>
  )
}
