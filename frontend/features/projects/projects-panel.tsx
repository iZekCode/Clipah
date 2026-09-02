'use client'

import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useId, useState, type FormEvent } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { mayWriteProjects } from '@/features/workspaces/roles'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import {
  createApiV1ProjectsPost,
  deleteApiV1ProjectsProjectIdDelete,
  listCollectionApiV1ProjectsGet,
  renameApiV1ProjectsProjectIdPatch,
  restoreApiV1ProjectsProjectIdRestorePost,
} from '@/lib/api/generated/projects/projects'
import type { ProjectPageResponse, ProjectResponse } from '@/lib/api/generated/model'

import { projectStatusLabel } from './status-labels'

const PAGE_SIZE = 20

/** The query key one Workspace's Project list is cached under. */
export function projectsQueryKey(workspaceId: string) {
  return ['/api/v1/projects', workspaceId] as const
}

/**
 * List the Projects of the active Workspace, one page at a time.
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

  if (projects.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading projects…
      </p>
    )
  }
  if (projects.isError) {
    return <ErrorNotice error={projects.error} />
  }

  const listed = projects.data.pages.flatMap((page) => page.projects)

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
        {mayWrite ? <CreateProject /> : null}
      </div>

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
        <p className="text-sm text-muted-foreground">
          No projects yet. Import a video to start one.
        </p>
      ) : (
        <ul aria-label="Projects" className="divide-y rounded-lg border">
          {listed.map((project) => (
            <ProjectRow
              key={project.id}
              project={project}
              mayWrite={mayWrite}
              onDeleted={() => setDeleted((pending) => [...pending, project])}
            />
          ))}
        </ul>
      )}

      {projects.hasNextPage ? (
        <button
          type="button"
          onClick={() => void projects.fetchNextPage()}
          disabled={projects.isFetchingNextPage}
          className="rounded-md border px-3 py-2 text-sm"
        >
          Load more
        </button>
      ) : null}
    </section>
  )
}

/** One Project in the list, with the writing controls this member is allowed. */
function ProjectRow({
  project,
  mayWrite,
  onDeleted,
}: {
  project: ProjectResponse
  mayWrite: boolean
  onDeleted: () => void
}) {
  const { active } = useWorkspaceScope()
  const queryClient = useQueryClient()
  const [renaming, setRenaming] = useState(false)

  const remove = useMutation<void, ApiError>({
    mutationFn: () =>
      deleteApiV1ProjectsProjectIdDelete(project.id, { workspace_id: active.id }).then(() => undefined),
    onSuccess: () => {
      queryClient.setQueryData(projectsQueryKey(active.id), withoutProject(project.id))
      onDeleted()
    },
  })

  return (
    <li className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0">
        <Link href={`/dashboard/projects/${project.id}`} className="text-sm font-medium">
          {project.name}
        </Link>
        <p className="text-xs text-muted-foreground">{projectStatusLabel(project.status)}</p>
      </div>
      {mayWrite ? (
        <div className="flex shrink-0 items-center gap-2">
          {renaming ? (
            <RenameForm project={project} onDone={() => setRenaming(false)} />
          ) : (
            <button
              type="button"
              onClick={() => setRenaming(true)}
              className="rounded-md border px-2 py-1 text-xs"
            >
              Rename {project.name}
            </button>
          )}
          <button
            type="button"
            onClick={() => remove.mutate()}
            className="rounded-md border px-2 py-1 text-xs"
          >
            Delete {project.name}
          </button>
        </div>
      ) : null}
      {remove.isError ? <ErrorNotice error={remove.error} /> : null}
    </li>
  )
}

/** Rename a Project, showing the new name before the backend has answered. */
function RenameForm({ project, onDone }: { project: ProjectResponse; onDone: () => void }) {
  const { active } = useWorkspaceScope()
  const queryClient = useQueryClient()
  const [name, setName] = useState(project.name)
  const fieldId = useId()

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
    rename.mutate(name)
  }

  return (
    <form onSubmit={submit} className="flex items-center gap-2">
      <label htmlFor={fieldId} className="sr-only">
        Project name
      </label>
      <input
        id={fieldId}
        value={name}
        onChange={(event) => setName(event.target.value)}
        className="rounded-md border px-2 py-1 text-xs"
      />
      <button type="submit" className="rounded-md border px-2 py-1 text-xs">
        Save
      </button>
    </form>
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
    <div className="flex items-center justify-between rounded-md border px-4 py-2 text-sm">
      <p>{project.name} was deleted.</p>
      <button
        type="button"
        onClick={() => restore.mutate()}
        className="rounded-md border px-2 py-1 text-xs"
      >
        Restore {project.name}
      </button>
    </div>
  )
}

/** Start a new Project in the active Workspace. */
function CreateProject() {
  const { active } = useWorkspaceScope()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const fieldId = useId()

  const create = useMutation<ProjectResponse, ApiError, string>({
    mutationFn: (projectName) =>
      createApiV1ProjectsPost(
        { name: projectName, sourceKind: 'upload' },
        { workspace_id: active.id },
      ),
    onSuccess: () => {
      setOpen(false)
      setName('')
      void queryClient.invalidateQueries({ queryKey: projectsQueryKey(active.id) })
    },
  })

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"
      >
        Create project
      </button>
    )
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        create.mutate(name)
      }}
      className="flex items-center gap-2"
    >
      <label htmlFor={fieldId} className="sr-only">
        New project name
      </label>
      <input
        id={fieldId}
        value={name}
        onChange={(event) => setName(event.target.value)}
        className="rounded-md border px-2 py-1 text-sm"
      />
      <button type="submit" className="rounded-md border px-2 py-1 text-sm">
        Start project
      </button>
    </form>
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
