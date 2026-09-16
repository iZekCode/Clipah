'use client'

import { ChevronsUpDown } from 'lucide-react'
import { useId } from 'react'

import { useWorkspaceScope } from './workspace-context'

/** Move between the Workspaces this member belongs to, one at a time. */
export function WorkspaceSwitcher() {
  const { active, workspaces, select } = useWorkspaceScope()
  const fieldId = useId()

  return (
    <div className="relative flex items-center">
      <label htmlFor={fieldId} className="sr-only">
        Workspace
      </label>
      <select
        id={fieldId}
        value={active.id}
        onChange={(event) => select(event.target.value)}
        className="h-9 max-w-44 appearance-none truncate rounded-lg border bg-card py-1 pl-3 pr-8 text-sm font-medium shadow-sm hover:bg-secondary sm:max-w-56"
      >
        {workspaces.map((workspace) => (
          <option key={workspace.id} value={workspace.id}>
            {workspace.name}
          </option>
        ))}
      </select>
      <ChevronsUpDown
        aria-hidden="true"
        className="pointer-events-none absolute right-2.5 size-3.5 text-muted-foreground"
      />
    </div>
  )
}
