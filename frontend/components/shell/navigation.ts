import {
  Clapperboard,
  FolderOpen,
  Home,
  Image as ImageIcon,
  LayoutTemplate,
  Palette,
  Send,
  Settings,
  type LucideIcon,
} from 'lucide-react'

/** One destination in the Workspace navigation. */
export interface NavigationEntry {
  href: string
  label: string
  icon: LucideIcon
}

export const PRIMARY_NAVIGATION: NavigationEntry[] = [
  { href: '/dashboard', label: 'Home', icon: Home },
  { href: '/dashboard/projects', label: 'Projects', icon: FolderOpen },
  { href: '/dashboard/clips', label: 'Clips', icon: Clapperboard },
  { href: '/dashboard/publishing', label: 'Publishing', icon: Send },
]

export const LIBRARY_NAVIGATION: NavigationEntry[] = [
  { href: '/dashboard/assets', label: 'Assets', icon: ImageIcon },
  { href: '/dashboard/templates', label: 'Templates', icon: LayoutTemplate },
  { href: '/dashboard/brand-kits', label: 'Brand kits', icon: Palette },
]

export const SETTINGS_ENTRY: NavigationEntry = {
  href: '/dashboard/settings',
  label: 'Settings',
  icon: Settings,
}

// Team and Connections live inside Settings, but their established URLs stay reachable.
const SETTINGS_ROUTES = ['/dashboard/settings', '/dashboard/team']

/** The entry for the area being viewed, not every entry the route happens to start with. */
export function isCurrent(pathname: string | null, href: string): boolean {
  if (pathname === null) return false
  if (href === '/dashboard') return pathname === '/dashboard'
  if (href === SETTINGS_ENTRY.href) {
    return SETTINGS_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`))
  }
  return pathname === href || pathname.startsWith(`${href}/`)
}

export const RAIL_PINNED_KEY = 'clipah.rail.pinned'

/** Whether this browser asked for the wide rail; storage that refuses to answer means no. */
export function readRailPinned(): boolean {
  try {
    return window.localStorage.getItem(RAIL_PINNED_KEY) === 'true'
  } catch {
    return false
  }
}

/** Remember the rail width for this browser, and carry on if storage is unavailable. */
export function writeRailPinned(pinned: boolean): void {
  try {
    window.localStorage.setItem(RAIL_PINNED_KEY, String(pinned))
  } catch {
    // A private window or blocked storage only costs the member their preference.
  }
}
