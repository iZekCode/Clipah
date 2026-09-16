'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Clapperboard, Search } from 'lucide-react'
import Link from 'next/link'
import { useId, useState } from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { selectClassName } from '@/components/field'
import { LoadingState } from '@/components/loading-state'
import { MediaCard, ProjectThumbnail } from '@/components/media-card'
import { PageHeader } from '@/components/page-header'
import { StatusBadge, type StatusTone } from '@/components/status-badge'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type {
  ClipPageResponse,
  ClipStage,
  ClipSummaryResponse,
  ProjectPageResponse,
} from '@/lib/api/generated/model'
import { listCollectionApiV1ProjectsGet } from '@/lib/api/generated/projects/projects'
import { browseClipCollectionApiV1ClipsGet } from '@/lib/api/generated/studio/studio'

import { formatDuration } from './ClipCard'

const PAGE_SIZE = 24

const FILTERS: { id: ClipStage | 'all'; label: string }[] = [
  { id: 'all', label: 'All clips' },
  { id: 'suggested', label: 'Suggested' },
  { id: 'edited', label: 'In editing' },
  { id: 'exported', label: 'Exported' },
]

const STAGE_BADGES: Record<ClipStage, { label: string; tone: StatusTone }> = {
  suggested: { label: 'Suggested', tone: 'neutral' },
  edited: { label: 'In editing', tone: 'accent' },
  exported: { label: 'Exported', tone: 'success' },
}

/**
 * Every clip in the Workspace, browsable before anything is typed.
 *
 * A clip is one moment at some point on its way to a published file, so the browser
 * separates what was only suggested from what has been edited and what has already been
 * exported. Searching by the words spoken is one step away rather than the only way in.
 */
export function ClipBrowser() {
  const { active } = useWorkspaceScope()
  const [stage, setStage] = useState<ClipStage | 'all'>('all')
  const [projectId, setProjectId] = useState('')
  const projectFieldId = useId()
  const searchFieldId = useId()

  const clips = useInfiniteQuery<ClipPageResponse, ApiError>({
    queryKey: ['/api/v1/clips', active.id, stage, projectId],
    queryFn: ({ pageParam, signal }) =>
      browseClipCollectionApiV1ClipsGet(
        {
          workspace_id: active.id,
          limit: PAGE_SIZE,
          ...(stage === 'all' ? {} : { stage }),
          ...(projectId === '' ? {} : { projectId }),
          ...(typeof pageParam === 'string' ? { cursor: pageParam } : {}),
        },
        { signal },
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor,
    retry: false,
  })
  const projects = useQuery<ProjectPageResponse, ApiError>({
    queryKey: ['/api/v1/projects', active.id, 'filter'],
    queryFn: ({ signal }) =>
      listCollectionApiV1ProjectsGet({ workspace_id: active.id, limit: 100 }, { signal }),
    retry: false,
  })

  const listed = clips.data?.pages.flatMap((page) => page.clips) ?? []

  return (
    <section className="space-y-6">
      <PageHeader
        title="Clips"
        description="Every moment from your projects — suggested, being edited, or ready to share."
      />

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div role="group" aria-label="Show clips" className="flex flex-wrap gap-1 rounded-lg bg-secondary p-1">
          {FILTERS.map((filter) => (
            <button
              key={filter.id}
              type="button"
              aria-pressed={stage === filter.id}
              onClick={() => setStage(filter.id)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                stage === filter.id ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {filter.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={projectFieldId} className="sr-only">
            Project
          </label>
          <select
            id={projectFieldId}
            value={projectId}
            onChange={(event) => setProjectId(event.target.value)}
            className={selectClassName}
          >
            <option value="">All projects</option>
            {(projects.data?.projects ?? []).map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
          <form role="search" action="/dashboard/search" method="get" className="relative">
            <input type="hidden" name="type" value="clip" />
            <label htmlFor={searchFieldId} className="sr-only">
              Search clips by what was said
            </label>
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            />
            <input
              id={searchFieldId}
              type="search"
              name="q"
              placeholder="Search by what was said"
              className="h-9 w-56 rounded-lg border border-input bg-card pl-9 pr-3 text-sm shadow-sm"
            />
          </form>
        </div>
      </div>

      {clips.isPending ? (
        <LoadingState label="Loading clips…" variant="cards" count={6} />
      ) : clips.isError ? (
        <ErrorNotice error={clips.error} onRetry={() => void clips.refetch()} />
      ) : listed.length === 0 ? (
        <EmptyState
          icon={Clapperboard}
          title={stage === 'all' && projectId === '' ? 'No clips yet' : 'No clips match these filters'}
          description={
            stage === 'all' && projectId === ''
              ? 'Clips appear here once a project has been processed and its moments suggested.'
              : 'Try another filter, or choose All clips.'
          }
          action={
            stage === 'all' && projectId === '' ? (
              <Link href="/dashboard/projects" className="text-sm font-medium text-primary hover:underline">
                Go to projects
              </Link>
            ) : undefined
          }
        />
      ) : (
        <ul aria-label="Clips" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {listed.map((clip) => (
            <li key={clip.id}>
              <ClipTile clip={clip} workspaceId={active.id} />
            </li>
          ))}
        </ul>
      )}

      {clips.hasNextPage ? (
        <div className="flex justify-center">
          <button
            type="button"
            onClick={() => void clips.fetchNextPage()}
            disabled={clips.isFetchingNextPage}
            className="h-10 rounded-lg border bg-card px-4 text-sm font-medium hover:bg-secondary disabled:opacity-50"
          >
            {clips.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </button>
        </div>
      ) : null}
    </section>
  )
}

function ClipTile({ clip, workspaceId }: { clip: ClipSummaryResponse; workspaceId: string }) {
  const badge = STAGE_BADGES[clip.stage]
  return (
    <MediaCard
      href={`/dashboard/clips/${clip.id}`}
      title={clip.hook}
      thumbnail={<ProjectThumbnail workspaceId={workspaceId} projectId={clip.projectId} />}
      status={<StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>}
      subtitle={
        <span>
          {clip.projectName} · {formatDuration(clip.durationMs)}
          {clip.exportCount > 0 ? ` · ${clip.exportCount} ${clip.exportCount === 1 ? 'export' : 'exports'}` : ''}
        </span>
      }
      footer={
        clip.editId === null ? null : (
          <Link
            href={`/editor/${clip.editId}`}
            className="relative z-10 text-xs font-medium text-primary hover:underline"
          >
            Continue editing
          </Link>
        )
      }
    />
  )
}
