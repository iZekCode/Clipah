'use client'

import { Menu } from 'lucide-react'
import Link from 'next/link'

import { cn } from '@/lib/utils'

import { PRIMARY_NAVIGATION, isCurrent } from './navigation'

/** Phone navigation: the four journey destinations, and the full rail behind one control. */
export function PhoneTabs({
  pathname,
  navigationOpen,
  onToggleNavigation,
}: {
  pathname: string | null
  navigationOpen: boolean
  onToggleNavigation: () => void
}) {
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-30 flex h-14 items-stretch border-t bg-card pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      {PRIMARY_NAVIGATION.map((entry) => {
        const Icon = entry.icon
        const current = isCurrent(pathname, entry.href)
        return (
          <Link
            key={entry.href}
            href={entry.href}
            aria-current={current ? 'page' : undefined}
            className={cn(
              'flex flex-1 flex-col items-center justify-center gap-0.5 text-caption font-medium',
              current ? 'text-primary' : 'text-muted-foreground',
            )}
          >
            <Icon aria-hidden="true" strokeWidth={1.75} className="size-5" />
            {entry.label}
          </Link>
        )
      })}
      <button
        type="button"
        aria-label="Navigation"
        aria-expanded={navigationOpen}
        aria-controls="workspace-navigation"
        onClick={onToggleNavigation}
        className="flex flex-1 flex-col items-center justify-center gap-0.5 text-caption font-medium text-muted-foreground"
      >
        <Menu aria-hidden="true" strokeWidth={1.75} className="size-5" />
        More
      </button>
    </nav>
  )
}
