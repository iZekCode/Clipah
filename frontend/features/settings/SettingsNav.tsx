'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import type { ReactNode } from 'react'

import { PageHeader } from '@/components/page-header'
import { cn } from '@/lib/utils'

const SECTIONS = [
  { href: '/dashboard/settings', label: 'General' },
  { href: '/dashboard/team', label: 'Members' },
  { href: '/dashboard/settings/connections', label: 'Connections' },
  { href: '/dashboard/settings#sessions', label: 'Sessions' },
  { href: '/dashboard/settings#usage', label: 'Usage' },
] as const

/**
 * Every Settings page: one title, a side list of sections, and the page's own content.
 *
 * Members and Connections keep the URLs they have always had; they are grouped here so a
 * creator finds every Workspace control in one place. Sessions and Usage are sections of
 * the General page, so they are never marked as the current page.
 */
export function SettingsLayout({ description, children }: { description: string; children: ReactNode }) {
  const pathname = usePathname()
  return (
    <div className="space-y-2">
      <PageHeader title="Settings" description={description} />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-8 md:grid-cols-[200px_minmax(0,1fr)]">
        <nav aria-label="Settings sections" className="min-w-0">
          <ul className="flex gap-1 overflow-x-auto md:flex-col">
            {SECTIONS.map((section) => {
              const current = !section.href.includes('#') && pathname === section.href
              return (
                <li key={section.href}>
                  <Link
                    href={section.href}
                    aria-current={current ? 'page' : undefined}
                    className={cn(
                      'block whitespace-nowrap rounded-md px-3 py-2 text-small font-medium transition-colors duration-fast ease-signal',
                      current
                        ? 'bg-secondary text-foreground'
                        : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
                    )}
                  >
                    {section.label}
                  </Link>
                </li>
              )
            })}
          </ul>
        </nav>
        <div className="min-w-0 space-y-10">{children}</div>
      </div>
    </div>
  )
}
