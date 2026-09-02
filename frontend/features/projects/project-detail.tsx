'use client'

import { useQuery } from '@tanstack/react-query'

import { ErrorNotice } from '@/components/error-notice'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { showApiV1ProjectsProjectIdGet } from '@/lib/api/generated/projects/projects'
import type { ProjectResponse } from '@/lib/api/generated/model'

import { projectStatusLabel } from './status-labels'

/**
 * Show one Project of the active Workspace.
 *
 * A Project belonging to another Workspace is refused exactly like a Project that never
 * existed, so this renders the backend's answer as it stands and never explains the
 * difference between "not yours" and "not there".
 */
export function ProjectDetail({ projectId }: { projectId: string }) {
  const { active } = useWorkspaceScope()
  const project = useQuery<ProjectResponse, ApiError>({
    queryKey: ['/api/v1/projects', active.id, projectId],
    queryFn: ({ signal }) =>
      showApiV1ProjectsProjectIdGet(projectId, { workspace_id: active.id }, { signal }),
    retry: false,
  })

  if (project.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading project…
      </p>
    )
  }
  if (project.isError) {
    return <ErrorNotice error={project.error} />
  }

  return (
    <article className="space-y-2">
      <h1 className="text-2xl font-semibold tracking-tight">{project.data.name}</h1>
      <p className="text-sm text-muted-foreground">{projectStatusLabel(project.data.status)}</p>
    </article>
  )
}
