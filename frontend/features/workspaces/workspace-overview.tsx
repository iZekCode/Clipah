'use client'

import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Clapperboard } from 'lucide-react'
import Link from 'next/link'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { MediaCard } from '@/components/media-card'
import { Poster } from '@/components/media/poster'
import { PIPELINE_KINDS, StageBar } from '@/components/media/stage-bar'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { NewProjectButton } from '@/features/projects/new-project'
import { projectStatusLabel, projectStatusTone } from '@/features/projects/status-labels'
import type { ApiError } from '@/lib/api/client'
import { showApiV1DashboardSummaryGet } from '@/lib/api/generated/dashboard/dashboard'
import type {
  ClipPageResponse,
  ClipSummaryResponse,
  DashboardCandidateResponse,
  DashboardJobResponse,
  DashboardProjectResponse,
  DashboardSummaryResponse,
} from '@/lib/api/generated/model'
import { browseClipCollectionApiV1ClipsGet } from '@/lib/api/generated/studio/studio'

import { useWorkspaceScope } from './workspace-context'

/** How often Home re-reads the summary while any work is running. */
const PROCESSING_REFRESH_MS = 5_000

/**
 * Home: the work to pick up, the work in motion, and the moments waiting for a decision.
 *
 * An empty Workspace is the importer itself. Otherwise the clip edited most recently leads,
 * processing shows the real pipeline stages, the strongest suggestions sit in a reel of
 * posters, and recent Projects follow.
 */
export function WorkspaceOverview() {
  const { active } = useWorkspaceScope()
  const summary = useQuery<DashboardSummaryResponse, ApiError>({
    queryKey: ['/api/v1/dashboard/summary', active.id],
    queryFn: ({ signal }) => showApiV1DashboardSummaryGet({ workspace_id: active.id }, { signal }),
    retry: false,
    // "Processing now" follows the work as it happens rather than only when the tab
    // regains focus.
    refetchInterval: (query) =>
      (query.state.data?.jobs.active.length ?? 0) > 0 ? PROCESSING_REFRESH_MS : false,
  })
  const editing = useQuery<ClipPageResponse, ApiError>({
    queryKey: ['/api/v1/clips', active.id, 'edited', 'recent'],
    queryFn: ({ signal }) =>
      browseClipCollectionApiV1ClipsGet(
        { workspace_id: active.id, stage: 'edited', order: 'recent', limit: 5 },
        { signal },
      ),
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
  const clips = (editing.data?.clips ?? []).filter((clip) => clip.editId !== null)
  const lead = clips[0] ?? null
  const firstRun = projects.activeCount === 0
  const names = new Map(projects.recent.map((project) => [project.id, project.name]))
  const processing = latestPipelineJobs(jobs.active)

  return (
    <div className="space-y-10">
      <PageHeader
        title={summary.data.workspace.name}
        actions={
          <NewProjectButton
            size="lg"
            variant={lead === null && !firstRun ? 'default' : 'secondary'}
          />
        }
      />
      {firstRun ? <FirstRun /> : null}
      {lead === null ? null : <ContinueEditing lead={lead} others={clips.slice(1)} />}
      {processing.length === 0 ? null : <ProcessingNow jobs={processing} names={names} />}
      {topCandidates.length > 0 ? (
        <ReadyToReview candidates={topCandidates} />
      ) : firstRun ? null : (
        <EmptyState
          compact
          icon={Clapperboard}
          title="Nothing is waiting for review"
          description="Suggested moments appear here once a video has been processed."
        />
      )}
      {projects.recent.length === 0 ? null : <RecentProjects projects={projects.recent} />}
    </div>
  )
}

function FirstRun() {
  return (
    <section
      aria-labelledby="first-run-title"
      className="rounded-lg border-2 border-dashed border-line-strong bg-card/40 px-6 py-12 sm:px-10"
    >
      <h2 id="first-run-title" className="font-display text-h1 sm:text-display">
        Drop a long video to start
      </h2>
      <p className="mt-3 max-w-xl text-body text-muted-foreground">
        Drag a podcast, interview, or stream recording anywhere on this page, or choose a file
        or a YouTube link. Clipah transcribes it and finds the moments worth posting.
      </p>
      <div className="mt-6">
        <NewProjectButton size="lg" label="Choose a video" />
      </div>
    </section>
  )
}

function ContinueEditing({
  lead,
  others,
}: {
  lead: ClipSummaryResponse
  others: ClipSummaryResponse[]
}) {
  return (
    <section aria-labelledby="continue-title" className="space-y-4">
      <h2 id="continue-title" className="text-title">
        Continue editing
      </h2>
      <div className="grid gap-6 rounded-lg border bg-card p-4 sm:grid-cols-[200px_minmax(0,1fr)] sm:p-6 lg:grid-cols-[240px_minmax(0,1fr)]">
        <div className="relative aspect-[9/16] overflow-hidden rounded-md">
          <Poster
            projectId={lead.projectId}
            startMs={lead.startMs}
            endMs={lead.endMs}
            aspect="portrait"
            durationMs={lead.durationMs}
          />
        </div>
        <div className="flex min-w-0 flex-col justify-center gap-3">
          <p className="text-caption font-medium uppercase tracking-wide text-subtle-foreground">
            {lead.projectName}
          </p>
          <p className="font-display text-balance text-h1 lg:text-display">{lead.hook}</p>
          <p className="font-mono text-caption text-muted-foreground">
            {lead.editUpdatedAt === null ? '' : `Saved ${formatWhen(lead.editUpdatedAt)} · `}
            Revision {lead.currentRevision ?? 1}
          </p>
          <div>
            <Button asChild size="lg">
              <Link href={`/editor/${lead.editId}`}>Continue editing</Link>
            </Button>
          </div>
        </div>
      </div>
      {others.length === 0 ? null : (
        <ul aria-label="More clips in editing" className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {others.map((clip) => (
            <li key={clip.id}>
              <MediaCard
                href={`/editor/${clip.editId}`}
                title={clip.hook}
                aspect="portrait"
                subtitle={clip.projectName}
                thumbnail={
                  <Poster
                    projectId={clip.projectId}
                    startMs={clip.startMs}
                    endMs={clip.endMs}
                    aspect="portrait"
                    durationMs={clip.durationMs}
                  />
                }
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function ProcessingNow({
  jobs,
  names,
}: {
  jobs: DashboardJobResponse[]
  names: Map<string, string>
}) {
  return (
    <section aria-labelledby="processing-title" className="space-y-3">
      <h2 id="processing-title" className="text-title">
        Processing now
      </h2>
      <ul aria-label="Processing now" className="space-y-2">
        {jobs.map((job) => (
          <li
            key={job.id}
            className="grid gap-3 rounded-lg border bg-card p-4 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)_auto] sm:items-center"
          >
            <p className="truncate text-small font-semibold">
              {names.get(job.projectId) ?? 'Project'}
            </p>
            <StageBar kind={job.kind} status={job.status} />
            <Link
              href={`/dashboard/projects/${job.projectId}`}
              className="text-small font-semibold text-primary hover:underline"
            >
              Open project
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ReadyToReview({ candidates }: { candidates: DashboardCandidateResponse[] }) {
  return (
    <section aria-labelledby="ready-title" className="space-y-3">
      <div className="flex items-end justify-between gap-3">
        <h2 id="ready-title" className="text-title">
          Ready to review
        </h2>
        <Link
          href="/dashboard/clips"
          className="inline-flex items-center gap-1 text-small font-semibold text-primary hover:underline"
        >
          All clips <ArrowRight aria-hidden="true" strokeWidth={1.75} className="size-4" />
        </Link>
      </div>
      <ul
        aria-label="Ready to review"
        className="-mx-4 flex snap-x gap-3 overflow-x-auto px-4 pb-2 sm:-mx-6 sm:px-6"
      >
        {candidates.map((candidate) => (
          <li key={candidate.id} className="w-40 shrink-0 snap-start sm:w-44">
            <MediaCard
              href={`/dashboard/projects/${candidate.projectId}/review?moment=${candidate.id}`}
              title={candidate.hook}
              hideTitle
              aspect="portrait"
              subtitle={candidate.projectName}
              thumbnail={
                <Poster
                  projectId={candidate.projectId}
                  startMs={candidate.startMs}
                  endMs={candidate.endMs}
                  aspect="portrait"
                  rank={candidate.rank}
                  durationMs={candidate.endMs - candidate.startMs}
                  hook={candidate.hook}
                />
              }
            />
          </li>
        ))}
      </ul>
    </section>
  )
}

function RecentProjects({ projects }: { projects: DashboardProjectResponse[] }) {
  return (
    <section aria-labelledby="recent-title" className="space-y-3">
      <div className="flex items-end justify-between gap-3">
        <h2 id="recent-title" className="text-title">
          Recent projects
        </h2>
        <Link
          href="/dashboard/projects"
          className="inline-flex items-center gap-1 text-small font-semibold text-primary hover:underline"
        >
          All projects <ArrowRight aria-hidden="true" strokeWidth={1.75} className="size-4" />
        </Link>
      </div>
      <ul aria-label="Recent projects" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {projects.map((project) => (
          <li key={project.id}>
            <MediaCard
              href={`/dashboard/projects/${project.id}`}
              title={project.name}
              thumbnail={
                <Poster
                  projectId={project.id}
                  hasMedia={project.status !== 'created' && project.status !== 'uploading'}
                />
              }
              status={
                <StatusBadge tone={projectStatusTone(project.status)} appearance="overlay">
                  {projectStatusLabel(project.status, project.sourceKind)}
                </StatusBadge>
              }
              subtitle={`Updated ${formatWhen(project.updatedAt)}`}
            />
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The newest pipeline job per Project, so each Project gets one stage bar. */
function latestPipelineJobs(jobs: DashboardJobResponse[]): DashboardJobResponse[] {
  const latest = new Map<string, DashboardJobResponse>()
  for (const job of jobs) {
    if (!PIPELINE_KINDS.has(job.kind)) continue
    const known = latest.get(job.projectId)
    if (known === undefined || known.updatedAt < job.updatedAt) latest.set(job.projectId, job)
  }
  return [...latest.values()]
}

function formatWhen(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      })
}
