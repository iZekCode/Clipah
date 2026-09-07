'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useState, type ReactNode } from 'react'

/** The signed-in User, as much of it as the shell needs to show. */
export interface ShellUser {
  displayName: string | null
  email?: string
}

const NAVIGATION = [
  { href: '/dashboard', label: 'Overview' },
  { href: '/dashboard/projects', label: 'Projects' },
  { href: '/dashboard/search', label: 'Search' },
  { href: '/dashboard/clips', label: 'Clips' },
  { href: '/dashboard/assets', label: 'Assets' },
  { href: '/dashboard/templates', label: 'Templates' },
  { href: '/dashboard/brand-kits', label: 'Brand kits' },
  { href: '/dashboard/team', label: 'Team' },
  { href: '/dashboard/publishing', label: 'Publishing' },
  { href: '/dashboard/settings', label: 'Settings' },
] as const

/**
 * The frame every authenticated page renders inside: the Workspace it is scoped to, the
 * navigation between its areas, the running work, and the signed-in User. Names come from
 * the API, so they are rendered as text and never as markup.
 */
export function DashboardShell({
  user,
  workspaceSwitcher,
  jobCenter,
  children,
}: {
  user: ShellUser
  workspaceSwitcher: ReactNode
  jobCenter: ReactNode
  children: ReactNode
}) {
  const pathname = usePathname()
  const [navigationOpen, setNavigationOpen] = useState(false)

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between gap-4 border-b px-6 py-4">
        <div className="flex items-center gap-4">
          <button
            type="button"
            aria-expanded={navigationOpen}
            aria-controls="workspace-navigation"
            onClick={() => setNavigationOpen((open) => !open)}
            className="rounded-md border px-2 py-1 text-sm md:hidden"
          >
            Navigation
          </button>
          {workspaceSwitcher}
        </div>
        <p className="text-sm text-muted-foreground">{user.displayName ?? user.email ?? ''}</p>
      </header>
      <div className="flex flex-1 flex-col md:flex-row">
        <nav
          id="workspace-navigation"
          aria-label="Workspace"
          className={`${navigationOpen ? 'block' : 'hidden'} w-full shrink-0 border-b px-4 py-6 md:block md:w-56 md:border-b-0 md:border-r`}
        >
          <ul className="space-y-1">
            {NAVIGATION.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={isCurrent(pathname, item.href) ? 'page' : undefined}
                  className="block rounded-md px-3 py-2 text-sm hover:bg-accent"
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
          <div className="mt-6">{jobCenter}</div>
        </nav>
        <main className="flex-1 px-6 py-8">{children}</main>
      </div>
    </div>
  )
}

/** The entry for the area being viewed, not every entry the route happens to start with. */
function isCurrent(pathname: string | null, href: string): boolean {
  if (pathname === null) {
    return false
  }
  if (href === '/dashboard') {
    return pathname === '/dashboard'
  }
  return pathname === href || pathname.startsWith(`${href}/`)
}
