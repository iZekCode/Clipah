'use client'

import { useEffect, useState, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** A tab choice that lives in `?tab=`, so a link can open a section and a refresh keeps it. */
export function useUrlTab<T extends string>(
  ids: readonly T[],
  fallback: T,
): [T, (next: T) => void] {
  const [tab, setTab] = useState<T>(fallback)

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get('tab')
    const known = ids.find((id) => id === requested)
    if (known !== undefined) setTab(known)
    // The address is read once, when the page opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function choose(next: T): void {
    setTab(next)
    const url = new URL(window.location.href)
    url.searchParams.set('tab', next)
    window.history.replaceState(window.history.state, '', url.toString())
  }

  return [tab, choose]
}

export function TabList<T extends string>({
  label,
  tabs,
  active,
  onChoose,
  idPrefix,
}: {
  label: string
  tabs: ReadonlyArray<{ id: T; label: string }>
  active: T
  onChoose: (id: T) => void
  idPrefix: string
}) {
  return (
    // Tabs wrap instead of scrolling: a scroll box clips the underline that overlaps the
    // border by a pixel, and that overflow alone would make the row scroll.
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-1 border-b">
      {tabs.map((entry) => (
        <button
          key={entry.id}
          type="button"
          role="tab"
          id={`${idPrefix}-tab-${entry.id}`}
          aria-selected={active === entry.id}
          aria-controls={`${idPrefix}-panel-${entry.id}`}
          onClick={() => onChoose(entry.id)}
          className={cn(
            '-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-small font-semibold transition-colors duration-fast ease-signal',
            active === entry.id
              ? 'border-primary text-foreground'
              : 'border-transparent text-muted-foreground hover:text-foreground',
          )}
        >
          {entry.label}
        </button>
      ))}
    </div>
  )
}

/** Only the chosen panel is mounted, so a tab never reads data nobody is looking at. */
export function TabPanel({
  idPrefix,
  id,
  active,
  children,
}: {
  idPrefix: string
  id: string
  active: string
  children: ReactNode
}) {
  if (id !== active) return null
  return (
    <div
      role="tabpanel"
      id={`${idPrefix}-panel-${id}`}
      aria-labelledby={`${idPrefix}-tab-${id}`}
      className="pt-5"
    >
      {children}
    </div>
  )
}
