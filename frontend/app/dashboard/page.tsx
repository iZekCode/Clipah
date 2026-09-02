import { DashboardShell } from '@/components/dashboard-shell'

// The shell renders before authentication exists in the frontend. Task 18 replaces these
// placeholders with the signed-in User and selected Workspace read from `/api/v1/me`.
const PLACEHOLDER_USER = { displayName: 'Signed-in user' }
const PLACEHOLDER_WORKSPACE = 'Your workspace'

/** The authenticated landing area, currently the empty frame later tasks fill in. */
export default function DashboardPage() {
  return (
    <DashboardShell user={PLACEHOLDER_USER} workspaceName={PLACEHOLDER_WORKSPACE}>
      <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Projects, clips, and job progress appear here as each area is built.
      </p>
    </DashboardShell>
  )
}
