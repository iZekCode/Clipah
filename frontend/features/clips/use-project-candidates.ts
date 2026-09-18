'use client'

import {
  useInfiniteQuery,
  type InfiniteData,
  type UseInfiniteQueryResult,
} from '@tanstack/react-query'
import { useEffect, useMemo } from 'react'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { listCollectionApiV1ProjectsProjectIdCandidatesGet } from '@/lib/api/generated/candidates/candidates'
import type { CandidatePageResponse, CandidateResponse } from '@/lib/api/generated/model'

const PAGE_SIZE = 100

/**
 * The whole exposed candidate set of one Project.
 *
 * Every page is read before `complete` turns true, because sorting or filtering half a list
 * would be an order the browser invented rather than the one the analysis decided. The set
 * is small by design — the ranking policy exposes a bounded number of candidates — and the
 * Project page, its moments list, and review mode share this one cache entry.
 */
export function useProjectCandidates(
  projectId: string,
  { enabled = true }: { enabled?: boolean } = {},
): {
  candidates: CandidateResponse[]
  complete: boolean
  query: UseInfiniteQueryResult<InfiniteData<CandidatePageResponse>, ApiError>
} {
  const { active } = useWorkspaceScope()
  const query = useInfiniteQuery<CandidatePageResponse, ApiError>({
    queryKey: ['/api/v1/projects/candidates', active.id, projectId],
    queryFn: ({ pageParam, signal }) =>
      listCollectionApiV1ProjectsProjectIdCandidatesGet(
        projectId,
        {
          workspace_id: active.id,
          limit: PAGE_SIZE,
          ...(typeof pageParam === 'string' ? { cursor: pageParam } : {}),
        },
        { signal },
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor,
    enabled,
    retry: false,
  })
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage) {
      void fetchNextPage()
    }
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])
  const candidates = useMemo(
    () => query.data?.pages.flatMap((page) => page.candidates) ?? [],
    [query.data],
  )
  return { candidates, complete: query.isSuccess && !hasNextPage, query }
}
