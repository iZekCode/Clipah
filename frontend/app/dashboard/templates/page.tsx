'use client'

import { RequireSession } from '@/features/auth/require-session'
import { TemplateLibrary } from '@/features/templates/TemplateLibrary'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

/** Templates: the reusable looks a Workspace applies to its clips. */
export default function TemplatesPage() {
  return (
    <RequireSession>
      <WorkspaceProvider>
        <TemplateLibrary />
      </WorkspaceProvider>
    </RequireSession>
  )
}
