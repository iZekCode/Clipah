'use client'

import { MoreHorizontal } from 'lucide-react'
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** One action an item menu offers. */
export interface ItemAction {
  label: string
  onSelect: () => void
  destructive?: boolean
  disabled?: boolean
}

/**
 * The contextual actions of one item — rename, delete, and the like — kept behind one
 * labelled button so they never compete with the item's primary action.
 *
 * It is a plain disclosure rather than a roving-focus menu: every action is an ordinary
 * button, reachable with Tab, and Escape closes it and returns focus to the trigger.
 */
export function ItemMenu({
  label,
  actions,
  icon,
}: {
  label: string
  actions: ItemAction[]
  icon?: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  const container = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) {
      return
    }
    function onPointer(event: MouseEvent) {
      if (container.current !== null && !container.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false)
        trigger.current?.focus()
      }
    }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={container} className="relative">
      <button
        ref={trigger}
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((current) => !current)}
        className="flex size-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground"
      >
        {icon ?? <MoreHorizontal aria-hidden="true" className="size-4" />}
      </button>
      <div
        id={panelId}
        hidden={!open}
        className="absolute right-0 top-9 z-30 min-w-40 rounded-lg border bg-popover p-1 shadow-lg"
      >
        {actions.map((action) => (
          <button
            key={action.label}
            type="button"
            disabled={action.disabled}
            onClick={() => {
              setOpen(false)
              action.onSelect()
            }}
            className={cn(
              'block w-full rounded-md px-3 py-2 text-left text-sm hover:bg-secondary disabled:opacity-50',
              action.destructive === true ? 'text-destructive' : '',
            )}
          >
            {action.label}
          </button>
        ))}
      </div>
    </div>
  )
}
