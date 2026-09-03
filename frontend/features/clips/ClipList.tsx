'use client'

import { useInfiniteQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { listCollectionApiV1ProjectsProjectIdCandidatesGet } from '@/lib/api/generated/candidates/candidates'
import { ClipCategory, type CandidatePageResponse, type CandidateResponse } from '@/lib/api/generated/model'

import { ClipCard } from './ClipCard'

const PAGE_SIZE = 100

/** The orders a reviewer may put the exposed clips in. */
const SORTS = {
  rank: 'Ranked by the analysis',
  score: 'Highest score first',
  longest: 'Longest first',
  shortest: 'Shortest first',
} as const

type SortKey = keyof typeof SORTS

/** The lengths a reviewer typically publishes within. */
const LENGTHS = [
  ['any', 'Any length'],
  ['30000', 'Up to 30 seconds'],
  ['60000', 'Up to 1 minute'],
  ['90000', 'Up to 90 seconds'],
] as const

/** What each analysis category is called in the filter. */
const CATEGORY_LABELS: Record<string, string> = {
  story: 'Story',
  insight: 'Insight',
  how_to: 'How-to',
  opinion: 'Opinion',
  question_answer: 'Question and answer',
  humour: 'Humour',
  data: 'Data',
  announcement: 'Announcement',
}

/**
 * Review the moments the analysis proposed, before anything has been rendered.
 *
 * The whole exposed set is read before any control is offered, because sorting and
 * filtering half a list would be an order the browser invented rather than the one the
 * analysis decided. The set is small by design — the ranking policy exposes a bounded
 * number of candidates — so reading it whole costs one or two requests.
 */
export function ClipList({ projectId }: { projectId: string }) {
  const { active } = useWorkspaceScope()
  const [sort, setSort] = useState<SortKey>('rank')
  const [category, setCategory] = useState<string>('all')
  const [maxDurationMs, setMaxDurationMs] = useState<string>('any')

  const clips = useInfiniteQuery<CandidatePageResponse, ApiError>({
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
    retry: false,
  })

  const { hasNextPage, isFetchingNextPage, fetchNextPage } = clips
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage) {
      void fetchNextPage()
    }
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  const listed = useMemo(
    () => clips.data?.pages.flatMap((page) => page.candidates) ?? [],
    [clips.data],
  )
  const shown = useMemo(
    () => arrange(listed, { sort, category, maxDurationMs }),
    [listed, sort, category, maxDurationMs],
  )

  if (clips.isPending || hasNextPage) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading clips…
      </p>
    )
  }
  if (clips.isError) {
    // Absence is the backend's one answer for a Project that is still being analysed, one
    // that produced nothing, and one the caller has no standing on. Only the first is
    // possible on a Project the member is already looking at, so say that rather than
    // raising an alarm about a Project that is simply not finished.
    if (clips.error.status === 404) {
      return <p className="text-sm text-muted-foreground">No clips to review yet.</p>
    }
    return <ErrorNotice error={clips.error} />
  }
  if (listed.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No clips yet. The analysis found no moment it could stand behind.
      </p>
    )
  }

  return (
    <section aria-label="Clips" className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold tracking-tight">Clips</h2>
        <label className="text-xs text-muted-foreground">
          <span className="sr-only">Sort clips</span>
          <select
            aria-label="Sort clips"
            value={sort}
            onChange={(event) => setSort(event.target.value as SortKey)}
            className="rounded-md border px-2 py-1 text-sm"
          >
            {Object.entries(SORTS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <select
          aria-label="Filter by category"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          className="rounded-md border px-2 py-1 text-sm"
        >
          <option value="all">Every category</option>
          {Object.values(ClipCategory).map((value) => (
            <option key={value} value={value}>
              {CATEGORY_LABELS[value] ?? value}
            </option>
          ))}
        </select>
        <select
          aria-label="Maximum length"
          value={maxDurationMs}
          onChange={(event) => setMaxDurationMs(event.target.value)}
          className="rounded-md border px-2 py-1 text-sm"
        >
          {LENGTHS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">No clips match those filters.</p>
      ) : (
        <ul aria-label="Ranked clips" className="divide-y rounded-lg border">
          {shown.map((clip) => (
            <ClipCard key={clip.id} candidate={clip} />
          ))}
        </ul>
      )}
    </section>
  )
}

/** Apply the reviewer's filters, then their order, without mutating what was read. */
function arrange(
  candidates: CandidateResponse[],
  { sort, category, maxDurationMs }: { sort: SortKey; category: string; maxDurationMs: string },
): CandidateResponse[] {
  const limit = maxDurationMs === 'any' ? null : Number(maxDurationMs)
  const kept = candidates.filter(
    (clip) =>
      (category === 'all' || clip.category === category) &&
      (limit === null || clip.durationMs <= limit),
  )
  if (sort === 'rank') {
    return [...kept].sort((left, right) => left.rank - right.rank)
  }
  if (sort === 'score') {
    return [...kept].sort((left, right) => right.score - left.score)
  }
  if (sort === 'longest') {
    return [...kept].sort((left, right) => right.durationMs - left.durationMs)
  }
  return [...kept].sort((left, right) => left.durationMs - right.durationMs)
}
