'use client'

import { useId } from 'react'

import { Select } from '@/components/ui/select'

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
      <Select
        id={fieldId}
        value={active.id}
        onChange={(event) => select(event.target.value)}
        controlSize="sm"
        wrapperClassName="max-w-44 sm:max-w-56"
        className="font-medium"
      >
        {workspaces.map((workspace) => (
          <option key={workspace.id} value={workspace.id}>
            {workspace.name}
          </option>
        ))}
      </Select>
    </div>
  )
}
