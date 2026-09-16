'use client'

import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Clapperboard, FolderOpen, Sparkles } from 'lucide-react'
import Link from 'next/link'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { MediaCard, ProjectThumbnail } from '@/components/media-card'
import { PageHeader, Section } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { formatDuration } from '@/features/clips/ClipCard'
import { JOB_KIND_LABELS } from '@/features/jobs/job-center'
import { NewProjectButton } from '@/features/projects/new-project'
import { projectStatusLabel, projectStatusTone } from '@/features/projects/status-labels'
import type { ApiError } from '@/lib/api/client'
import { showApiV1DashboardSummaryGet } from '@/lib/api/generated/dashboard/dashboard'
import type { ClipPageResponse, DashboardSummaryResponse } from '@/lib/api/generated/model'
import { browseClipCollectionApiV1ClipsGet } from '@/lib/api/generated/studio/studio'

import { useWorkspaceScope } from './workspace-context'

/**
 * Home: the one place that answers "what should I do next?".
 *
 * Starting something new comes first, then the work already in motion, then the moments
 * waiting for a decision. Budget details live in Settings; the numbers that matter when
 * starting metered work are shown where that work is started.
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
      <div className="space-y-6">
        <PageHeader title="Home" />
        <LoadingState label="Loading your workspace…" variant="cards" />
      </div>
    )
  }
  if (summary.isError) {
    return (
      <div className="space-y-6">
        <PageHeader title="Home" />
        <ErrorNotice error={summary.error} onRetry={() => void summary.refetch()} />
      </div>
    )
  }

  const { projects, topCandidates, jobs } = summary.data

  return (
    <div className="space-y-10">
      <PageHeader
        title={summary.data.workspace.name}
        description="Turn a long video into short clips: add a video, choose the best moments, edit, export, and publish."
        actions={<NewProjectButton size="lg" />}
      />

      {projects.activeCount === 0 ? (
        <EmptyState
          icon={Sparkles}
          title="Start with your first video"
          description="Upload a recording or paste a YouTube link. Clipah transcribes it and suggests the moments worth sharing."
          action={<NewProjectButton />}
        />
      ) : null}

      {jobs.active.length === 0 ? null : (
        <Section title="In progress" description="Work that is running right now.">
          <ul aria-label="Work in progress" className="surface divide-y">
            {jobs.active.map((job) => (
              <li key={job.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium">{JOB_KIND_LABELS[job.kind] ?? job.kind}</p>
                  <p className="text-xs text-muted-foreground">{job.stage}</p>
                </div>
                <Link
                  href={`/dashboard/projects/${job.projectId}`}
                  className="shrink-0 text-sm font-medium text-primary hover:underline"
                >
                  Open project
                </Link>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {projects.recent.length === 0 ? null : (
        <Section
          title="Recent projects"
          actions={
            <Link
              href="/dashboard/projects"
              className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
            >
              All projects <ArrowRight aria-hidden="true" className="size-4" />
            </Link>
          }
        >
          <ul aria-label="Recent projects" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {projects.recent.map((project) => (
              <li key={project.id}>
                <MediaCard
                  href={`/dashboard/projects/${project.id}`}
                  title={project.name}
                  thumbnail={
                    <ProjectThumbnail
                      workspaceId={active.id}
                      projectId={project.id}
                      hasMedia={project.status !== 'created' && project.status !== 'uploading'}
                    />
                  }
                  status={
                    <StatusBadge tone={projectStatusTone(project.status)}>
                      {projectStatusLabel(project.status)}
                    </StatusBadge>
                  }
                  subtitle={`Updated ${formatDate(project.updatedAt)}`}
                />
              </li>
            ))}
          </ul>
        </Section>
      )}

      <ContinueEditing workspaceId={active.id} />

      <Section
        title="Moments to review"
        description="The strongest suggestions waiting for your decision."
        actions={
          <Link
            href="/dashboard/clips"
            className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
          >
            All clips <ArrowRight aria-hidden="true" className="size-4" />
          </Link>
        }
      >
        {topCandidates.length === 0 ? (
          <EmptyState
            compact
            icon={Clapperboard}
            title="Nothing is waiting for review"
            description="Suggested moments appear here once a video has been processed."
          />
        ) : (
          <ul aria-label="Top candidates" className="surface divide-y">
            {topCandidates.map((candidate) => (
              <li key={candidate.id} className="flex items-center justify-between gap-4 px-4 py-3">
                <div className="min-w-0 space-y-0.5">
                  <p className="truncate text-sm font-medium">{candidate.hook}</p>
                  <p className="text-xs text-muted-foreground">
                    {candidate.projectName} · {formatDuration(candidate.endMs - candidate.startMs)}
                  </p>
                </div>
                <Link
                  href={`/dashboard/clips/${candidate.id}`}
                  className="shrink-0 text-sm font-medium text-primary hover:underline"
                >
                  Review in {candidate.projectName}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  )
}

/** Clips the member already opened in the editor and has not exported yet. */
function ContinueEditing({ workspaceId }: { workspaceId: string }) {
  const edited = useQuery<ClipPageResponse, ApiError>({
    queryKey: ['/api/v1/clips', workspaceId, 'edited', 'home'],
    queryFn: ({ signal }) =>
      browseClipCollectionApiV1ClipsGet(
        { workspace_id: workspaceId, stage: 'edited', limit: 4 },
        { signal },
      ),
    retry: false,
  })

  const clips = edited.data?.clips ?? []
  if (clips.length === 0) {
    return null
  }
  return (
    <Section title="Continue editing" description="Clips you opened in the editor and have not exported yet.">
      <ul aria-label="Continue editing" className="grid gap-3 sm:grid-cols-2">
        {clips.map((clip) => (
          <li key={clip.id} className="surface flex items-center gap-3 p-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground">
              <FolderOpen aria-hidden="true" className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{clip.hook}</p>
              <p className="truncate text-xs text-muted-foreground">{clip.projectName}</p>
            </div>
            {clip.editId === null ? null : (
              <Link
                href={`/editor/${clip.editId}`}
                className="shrink-0 rounded-lg border px-3 py-1.5 text-sm font-medium hover:bg-secondary"
              >
                Continue
              </Link>
            )}
          </li>
        ))}
      </ul>
    </Section>
  )
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
