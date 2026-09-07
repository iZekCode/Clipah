import { RequireSession } from '@/features/auth/require-session'
import { TeamSettings } from '@/features/team/TeamSettings'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

/** Team membership, roles, invitations, and ownership controls. */
export default function TeamPage() {
  return (
    <RequireSession>
      <WorkspaceProvider>
        <main className="space-y-4">
          <h1 className="text-2xl font-semibold tracking-tight">Team</h1>
          <TeamSettings />
        </main>
      </WorkspaceProvider>
    </RequireSession>
  )
}
