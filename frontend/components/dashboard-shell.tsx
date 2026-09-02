import Link from 'next/link'
import type { ReactNode } from 'react'

/** The signed-in User, as much of it as the shell needs to show. */
export interface ShellUser {
  displayName: string
}

const NAVIGATION = [
  { href: '/dashboard', label: 'Overview' },
  { href: '/dashboard/projects', label: 'Projects' },
  { href: '/dashboard/clips', label: 'Clips' },
  { href: '/dashboard/settings', label: 'Settings' },
] as const

/**
 * The frame every authenticated page renders inside: the Workspace it is scoped to, the
 * navigation between its areas, and the signed-in User. Names come from the API, so they
 * are rendered as text and never as markup.
 */
export function DashboardShell({
  user,
  workspaceName,
  children,
}: {
  user: ShellUser
  workspaceName: string
  children: ReactNode
}) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">Workspace</p>
          <p className="text-sm font-medium">{workspaceName}</p>
        </div>
        <p className="text-sm text-muted-foreground">{user.displayName}</p>
      </header>
      <div className="flex flex-1">
        <nav aria-label="Workspace" className="w-56 shrink-0 border-r px-4 py-6">
          <ul className="space-y-1">
            {NAVIGATION.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className="block rounded-md px-3 py-2 text-sm hover:bg-accent"
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <main className="flex-1 px-6 py-8">{children}</main>
      </div>
    </div>
  )
}
