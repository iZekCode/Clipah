'use client'

import { usePathname } from 'next/navigation'
import { useEffect, useState, type ReactNode } from 'react'

import { AccountMenu } from '@/components/shell/account-menu'
import { DropTarget } from '@/components/shell/drop-target'
import { PhoneTabs } from '@/components/shell/phone-tabs'
import { Rail } from '@/components/shell/rail'
import { RenderQueue } from '@/components/shell/render-queue'
import { TopBar } from '@/components/shell/top-bar'
import { notify } from '@/lib/notify'

/** The signed-in User, as much of it as the shell needs to show. */
export interface ShellUser {
  displayName: string | null
  email?: string
}

/**
 * The frame every authenticated page renders inside.
 *
 * A narrow rail holds the journey — Home, Projects, Clips, Publishing — with the library and
 * Settings; the top bar holds what is needed from anywhere. Names come from the API, so they
 * are rendered as text and never as markup.
 */
export function DashboardShell({
  user,
  workspaceSwitcher,
  jobCenter,
  newProject,
  activeJobCount = 0,
  onSignOut,
  onOpenCommandPalette,
  onDropFile,
  children,
}: {
  user: ShellUser
  workspaceSwitcher: ReactNode
  jobCenter: ReactNode
  newProject?: ReactNode
  activeJobCount?: number
  onSignOut?: () => void
  onOpenCommandPalette?: () => void
  onDropFile?: (file: File) => void
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
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-popover focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <Rail pathname={pathname} newProject={newProject} open={navigationOpen} onClose={() => setNavigationOpen(false)} />
      {navigationOpen ? (
        <div aria-hidden="true" className="fixed inset-0 z-30 bg-background/70 md:hidden" onClick={() => setNavigationOpen(false)} />
      ) : null}
      <div className="flex min-w-0 flex-1 flex-col pb-14 md:pb-0">
        <TopBar
          workspaceSwitcher={workspaceSwitcher}
          onOpenCommandPalette={onOpenCommandPalette}
          activity={<RenderQueue count={activeJobCount}>{jobCenter}</RenderQueue>}
          account={<AccountMenu user={user} onSignOut={onSignOut} />}
        />
        <main id="main-content" className="mx-auto w-full max-w-studio flex-1 px-4 py-6 sm:px-6 lg:py-8">
          {children}
        </main>
      </div>
      <PhoneTabs
        pathname={pathname}
        navigationOpen={navigationOpen}
        onToggleNavigation={() => setNavigationOpen((open) => !open)}
      />
      {onDropFile === undefined ? null : (
        <DropTarget
          onFile={onDropFile}
          onRefused={() => notify.info('That file isn’t a video. Drop an MP4, MOV, or WebM.')}
        />
      )}
    </div>
  )
}
