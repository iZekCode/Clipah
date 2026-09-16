'use client'

import {
  Activity,
  Clapperboard,
  FolderOpen,
  Home,
  Image as ImageIcon,
  LayoutTemplate,
  Menu,
  Palette,
  Search,
  Send,
  Settings,
  X,
} from 'lucide-react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** The signed-in User, as much of it as the shell needs to show. */
export interface ShellUser {
  displayName: string | null
  email?: string
}

/** One destination in the Workspace navigation. */
interface NavigationEntry {
  href: string
  label: string
  icon: typeof Home
}

const PRIMARY_NAVIGATION: NavigationEntry[] = [
  { href: '/dashboard', label: 'Home', icon: Home },
  { href: '/dashboard/projects', label: 'Projects', icon: FolderOpen },
  { href: '/dashboard/clips', label: 'Clips', icon: Clapperboard },
  { href: '/dashboard/publishing', label: 'Publishing', icon: Send },
]

const LIBRARY_NAVIGATION: NavigationEntry[] = [
  { href: '/dashboard/assets', label: 'Assets', icon: ImageIcon },
  { href: '/dashboard/templates', label: 'Templates', icon: LayoutTemplate },
  { href: '/dashboard/brand-kits', label: 'Brand kits', icon: Palette },
]

const SETTINGS_ENTRY: NavigationEntry = {
  href: '/dashboard/settings',
  label: 'Settings',
  icon: Settings,
}

// Team and Connections live inside Settings, but their established URLs stay reachable.
const SETTINGS_ROUTES = ['/dashboard/settings', '/dashboard/team']

/**
 * The frame every authenticated page renders inside.
 *
 * The navigation names the creator's journey — Home, Projects, Clips, Publishing — with
 * the reusable library grouped beneath it and Settings last. The top bar holds what is
 * needed from anywhere: the Workspace, search, running work, the member, and the one
 * action that starts everything, a new Project. Names come from the API, so they are
 * rendered as text and never as markup.
 */
export function DashboardShell({
  user,
  workspaceSwitcher,
  jobCenter,
  newProject,
  activeJobCount = 0,
  onSignOut,
  children,
}: {
  user: ShellUser
  workspaceSwitcher: ReactNode
  jobCenter: ReactNode
  newProject?: ReactNode
  activeJobCount?: number
  onSignOut?: () => void
  children: ReactNode
}) {
  const pathname = usePathname()
  const [navigationOpen, setNavigationOpen] = useState(false)

  useEffect(() => {
    setNavigationOpen(false)
  }, [pathname])

  return (
    <div className="flex min-h-screen bg-background">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-card focus:px-3 focus:py-2 focus:shadow"
      >
        Skip to content
      </a>
      <nav
        id="workspace-navigation"
        aria-label="Workspace"
        className={cn(
          'fixed inset-y-0 left-0 z-40 w-64 shrink-0 flex-col border-r bg-sidebar px-3 py-4 md:sticky md:top-0 md:flex md:h-screen',
          navigationOpen ? 'flex shadow-xl' : 'hidden',
        )}
      >
        <div className="flex items-center justify-between px-2 pb-4">
          <Link href="/dashboard" className="flex items-center gap-2 rounded-lg">
            <span
              aria-hidden="true"
              className="flex size-8 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground"
            >
              C
            </span>
            <span className="text-base font-semibold tracking-tight">Clipah</span>
          </Link>
          <button
            type="button"
            aria-label="Close menu"
            onClick={() => setNavigationOpen(false)}
            className="flex size-8 items-center justify-center rounded-lg hover:bg-secondary md:hidden"
          >
            <X aria-hidden="true" className="size-4" />
          </button>
        </div>
        {newProject === undefined ? null : <div className="px-1 pb-4">{newProject}</div>}
        <ul className="space-y-0.5">
          {PRIMARY_NAVIGATION.map((entry) => (
            <NavigationLink key={entry.href} entry={entry} pathname={pathname} />
          ))}
        </ul>
        <p className="px-3 pb-1 pt-6 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Library
        </p>
        <ul className="space-y-0.5">
          {LIBRARY_NAVIGATION.map((entry) => (
            <NavigationLink key={entry.href} entry={entry} pathname={pathname} />
          ))}
        </ul>
        <ul className="mt-auto space-y-0.5 pt-6">
          <NavigationLink entry={SETTINGS_ENTRY} pathname={pathname} />
        </ul>
      </nav>
      {navigationOpen ? (
        <div
          aria-hidden="true"
          className="fixed inset-0 z-30 bg-foreground/20 md:hidden"
          onClick={() => setNavigationOpen(false)}
        />
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex items-center gap-2 border-b bg-background/90 px-4 py-2.5 backdrop-blur sm:gap-3 sm:px-6">
          <button
            type="button"
            aria-expanded={navigationOpen}
            aria-controls="workspace-navigation"
            aria-label="Navigation"
            onClick={() => setNavigationOpen((open) => !open)}
            className="flex size-9 items-center justify-center rounded-lg border bg-card md:hidden"
          >
            <Menu aria-hidden="true" className="size-4" />
          </button>
          <div className="min-w-0">{workspaceSwitcher}</div>
          <GlobalSearchField />
          <div className="ml-auto flex items-center gap-2">
            <ActivityIndicator count={activeJobCount}>{jobCenter}</ActivityIndicator>
            <UserMenu user={user} onSignOut={onSignOut} />
          </div>
        </header>
        <main id="main-content" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  )
}

function NavigationLink({ entry, pathname }: { entry: NavigationEntry; pathname: string | null }) {
  const current = isCurrent(pathname, entry.href)
  const Icon = entry.icon
  return (
    <li>
      <Link
        href={entry.href}
        aria-current={current ? 'page' : undefined}
        className={cn(
          'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
          current
            ? 'bg-sidebar-accent text-sidebar-accent-foreground'
            : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
        )}
      >
        <Icon aria-hidden="true" className="size-4" />
        {entry.label}
      </Link>
    </li>
  )
}

/**
 * Send a question to the library search from anywhere in the Workspace.
 *
 * It is an ordinary GET form, so the question lands in the search page's URL and a
 * search can be bookmarked, shared, or reopened from history.
 */
function GlobalSearchField() {
  const fieldId = useId()

  return (
    <form
      role="search"
      action="/dashboard/search"
      method="get"
      className="hidden max-w-sm flex-1 sm:block"
    >
      <label htmlFor={fieldId} className="sr-only">
        Search this workspace
      </label>
      <div className="relative">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <input
          id={fieldId}
          type="search"
          name="q"
          placeholder="Search projects, transcripts, clips…"
          className="h-9 w-full rounded-lg border bg-card pl-9 pr-3 text-sm placeholder:text-muted-foreground"
        />
      </div>
    </form>
  )
}

/**
 * Running work, one click away from every page.
 *
 * The panel is hidden rather than unmounted when closed, so the job center behind it keeps
 * its one live connection and its history while the member moves around.
 */
function ActivityIndicator({ count, children }: { count: number; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const container = useOutsideClose(open, () => setOpen(false))
  const panelId = useId()

  return (
    <div ref={container} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={count === 0 ? 'Activity' : `Activity, ${count} running`}
        onClick={() => setOpen((current) => !current)}
        className="relative flex size-9 items-center justify-center rounded-lg border bg-card hover:bg-secondary"
      >
        <Activity aria-hidden="true" className="size-4" />
        {count === 0 ? null : (
          <span
            aria-hidden="true"
            className="absolute -right-1 -top-1 flex min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold text-primary-foreground"
          >
            {count}
          </span>
        )}
      </button>
      <div
        id={panelId}
        hidden={!open}
        className="absolute right-0 top-11 z-30 max-h-[70vh] w-80 overflow-y-auto rounded-xl border bg-popover p-4 shadow-lg"
      >
        {children}
      </div>
    </div>
  )
}

/** Who is signed in, and the way out. */
function UserMenu({ user, onSignOut }: { user: ShellUser; onSignOut?: () => void }) {
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
        className="flex items-center gap-2 rounded-lg px-1.5 py-1 hover:bg-secondary"
      >
        <span
          aria-hidden="true"
          className="flex size-7 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground"
        >
          {initial}
        </span>
        <span className="hidden max-w-32 truncate text-sm font-medium lg:inline">{name}</span>
        <span className="sr-only lg:hidden">Account menu for {name}</span>
      </button>
      <div
        id={panelId}
        hidden={!open}
        className="absolute right-0 top-11 z-30 w-56 rounded-xl border bg-popover p-1 shadow-lg"
      >
        {user.email === undefined ? null : (
          <p className="truncate px-3 py-2 text-xs text-muted-foreground">{user.email}</p>
        )}
        <Link
          href="/dashboard/settings"
          onClick={() => setOpen(false)}
          className="block rounded-md px-3 py-2 text-sm hover:bg-secondary"
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
            className="block w-full rounded-md px-3 py-2 text-left text-sm hover:bg-secondary"
          >
            Sign out
          </button>
        )}
      </div>
    </div>
  )
}

/** Close a popover when the member clicks elsewhere or presses Escape. */
function useOutsideClose(open: boolean, close: () => void) {
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

/** The entry for the area being viewed, not every entry the route happens to start with. */
function isCurrent(pathname: string | null, href: string): boolean {
  if (pathname === null) {
    return false
  }
  if (href === '/dashboard') {
    return pathname === '/dashboard'
  }
  if (href === '/dashboard/settings') {
    return SETTINGS_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`))
  }
  return pathname === href || pathname.startsWith(`${href}/`)
}
