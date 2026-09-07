'use client'

import { RequireSession } from '@/features/auth/require-session'
import { BrandKitEditor } from '@/features/brand-kits/BrandKitEditor'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

/** Brand kits: colours, type, safe areas, and the claims a Workspace may not make. */
export default function BrandKitsPage() {
  return (
    <RequireSession>
      <WorkspaceProvider>
        <BrandKitEditor />
      </WorkspaceProvider>
    </RequireSession>
  )
}
