'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

import { PageHeader } from '@/components/page-header'

const SECTIONS = [
  { href: '/dashboard/settings', label: 'General' },
  { href: '/dashboard/team', label: 'Members' },
  { href: '/dashboard/settings/connections', label: 'Connections' },
] as const

/**
 * The top of every Settings page: one title, and the sections Settings is made of.
 *
 * Members and Connections keep the URLs they have always had; they are grouped here so a
 * creator finds every Workspace control in one place.
 */
export function SettingsHeader({ description }: { description: string }) {
  const pathname = usePathname()
  return (
    <div className="space-y-2">
      <PageHeader title="Settings" description={description} />
      <nav aria-label="Settings sections" className="-mt-2 border-b">
        <ul className="flex gap-1 overflow-x-auto">
          {SECTIONS.map((section) => {
            const current = pathname === section.href
            return (
              <li key={section.href}>
                <Link
                  href={section.href}
                  aria-current={current ? 'page' : undefined}
                  className={`-mb-px inline-block whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium ${
                    current
                      ? 'border-primary text-foreground'
                      : 'border-transparent text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {section.label}
                </Link>
              </li>
            )
          })}
        </ul>
      </nav>
    </div>
  )
}
