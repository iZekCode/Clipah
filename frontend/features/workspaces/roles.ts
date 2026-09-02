import type { WorkspaceRole } from '@/lib/api/generated/model'

/** The roles the backend lets write inside a Workspace. */
const WRITING_ROLES: readonly WorkspaceRole[] = ['owner', 'admin', 'editor']

/**
 * Whether this member may change Projects.
 *
 * Hiding a control is a courtesy, not a boundary: the backend refuses the same writes
 * for the same roles, so a member who reaches the endpoint another way is still stopped.
 */
export function mayWriteProjects(role: WorkspaceRole): boolean {
  return WRITING_ROLES.includes(role)
}
