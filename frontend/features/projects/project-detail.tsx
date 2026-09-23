'use client'

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Clapperboard, History } from 'lucide-react'
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
import type { CandidateResponse, ProjectResponse } from '@/lib/api/generated/model'
import { showApiV1ProjectsProjectIdGet } from '@/lib/api/generated/projects/projects'

import { SourceColumn, useRequestedMomentMs } from './source-column'
import { projectIsProcessing, projectStatusLabel, projectStatusTone } from './status-labels'

const TABS = [
  { id: 'moments', label: 'Moments' },
  { id: 'exports', label: 'Exports' },
  { id: 'activity', label: 'Activity' },
] as const

type TabId = (typeof TABS)[number]['id']

/** How often a Project that is being processed is read again. */
const PROCESSING_REFRESH_MS = 10_000
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
    // Live updates refresh the Project as each stage ends; this catches anything the
    // stream missed, so the status never sits stale while work is going on.
    refetchInterval: (query) =>
      query.state.data !== undefined && projectIsProcessing(query.state.data.status)
        ? PROCESSING_REFRESH_MS
        : false,
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
  // A file upload that stopped part-way resumes when the member picks the file again, so a
  // Project still `uploading` from a file keeps offering the picker.
  const needsMedia =
    project.status === 'created' ||
    project.status === 'failed' ||
    (project.status === 'uploading' && project.sourceKind === 'upload')
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
            {projectStatusLabel(project.status, project.sourceKind)}
          </StatusBadge>
        }
        actions={
          needsMedia && mayWriteProjects(active.role) ? (
            <Button size="lg" onClick={() => document.getElementById(fileInputId)?.click()}>
              Add media
            </Button>
          ) : ready ? (
            <Button asChild size="lg">
              <Link href={`/dashboard/projects/${project.id}/review`}>Review moments</Link>
            </Button>
          ) : undefined
        }
      />

      {ready ? (
        <UploadPanel
          projectId={project.id}
          addMedia={false}
          onJob={onJob}
          projectStatus={project.status}
          sourceKind={project.sourceKind}
          onRetried={onChanged}
        />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
          <UploadPanel
            projectId={project.id}
            addMedia={needsMedia}
            onJob={onJob}
            fileInputId={fileInputId}
            projectStatus={project.status}
            sourceKind={project.sourceKind}
            onRetried={onChanged}
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
