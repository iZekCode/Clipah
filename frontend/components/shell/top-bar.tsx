'use client'

import { Search } from 'lucide-react'
import { useId, type ReactNode } from 'react'

/** What every page needs from anywhere: search or jump, the Workspace, work, and the member. */
export function TopBar({
  workspaceSwitcher,
  onOpenCommandPalette,
  activity,
  account,
}: {
  workspaceSwitcher: ReactNode
  onOpenCommandPalette?: () => void
  activity: ReactNode
  account: ReactNode
}) {
  return (
    <header className="sticky top-0 z-20 flex h-[52px] items-center gap-2 border-b bg-background/95 px-4 sm:gap-3 sm:px-6">
      <div className="min-w-0">{workspaceSwitcher}</div>
      {onOpenCommandPalette === undefined ? (
        <SearchForm />
      ) : (
        <button
          type="button"
          onClick={onOpenCommandPalette}
          className="hidden h-9 max-w-md flex-1 items-center gap-2 rounded-md border border-border bg-card px-3 text-small text-subtle-foreground transition-colors duration-fast ease-signal hover:border-line-strong hover:text-muted-foreground sm:flex"
        >
          <Search aria-hidden="true" strokeWidth={1.75} className="size-4" />
          <span className="flex-1 text-left">Search or jump to…</span>
          <kbd className="font-mono text-caption">⌘K</kbd>
        </button>
      )}
      <div className="ml-auto flex items-center gap-2">
        {activity}
        {account}
      </div>
    </header>
  )
}

/** The plain GET search form, for shells rendered without a command palette. */
function SearchForm() {
  const fieldId = useId()
  return (
    <form role="search" action="/dashboard/search" method="get" className="hidden max-w-md flex-1 sm:block">
      <label htmlFor={fieldId} className="sr-only">
        Search this workspace
      </label>
      <input
        id={fieldId}
        type="search"
        name="q"
        placeholder="Search projects, transcripts, clips…"
        className="h-9 w-full rounded-md border border-input bg-secondary px-3 text-small placeholder:text-subtle-foreground"
      />
    </form>
  )
}
