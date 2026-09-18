'use client'

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Clapperboard, History, Scissors } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useState } from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { TabList, TabPanel, useUrlTab } from '@/components/url-tabs'
import { ClipList } from '@/features/clips/ClipList'
import { useProjectCandidates } from '@/features/clips/use-project-candidates'
import { ExportList } from '@/features/exports/export-list'
import { JOB_KIND_LABELS } from '@/features/jobs/job-center'
import { UploadPanel, type ProjectJob } from '@/features/uploads/UploadPanel'
import { mayWriteProjects } from '@/features/workspaces/roles'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type {
  CandidateResponse,
  ClipPageResponse,
  ProjectResponse,
} from '@/lib/api/generated/model'
import { showApiV1ProjectsProjectIdGet } from '@/lib/api/generated/projects/projects'
import { browseClipCollectionApiV1ClipsGet } from '@/lib/api/generated/studio/studio'
import { formatClock } from '@/lib/media/time'

import { SourceColumn, useRequestedMomentMs } from './source-column'
import { projectIsProcessing, projectStatusLabel, projectStatusTone } from './status-labels'

const TABS = [
  { id: 'moments', label: 'Moments' },
  { id: 'edits', label: 'Edits' },
  { id: 'exports', label: 'Exports' },
  { id: 'activity', label: 'Activity' },
] as const

type TabId = (typeof TABS)[number]['id']
const TAB_IDS = TABS.map((entry) => entry.id)

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
    return <LoadingState label="Loading project…" variant="cards" count={1} />
  }
  if (project.isError) {
    return <ErrorNotice error={project.error} />
  }

  return <LoadedProject project={project.data} onChanged={() => void project.refetch()} />
}

function LoadedProject({ project, onChanged }: { project: ProjectResponse; onChanged: () => void }) {
  const [tab, choose] = useUrlTab<TabId>(TAB_IDS, 'moments')
  const [jobs, setJobs] = useState<ProjectJob[]>([])
  const [selected, setSelected] = useState<CandidateResponse | null>(null)
  const queryClient = useQueryClient()
  const { active } = useWorkspaceScope()
  const requestedMs = useRequestedMomentMs()
  const ready = project.status === 'ready'
  const { candidates: moments } = useProjectCandidates(project.id, { enabled: ready })
  const needsMedia = project.status === 'created' || project.status === 'failed'
  const processing = projectIsProcessing(project.status)
  const fileInputId = `project-${project.id}-file`

  // Every job event for this Project may have moved its durable state, so the Project and
  // what hangs off it are read again rather than guessed at from the event.
  const onJob = useCallback(
    (job: ProjectJob) => {
      setJobs((known) =>
        known.some((entry) => entry.jobId === job.jobId)
          ? known.map((entry) => (entry.jobId === job.jobId ? job : entry))
          : [...known, job],
      )
      if (['succeeded', 'failed', 'canceled'].includes(job.status)) {
        onChanged()
        void queryClient.invalidateQueries({
          queryKey: ['/api/v1/projects/candidates', active.id, project.id],
        })
        void queryClient.invalidateQueries({ queryKey: ['/api/v1/exports', active.id] })
      }
    },
    [active.id, onChanged, project.id, queryClient],
  )

  return (
    <article className="space-y-8">
      <PageHeader
        title={project.name}
        crumbs={[{ href: '/dashboard/projects', label: 'Projects' }]}
        meta={
          <StatusBadge tone={projectStatusTone(project.status)}>
            {projectStatusLabel(project.status)}
          </StatusBadge>
        }
        actions={
          needsMedia && mayWriteProjects(active.role) ? (
            <Button size="lg" onClick={() => document.getElementById(fileInputId)?.click()}>
              Add media
            </Button>
          ) : ready ? (
            <>
              <Button asChild size="lg">
                <Link href={`/dashboard/projects/${project.id}/review`}>Review moments</Link>
              </Button>
              <Button variant="secondary" size="lg" onClick={() => choose('exports')}>
                Open exports
              </Button>
            </>
          ) : undefined
        }
      />

      {ready ? (
        <UploadPanel projectId={project.id} addMedia={false} onJob={onJob} />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
          <UploadPanel
            projectId={project.id}
            addMedia={needsMedia}
            onJob={onJob}
            fileInputId={fileInputId}
          />
          {processing || requestedMs !== null ? (
            <SourceColumn
              projectId={project.id}
              status={project.status}
              candidates={[]}
              selected={null}
            />
          ) : null}
        </div>
      )}

      <div>
        <TabList
          label="Project contents"
          tabs={TABS}
          active={tab}
          onChoose={choose}
          idPrefix="project"
        />
        <TabPanel idPrefix="project" id="moments" active={tab}>
          {ready ? (
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px] xl:grid-cols-[minmax(0,1fr)_420px]">
              <ClipList
                projectId={project.id}
                selectedId={selected?.id ?? null}
                onSelect={setSelected}
              />
              <SourceColumn
                projectId={project.id}
                status={project.status}
                candidates={moments}
                selected={selected}
              />
            </div>
          ) : (
            <EmptyState
              compact
              icon={Clapperboard}
              title="Moments appear when processing finishes"
              description={
                processing
                  ? 'Clipah is working on this video. Suggested moments will show up here.'
                  : 'Add a video to this project to get suggested moments.'
              }
            />
          )}
        </TabPanel>
        <TabPanel idPrefix="project" id="edits" active={tab}>
          <ProjectEdits projectId={project.id} />
        </TabPanel>
        <TabPanel idPrefix="project" id="exports" active={tab}>
          <ExportList projectId={project.id} />
        </TabPanel>
        <TabPanel idPrefix="project" id="activity" active={tab}>
          <ProjectActivity jobs={jobs} />
        </TabPanel>
      </div>
    </article>
  )
}

/** The moments of this Project that already have an Edit, with a way back into each. */
function ProjectEdits({ projectId }: { projectId: string }) {
  const { active } = useWorkspaceScope()
  const clips = useQuery<ClipPageResponse, ApiError>({
    queryKey: ['/api/v1/clips', active.id, 'project', projectId],
    queryFn: ({ signal }) =>
      browseClipCollectionApiV1ClipsGet(
        { workspace_id: active.id, projectId, limit: 100 },
        { signal },
      ),
    retry: false,
  })

  if (clips.isPending) {
    return <LoadingState label="Loading edits…" variant="rows" count={2} />
  }
  if (clips.isError) {
    return <ErrorNotice error={clips.error} onRetry={() => void clips.refetch()} />
  }
  const edited = clips.data.clips.filter((clip) => clip.editId !== null)
  if (edited.length === 0) {
    return (
      <EmptyState
        compact
        icon={Scissors}
        title="No edits yet"
        description="Choose Edit clip on a moment to start editing it. Your edits are saved automatically."
      />
    )
  }
  return (
    <ul aria-label="Edits" className="surface divide-y">
      {edited.map((clip) => (
        <li key={clip.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{clip.hook}</p>
            <p className="text-xs text-muted-foreground">
              {formatClock(clip.durationMs)} · Revision {clip.currentRevision ?? 1}
              {clip.exportCount === 0 ? '' : ` · ${clip.exportCount} exported`}
            </p>
          </div>
          <StatusBadge tone={clip.stage === 'exported' ? 'success' : 'accent'}>
            {clip.stage === 'exported' ? 'Exported' : 'In editing'}
          </StatusBadge>
          <Link
            href={`/dashboard/clips/${clip.id}`}
            className="text-sm font-medium text-muted-foreground hover:text-foreground"
          >
            Details
          </Link>
          <Link
            href={`/editor/${clip.editId}`}
            className="inline-flex h-9 items-center rounded-lg border bg-card px-3 text-sm font-medium hover:bg-secondary"
          >
            Continue editing
          </Link>
        </li>
      ))}
    </ul>
  )
}

/** The work this Project's jobs reported while this page has been open. */
function ProjectActivity({ jobs }: { jobs: ProjectJob[] }) {
  if (jobs.length === 0) {
    return (
      <EmptyState
        compact
        icon={History}
        title="No activity while this page has been open"
        description="Imports, transcription, analysis, and exports report here as they happen."
      />
    )
  }
  return (
    <ul aria-label="Activity" className="surface divide-y">
      {[...jobs].reverse().map((job) => (
        <li key={job.jobId} className="flex items-center justify-between gap-3 px-4 py-3">
          <div>
            <p className="text-sm font-medium">{JOB_KIND_LABELS[job.kind] ?? job.kind}</p>
            {job.errorCode === null ? null : (
              <p className="text-xs text-destructive">Reported as {job.errorCode}</p>
            )}
          </div>
          <StatusBadge
            tone={
              job.status === 'succeeded'
                ? 'success'
                : job.status === 'failed'
                  ? 'danger'
                  : job.status === 'canceled'
                    ? 'neutral'
                    : 'progress'
            }
          >
            {job.status === 'succeeded'
              ? 'Finished'
              : job.status === 'failed'
                ? 'Failed'
                : job.status === 'canceled'
                  ? 'Canceled'
                  : job.status === 'retrying'
                    ? `Retrying (attempt ${job.attempt})`
                    : 'Working'}
          </StatusBadge>
        </li>
      ))}
    </ul>
  )
}
