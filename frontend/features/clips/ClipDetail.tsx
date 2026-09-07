'use client'

import { BrollProvenanceList } from '@/features/broll/BrollProvenanceList'
import { RequireSession } from '@/features/auth/require-session'
import { useWorkspaceScope, WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { EvidencePanel } from './EvidencePanel'
import { VariantLab } from './VariantLab'

/**
 * One clip, seen from outside the editor.
 *
 * What this page answers is what a member needs before deciding to edit: the B-roll it
 * carries, how the moment reads at other lengths, and what its claims rest on. The
 * Revision history and the exports belong to the tasks that own project review and the
 * content library; a page that guessed at them would be a page a member could not trust.
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
        <section className="space-y-4">
          <h1 className="text-2xl font-semibold tracking-tight">Clip</h1>
          {projectId === null ? (
            <p className="text-sm text-muted-foreground">
              Open this clip from its Project to see the B-roll it carries, its variants, and
              its sources.
            </p>
          ) : (
            <ClipReview projectId={projectId} candidateId={candidateId} />
          )}
        </section>
      </WorkspaceProvider>
    </RequireSession>
  )
}

/** The three review surfaces, each scoped to the Workspace the member is acting in. */
function ClipReview({ projectId, candidateId }: { projectId: string; candidateId: string }) {
  const { active } = useWorkspaceScope()
  const workspaceId = active.id
  return (
    <div className="space-y-4">
      <section className="space-y-2">
        <h2 className="text-sm font-medium">B-roll in this clip</h2>
        <BrollProvenanceList projectId={projectId} candidateId={candidateId} />
      </section>
      <VariantLab
        projectId={projectId}
        candidateId={candidateId}
        workspaceId={workspaceId}
        proxyUrl={`/api/v1/projects/${projectId}/proxy?workspace_id=${workspaceId}`}
      />
      <EvidencePanel
        projectId={projectId}
        candidateId={candidateId}
        workspaceId={workspaceId}
      />
    </div>
  )
}
