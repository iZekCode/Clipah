'use client'

import { useQuery } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import type { ApiError } from '@/lib/api/client'
import { indexApiV1WorkspacesGet } from '@/lib/api/generated/workspaces/workspaces'
import type { WorkspaceCollectionResponse, WorkspaceResponse } from '@/lib/api/generated/model'

const SELECTED_WORKSPACE_KEY = 'clipah.workspace'

export const WORKSPACES_QUERY_KEY = ['/api/v1/workspaces'] as const

/** The Workspace every read and write on the page is scoped to. */
export interface WorkspaceScope {
  active: WorkspaceResponse
  workspaces: WorkspaceResponse[]
  select: (workspaceId: string) => void
}

const WorkspaceScopeContext = createContext<WorkspaceScope | null>(null)

/**
 * Hold the Workspace the member is looking at, and the memberships they may switch to.
 *
 * The list is authoritative and comes from the backend on every visit; the browser only
 * remembers which of those memberships was last opened, and that preference is ignored
 * when it no longer names a Workspace the member belongs to.
 */
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [selected, setSelected] = useState<string | null>(() => rememberedWorkspaceId())
  const memberships = useQuery<WorkspaceCollectionResponse, ApiError>({
    queryKey: WORKSPACES_QUERY_KEY,
    queryFn: ({ signal }) => indexApiV1WorkspacesGet({ signal }),
    retry: false,
  })

  const select = useCallback((workspaceId: string) => {
    setSelected(workspaceId)
    rememberWorkspaceId(workspaceId)
  }, [])

  const workspaces = useMemo(() => memberships.data?.workspaces ?? [], [memberships.data])
  const active = useMemo(
    () => workspaces.find((workspace) => workspace.id === selected) ?? defaultWorkspace(workspaces),
    [workspaces, selected],
  )
  const scope = useMemo(
    () => (active === undefined ? null : { active, workspaces, select }),
    [active, workspaces, select],
  )

  if (memberships.isPending) {
    return (
      <p role="status" className="p-6 text-sm text-muted-foreground">
        Loading your workspaces…
      </p>
    )
  }
  if (memberships.isError) {
    return <ErrorNotice error={memberships.error} />
  }
  if (scope === null) {
    return (
      <p className="p-6 text-sm text-muted-foreground">
        You do not belong to a workspace yet.
      </p>
    )
  }

  return <WorkspaceScopeContext.Provider value={scope}>{children}</WorkspaceScopeContext.Provider>
}

/** Read the active Workspace. Only components rendered inside the provider may ask. */
export function useWorkspaceScope(): WorkspaceScope {
  const scope = useContext(WorkspaceScopeContext)
  if (scope === null) {
    throw new Error('useWorkspaceScope must be used inside a WorkspaceProvider')
  }
  return scope
}

/** A freshly bootstrapped User lands in their own Workspace, not in someone else's. */
function defaultWorkspace(workspaces: WorkspaceResponse[]): WorkspaceResponse | undefined {
  return workspaces.find((workspace) => workspace.kind === 'personal') ?? workspaces[0]
}

function rememberedWorkspaceId(): string | null {
  if (typeof window === 'undefined') {
    return null
  }
  return window.sessionStorage.getItem(SELECTED_WORKSPACE_KEY)
}

function rememberWorkspaceId(workspaceId: string): void {
  if (typeof window !== 'undefined') {
    window.sessionStorage.setItem(SELECTED_WORKSPACE_KEY, workspaceId)
  }
}
