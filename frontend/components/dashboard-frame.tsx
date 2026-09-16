'use client'

import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useState, type ReactNode } from 'react'

import { DashboardShell } from '@/components/dashboard-shell'
import { RequireSession } from '@/features/auth/require-session'
import { useSession } from '@/features/auth/session'
import { JobCenter } from '@/features/jobs/job-center'
import { NewProjectButton, NewProjectProvider } from '@/features/projects/new-project'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'
import { WorkspaceSwitcher } from '@/features/workspaces/workspace-switcher'
import { logoutApiV1AuthLogoutPost } from '@/lib/api/generated/auth/auth'

/**
 * Everything an authenticated page sits inside: a confirmed Session, the Workspace it is
 * scoped to, and the shell that shows both. The order matters, because the Workspace list
 * is only worth asking for once the backend has confirmed who is asking.
 */
export function DashboardFrame({ children }: { children: ReactNode }) {
  return (
    <RequireSession>
      <WorkspaceProvider>
        <NewProjectProvider>
          <SignedInShell>{children}</SignedInShell>
        </NewProjectProvider>
      </WorkspaceProvider>
    </RequireSession>
  )
}

function SignedInShell({ children }: { children: ReactNode }) {
  const session = useSession()
  const queryClient = useQueryClient()
  const [running, setRunning] = useState(0)
  const user = session.data

  const signOut = useCallback(() => {
    void logoutApiV1AuthLogoutPost()
      .catch(() => undefined)
      .finally(() => {
        queryClient.clear()
        window.location.assign('/signin')
      })
  }, [queryClient])

  if (user === undefined) {
    return (
      <p role="status" className="p-6 text-sm text-muted-foreground">
        Checking your session…
      </p>
    )
  }

  return (
    <DashboardShell
      user={user}
      workspaceSwitcher={<WorkspaceSwitcher />}
      jobCenter={<JobCenter onActiveCountChange={setRunning} />}
      activeJobCount={running}
      newProject={<NewProjectButton className="w-full" />}
      onSignOut={signOut}
    >
      {children}
    </DashboardShell>
  )
}
