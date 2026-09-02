'use client'

import { useQuery } from '@tanstack/react-query'
import Link from 'next/link'

import { ErrorNotice } from '@/components/error-notice'
import { projectStatusLabel } from '@/features/projects/status-labels'
import type { ApiError } from '@/lib/api/client'
import { showApiV1DashboardSummaryGet } from '@/lib/api/generated/dashboard/dashboard'
import type { DashboardSummaryResponse, QuotaResource } from '@/lib/api/generated/model'

import { useWorkspaceScope } from './workspace-context'

const USAGE_LABELS: Record<QuotaResource, string> = {
  analyses: 'Analyses',
  stock_requests: 'Stock requests',
  generated_images: 'Generated images',
  generated_videos: 'Generated videos',
  generated_seconds: 'Generated seconds',
  social_publications: 'Social publications',
}

/**
 * The first screen of a Workspace, answered by one backend read.
 *
 * Every number here is the backend's, including what has been spent against each monthly
 * budget, so the browser never adds up a total of its own.
 */
export function WorkspaceOverview() {
  const { active } = useWorkspaceScope()
  const summary = useQuery<DashboardSummaryResponse, ApiError>({
    queryKey: ['/api/v1/dashboard/summary', active.id],
    queryFn: ({ signal }) =>
      showApiV1DashboardSummaryGet({ workspace_id: active.id }, { signal }),
    retry: false,
  })

  if (summary.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading your workspace…
      </p>
    )
  }
  if (summary.isError) {
    return <ErrorNotice error={summary.error} />
  }

  const { projects, usage, topCandidates } = summary.data

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold tracking-tight">{summary.data.workspace.name}</h1>

      <div className="grid gap-4 sm:grid-cols-3">
        <section role="group" aria-label="Active projects" className="rounded-lg border p-4">
          <p className="text-xs uppercase tracking-wide text-muted-foreground">Active projects</p>
          <p className="text-2xl font-semibold">{projects.activeCount}</p>
        </section>
        {usage.map((entry) => (
          <section
            key={entry.resource}
            role="group"
            aria-label={USAGE_LABELS[entry.resource]}
            className="rounded-lg border p-4"
          >
            <p className="text-xs uppercase tracking-wide text-muted-foreground">
              {USAGE_LABELS[entry.resource]}
            </p>
            <p className="text-2xl font-semibold">
              {formatConsumed(entry.consumed)} of {entry.limit}
            </p>
          </section>
        ))}
      </div>

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Recent projects</h2>
        {projects.recent.length === 0 ? (
          <p className="text-sm text-muted-foreground">No projects yet.</p>
        ) : (
          <ul aria-label="Recent projects" className="divide-y rounded-lg border">
            {projects.recent.map((project) => (
              <li key={project.id} className="flex items-center justify-between px-4 py-3">
                <Link href={`/dashboard/projects/${project.id}`} className="text-sm font-medium">
                  {project.name}
                </Link>
                <span className="text-xs text-muted-foreground">
                  {projectStatusLabel(project.status)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Top candidates</h2>
        {topCandidates.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing is waiting for review.</p>
        ) : (
          <ul aria-label="Top candidates" className="divide-y rounded-lg border">
            {topCandidates.map((candidate) => (
              <li key={candidate.id} className="space-y-1 px-4 py-3">
                <p className="text-sm font-medium">{candidate.hook}</p>
                <p className="text-xs text-muted-foreground">
                  <Link href={`/dashboard/projects/${candidate.projectId}`}>
                    Review in {candidate.projectName}
                  </Link>
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

/** Show what has been spent without inventing precision the backend did not report. */
function formatConsumed(consumed: number): string {
  return Number.isInteger(consumed) ? String(consumed) : consumed.toFixed(1)
}
