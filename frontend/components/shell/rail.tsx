'use client'

import { Library, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import Link from 'next/link'
import { useEffect, useId, useState, type ReactNode } from 'react'

import { IconButton } from '@/components/ui/icon-button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

import {
  LIBRARY_NAVIGATION,
  PRIMARY_NAVIGATION,
  SETTINGS_ENTRY,
  isCurrent,
  readRailPinned,
  writeRailPinned,
  type NavigationEntry,
} from './navigation'
import { Wordmark } from './wordmark'

/**
 * The Workspace navigation: a 64 px icon rail, or 220 px with labels once pinned.
 *
 * On phones the same element opens as an overlay from the tab bar's Navigation control,
 * always at full width, so there is one navigation landmark rather than two.
 */
export function Rail({
  pathname,
  newProject,
  open,
  onClose,
}: {
  pathname: string | null
  newProject?: ReactNode
  /** Whether the phone overlay is showing. */
  open: boolean
  onClose: () => void
}) {
  const [pinned, setPinned] = useState(false)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const libraryId = useId()
  const expanded = pinned || open

  useEffect(() => {
    setPinned(readRailPinned())
  }, [])

  function togglePinned(): void {
    const next = !pinned
    setPinned(next)
    writeRailPinned(next)
  }

  return (
    <TooltipProvider delayDuration={200}>
      <nav
        id="workspace-navigation"
        aria-label="Workspace"
        className={cn(
          'fixed inset-y-0 left-0 z-40 shrink-0 flex-col border-r bg-card py-3 transition-[width] duration-panel ease-signal md:sticky md:top-0 md:flex md:h-screen',
          expanded ? 'w-[220px] px-3' : 'w-16 items-center px-2',
          open ? 'flex shadow-2xl' : 'hidden',
        )}
      >
        <div className={cn('flex h-10 items-center pb-3', expanded ? 'justify-between px-1' : 'justify-center')}>
          <Link href="/dashboard" aria-label="Clipah home" onClick={onClose}>
            <Wordmark compact={!expanded} />
          </Link>
        </div>
        {newProject === undefined ? null : <div className={cn('pb-3', expanded ? '' : 'flex justify-center')}>{newProject}</div>}
        <ul className="flex w-full flex-col gap-0.5">
          {PRIMARY_NAVIGATION.map((entry) => (
            <RailLink key={entry.href} entry={entry} pathname={pathname} expanded={expanded} onNavigate={onClose} />
          ))}
          <li>
            <button
              type="button"
              aria-expanded={libraryOpen}
              aria-controls={libraryId}
              onClick={() => setLibraryOpen((current) => !current)}
              className={railItemClass(false, expanded)}
            >
              <Library aria-hidden="true" strokeWidth={1.75} className="size-5 shrink-0" />
              <span className={expanded ? '' : 'sr-only'}>Library</span>
            </button>
            <ul id={libraryId} hidden={!libraryOpen} className={cn('mt-0.5 flex flex-col gap-0.5', expanded ? 'pl-3' : '')}>
              {LIBRARY_NAVIGATION.map((entry) => (
                <RailLink key={entry.href} entry={entry} pathname={pathname} expanded={expanded} onNavigate={onClose} />
              ))}
            </ul>
          </li>
        </ul>
        <ul className="mt-auto flex w-full flex-col gap-0.5 pt-3">
          <RailLink entry={SETTINGS_ENTRY} pathname={pathname} expanded={expanded} onNavigate={onClose} />
          <li className={cn('hidden md:flex', expanded ? 'justify-end' : 'justify-center')}>
            <IconButton
              label={pinned ? 'Collapse navigation' : 'Pin navigation'}
              aria-pressed={pinned}
              icon={pinned ? <PanelLeftClose strokeWidth={1.75} /> : <PanelLeftOpen strokeWidth={1.75} />}
              onClick={togglePinned}
              tooltipSide="right"
            />
          </li>
        </ul>
      </nav>
    </TooltipProvider>
  )
}

function RailLink({
  entry,
  pathname,
  expanded,
  onNavigate,
}: {
  entry: NavigationEntry
  pathname: string | null
  expanded: boolean
  onNavigate: () => void
}) {
  const current = isCurrent(pathname, entry.href)
  const Icon = entry.icon
  const link = (
    <Link
      href={entry.href}
      aria-current={current ? 'page' : undefined}
      onClick={onNavigate}
      className={railItemClass(current, expanded)}
    >
      <Icon aria-hidden="true" strokeWidth={1.75} className="size-5 shrink-0" />
      <span className={expanded ? '' : 'sr-only'}>{entry.label}</span>
    </Link>
  )
  return (
    <li>
      {expanded ? (
        link
      ) : (
        <Tooltip>
          <TooltipTrigger asChild>{link}</TooltipTrigger>
          <TooltipContent side="right">{entry.label}</TooltipContent>
        </Tooltip>
      )}
    </li>
  )
}

function railItemClass(current: boolean, expanded: boolean): string {
  return cn(
    'relative flex h-10 items-center gap-3 rounded-md text-small font-medium transition-colors duration-fast ease-signal',
    expanded ? 'w-full px-3' : 'w-10 justify-center',
    current
      ? 'bg-secondary text-foreground before:absolute before:left-0 before:top-2 before:h-6 before:w-0.5 before:bg-primary'
      : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
  )
}
