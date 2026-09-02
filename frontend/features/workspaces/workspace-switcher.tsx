'use client'

import { useId } from 'react'

import { useWorkspaceScope } from './workspace-context'

/** Move between the Workspaces this member belongs to, one at a time. */
export function WorkspaceSwitcher() {
  const { active, workspaces, select } = useWorkspaceScope()
  const fieldId = useId()

  return (
    <div className="flex items-center gap-2">
      <label htmlFor={fieldId} className="text-xs uppercase tracking-wide text-muted-foreground">
        Workspace
      </label>
      <select
        id={fieldId}
        value={active.id}
        onChange={(event) => select(event.target.value)}
        className="rounded-md border bg-background px-2 py-1 text-sm"
      >
        {workspaces.map((workspace) => (
          <option key={workspace.id} value={workspace.id}>
            {workspace.name}
          </option>
        ))}
      </select>
    </div>
  )
}
