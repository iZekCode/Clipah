/** The query key one Workspace's Project list is cached under. */
export function projectsQueryKey(workspaceId: string) {
  return ['/api/v1/projects', workspaceId] as const
}
