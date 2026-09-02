'use client'

import type { ReactNode } from 'react'

import { DashboardShell } from '@/components/dashboard-shell'
import { RequireSession } from '@/features/auth/require-session'
import { useSession } from '@/features/auth/session'
import { JobCenter } from '@/features/jobs/job-center'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'
import { WorkspaceSwitcher } from '@/features/workspaces/workspace-switcher'

/**
 * Everything an authenticated page sits inside: a confirmed Session, the Workspace it is
 * scoped to, and the shell that shows both. The order matters, because the Workspace list
 * is only worth asking for once the backend has confirmed who is asking.
 */
export function DashboardFrame({ children }: { children: ReactNode }) {
  return (
    <RequireSession>
      <WorkspaceProvider>
        <SignedInShell>{children}</SignedInShell>
      </WorkspaceProvider>
    </RequireSession>
  )
}

function SignedInShell({ children }: { children: ReactNode }) {
  const session = useSession()
  const user = session.data

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
      jobCenter={<JobCenter />}
    >
      {children}
    </DashboardShell>
  )
}
