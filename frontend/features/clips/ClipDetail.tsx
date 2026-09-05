'use client'

import { BrollProvenanceList } from '@/features/broll/BrollProvenanceList'
import { RequireSession } from '@/features/auth/require-session'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

/**
 * One clip, seen from outside the editor.
 *
 * Only the B-roll this clip carries is answered here. The Revision history and the
 * exports belong to the tasks that own project review and the content library; a page
 * that guessed at them would be a page a member could not trust.
 */
export function ClipDetail({
  candidateId,
  projectId,
}: {
  candidateId: string
  projectId: string | null
}) {
  return (
    <RequireSession>
      <WorkspaceProvider>
        <section className="space-y-3">
          <h1 className="text-2xl font-semibold tracking-tight">Clip</h1>
          <h2 className="text-sm font-medium">B-roll in this clip</h2>
          {projectId === null ? (
            <p className="text-sm text-muted-foreground">
              Open this clip from its Project to see the B-roll it carries.
            </p>
          ) : (
            <BrollProvenanceList projectId={projectId} candidateId={candidateId} />
          )}
        </section>
      </WorkspaceProvider>
    </RequireSession>
  )
}
