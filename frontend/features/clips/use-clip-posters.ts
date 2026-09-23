'use client'

import { useQuery } from '@tanstack/react-query'

import { SIGNED_MEDIA_STALE_MS } from '@/features/media/use-storyboard'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { ClipPostersResponse } from '@/lib/api/generated/model'
import { postersApiV1ProjectsProjectIdPostersGet } from '@/lib/api/generated/studio/studio'

/** How often posters still being drawn are asked for again, and for how many tries. */
const DRAWING_REFRESH_MS = 10_000
const DRAWING_REFRESH_LIMIT = 30

/**
 * The sharp poster of each of a Project's moments, by candidate, once it has been drawn.
 *
 * Posters are drawn after analysis finishes, so a Project that was just analysed has fewer
 * posters than moments for a little while; the read repeats until every moment has one, or
 * gives up after five minutes and leaves the rest on their storyboard tile.
 */
export function useClipPosters(projectId: string, momentCount: number): Map<string, string> {
  const { active } = useWorkspaceScope()
  const posters = useQuery<ClipPostersResponse, ApiError>({
    queryKey: ['/api/v1/projects/posters', active.id, projectId],
    queryFn: ({ signal }) =>
      postersApiV1ProjectsProjectIdPostersGet(projectId, { workspace_id: active.id }, { signal }),
    enabled: momentCount > 0,
    retry: false,
    staleTime: SIGNED_MEDIA_STALE_MS,
    refetchInterval: (query) =>
      query.state.data !== undefined &&
      query.state.data.posters.length < momentCount &&
      query.state.dataUpdateCount < DRAWING_REFRESH_LIMIT
        ? DRAWING_REFRESH_MS
        : false,
  })
  return new Map((posters.data?.posters ?? []).map((poster) => [poster.candidateId, poster.url]))
}
