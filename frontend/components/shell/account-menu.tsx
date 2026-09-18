'use client'

import Link from 'next/link'
import { useEffect, useId, useRef, useState } from 'react'

import type { ShellUser } from '@/components/dashboard-shell'

/** Who is signed in, and the way out. */
export function AccountMenu({ user, onSignOut }: { user: ShellUser; onSignOut?: () => void }) {
  const [open, setOpen] = useState(false)
  const container = useOutsideClose(open, () => setOpen(false))
  const panelId = useId()
  const name = user.displayName ?? user.email ?? ''
  const initial = name.trim().charAt(0).toUpperCase() || '?'

  return (
    <div ref={container} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((current) => !current)}
        className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-secondary"
      >
        <span
          aria-hidden="true"
          className="flex size-7 items-center justify-center rounded-full bg-secondary text-caption font-semibold text-foreground"
        >
          {initial}
        </span>
        <span className="hidden max-w-32 truncate text-small font-medium lg:inline">{name}</span>
        <span className="sr-only lg:hidden">Account menu for {name}</span>
      </button>
      <div
        id={panelId}
        hidden={!open}
        className="absolute right-0 top-11 z-30 w-56 rounded-lg border border-line-strong bg-popover p-1 shadow-xl"
      >
        {user.email === undefined ? null : (
          <p className="truncate px-3 py-2 text-caption text-muted-foreground">{user.email}</p>
        )}
        <Link
          href="/dashboard/settings"
          onClick={() => setOpen(false)}
          className="block w-full rounded-md px-3 py-2 text-left text-small hover:bg-secondary"
        >
          Settings
        </Link>
        {onSignOut === undefined ? null : (
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              onSignOut()
            }}
            className="block w-full rounded-md px-3 py-2 text-left text-small hover:bg-secondary"
          >
            Sign out
          </button>
        )}
      </div>
    </div>
  )
}

/** Close a popover when the member clicks elsewhere or presses Escape. */
export function useOutsideClose(open: boolean, close: () => void) {
  const container = useRef<HTMLDivElement>(null)
  const latest = useRef(close)
  latest.current = close

  useEffect(() => {
    if (!open) {
      return
    }
    function onPointer(event: MouseEvent) {
      if (container.current !== null && !container.current.contains(event.target as Node)) {
        latest.current()
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        latest.current()
      }
    }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return container
}
