'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { StoryboardResponse } from '@/lib/api/generated/model'
import { storyboardApiV1ProjectsProjectIdStoryboardGet } from '@/lib/api/generated/studio/studio'

/** Signed URLs live five minutes; a manifest is re-signed after four. */
export const SIGNED_MEDIA_STALE_MS = 4 * 60_000

/** One Project's storyboard manifest, asked for only when a picture is about to be drawn. */
export function useStoryboard(
  projectId: string,
  { enabled }: { enabled: boolean },
): UseQueryResult<StoryboardResponse, ApiError> {
  const { active } = useWorkspaceScope()
  return useQuery<StoryboardResponse, ApiError>({
    queryKey: ['/api/v1/projects/storyboard', active.id, projectId],
    queryFn: ({ signal }) =>
      storyboardApiV1ProjectsProjectIdStoryboardGet(
        projectId,
        { workspace_id: active.id },
        { signal },
      ),
    enabled,
    retry: false,
    staleTime: SIGNED_MEDIA_STALE_MS,
  })
}
