'use client'

import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { FolderOpen } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { Field, inputClassName } from '@/components/field'
import { LoadingState } from '@/components/loading-state'
import { MediaCard, ProjectThumbnail } from '@/components/media-card'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { ItemMenu } from '@/components/ui/item-menu'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { mayWriteProjects } from '@/features/workspaces/roles'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import {
  deleteApiV1ProjectsProjectIdDelete,
  listCollectionApiV1ProjectsGet,
  renameApiV1ProjectsProjectIdPatch,
  restoreApiV1ProjectsProjectIdRestorePost,
} from '@/lib/api/generated/projects/projects'
import type { ProjectPageResponse, ProjectResponse } from '@/lib/api/generated/model'

import { NewProjectButton } from './new-project'
import { projectsQueryKey } from './query-keys'
import { projectStatusLabel, projectStatusTone } from './status-labels'

const PAGE_SIZE = 24

export { projectsQueryKey }

/**
 * The Project library of the active Workspace, as a browsable grid of media.
 *
 * Pages are asked for by the cursor the backend handed back, so the browser never holds
 * a whole Workspace in memory and never invents an ordering of its own.
 */
export function ProjectsPanel() {
  const { active } = useWorkspaceScope()

  return <ProjectsList key={active.id} />
}

function ProjectsList() {
  const { active } = useWorkspaceScope()
  const queryClient = useQueryClient()
  const [deleted, setDeleted] = useState<ProjectResponse[]>([])
  const [renaming, setRenaming] = useState<ProjectResponse | null>(null)
  const mayWrite = mayWriteProjects(active.role)

  const projects = useInfiniteQuery<ProjectPageResponse, ApiError>({
    queryKey: projectsQueryKey(active.id),
    queryFn: ({ pageParam, signal }) =>
      listCollectionApiV1ProjectsGet(
        {
          workspace_id: active.id,
          limit: PAGE_SIZE,
          ...(typeof pageParam === 'string' ? { cursor: pageParam } : {}),
        },
        { signal },
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor,
    retry: false,
  })

  const header = (
    <PageHeader
      title="Projects"
      description="Each project holds one video and everything made from it."
      actions={mayWrite ? <NewProjectButton /> : undefined}
    />
  )

  if (projects.isPending) {
    return (
      <section>
        {header}
        <LoadingState label="Loading projects…" variant="cards" count={6} />
      </section>
    )
  }
  if (projects.isError) {
    return (
      <section>
        {header}
        <ErrorNotice error={projects.error} onRetry={() => void projects.refetch()} />
      </section>
    )
  }

  const listed = projects.data.pages.flatMap((page) => page.projects)

  return (
    <section className="space-y-6">
      {header}

      {deleted.map((project) => (
        <RestoreNotice
          key={project.id}
          project={project}
          onRestored={() => {
            setDeleted((pending) => pending.filter((entry) => entry.id !== project.id))
            void queryClient.invalidateQueries({ queryKey: projectsQueryKey(active.id) })
          }}
        />
      ))}

      {listed.length === 0 ? (
        <EmptyState
          icon={FolderOpen}
          title="No projects yet"
          description="Import a video to start one. Clipah will transcribe it and suggest the best moments."
          action={mayWrite ? <NewProjectButton /> : undefined}
        />
      ) : (
        <ul aria-label="Projects" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {listed.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              mayWrite={mayWrite}
              onRename={() => setRenaming(project)}
              onDeleted={() => setDeleted((pending) => [...pending, project])}
            />
          ))}
        </ul>
      )}

      {projects.hasNextPage ? (
        <div className="flex justify-center">
          <Button
            type="button"
            variant="outline"
            onClick={() => void projects.fetchNextPage()}
            disabled={projects.isFetchingNextPage}
          >
            {projects.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </Button>
        </div>
      ) : null}

      {renaming === null ? null : (
        <RenameDialog project={renaming} onDone={() => setRenaming(null)} />
      )}
    </section>
  )
}

/** One Project in the grid, with the contextual actions this member is allowed. */
function ProjectCard({
  project,
  mayWrite,
  onRename,
  onDeleted,
}: {
  project: ProjectResponse
  mayWrite: boolean
  onRename: () => void
  onDeleted: () => void
}) {
  const { active } = useWorkspaceScope()
  const queryClient = useQueryClient()

  const remove = useMutation<void, ApiError>({
    mutationFn: () =>
      deleteApiV1ProjectsProjectIdDelete(project.id, { workspace_id: active.id }).then(() => undefined),
    onSuccess: () => {
      queryClient.setQueryData(projectsQueryKey(active.id), withoutProject(project.id))
      onDeleted()
    },
  })

  return (
    <li className="space-y-2">
      <MediaCard
        href={`/dashboard/projects/${project.id}`}
        title={project.name}
        thumbnail={<ProjectThumbnail
                    workspaceId={active.id}
                    projectId={project.id}
                    hasMedia={project.status !== 'created' && project.status !== 'uploading'}
                  />}
        status={
          <StatusBadge tone={projectStatusTone(project.status)}>
            {projectStatusLabel(project.status)}
          </StatusBadge>
        }
        subtitle={`Created ${formatDate(project.createdAt)}`}
        menu={
          mayWrite ? (
            <ItemMenu
              label={`Actions for ${project.name}`}
              actions={[
                { label: `Rename ${project.name}`, onSelect: onRename },
                {
                  label: `Delete ${project.name}`,
                  onSelect: () => remove.mutate(),
                  destructive: true,
                  disabled: remove.isPending,
                },
              ]}
            />
          ) : undefined
        }
      />
      {remove.isError ? <ErrorNotice error={remove.error} /> : null}
    </li>
  )
}

/** Rename a Project, showing the new name before the backend has answered. */
function RenameDialog({ project, onDone }: { project: ProjectResponse; onDone: () => void }) {
  const { active } = useWorkspaceScope()
  const queryClient = useQueryClient()
  const [name, setName] = useState(project.name)

  const rename = useMutation<ProjectResponse, ApiError, string>({
    mutationFn: (nextName) =>
      renameApiV1ProjectsProjectIdPatch(project.id, { name: nextName }, { workspace_id: active.id }),
    onMutate: (nextName) => {
      queryClient.setQueryData(projectsQueryKey(active.id), withRenamedProject(project.id, nextName))
      onDone()
    },
    onSuccess: (renamed) => {
      queryClient.setQueryData(projectsQueryKey(active.id), withRenamedProject(renamed.id, renamed.name))
    },
    onError: () => {
      queryClient.setQueryData(projectsQueryKey(active.id), withRenamedProject(project.id, project.name))
    },
  })

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (name.trim() !== '') {
      rename.mutate(name.trim())
    }
  }

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onDone())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rename project</DialogTitle>
          <DialogDescription>The new name is used everywhere this project appears.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Project name">
            <input
              value={name}
              maxLength={200}
              onChange={(event) => setName(event.target.value)}
              className={inputClassName}
            />
          </Field>
          <DialogFooter className="gap-2">
            <Button type="button" variant="ghost" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit">Save</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/** Offer the recovery window the backend keeps a soft-deleted Project inside. */
function RestoreNotice({
  project,
  onRestored,
}: {
  project: ProjectResponse
  onRestored: () => void
}) {
  const { active } = useWorkspaceScope()
  const restore = useMutation<ProjectResponse, ApiError>({
    mutationFn: () =>
      restoreApiV1ProjectsProjectIdRestorePost(project.id, { workspace_id: active.id }),
    onSuccess: onRestored,
  })

  return (
    <div
      role="status"
      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3 text-sm shadow-sm"
    >
      <p>
        <span className="font-medium">{project.name}</span> was deleted. You can restore it
        for 30 days.
      </p>
      <Button type="button" size="sm" variant="outline" onClick={() => restore.mutate()}>
        Restore {project.name}
      </Button>
      {restore.isError ? <ErrorNotice error={restore.error} /> : null}
    </div>
  )
}

type ProjectPages = { pages: ProjectPageResponse[]; pageParams: unknown[] } | undefined

/** Drop one Project from every cached page without refetching the list. */
function withoutProject(projectId: string) {
  return (cached: ProjectPages): ProjectPages =>
    cached === undefined
      ? cached
      : {
          ...cached,
          pages: cached.pages.map((page) => ({
            ...page,
            projects: page.projects.filter((project) => project.id !== projectId),
          })),
        }
}

/** Show a renamed Project immediately, before the backend has confirmed it. */
function withRenamedProject(projectId: string, name: string) {
  return (cached: ProjectPages): ProjectPages =>
    cached === undefined
      ? cached
      : {
          ...cached,
          pages: cached.pages.map((page) => ({
            ...page,
            projects: page.projects.map((project) =>
              project.id === projectId ? { ...project, name } : project,
            ),
          })),
        }
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}
