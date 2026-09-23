'use client'

import { useQuery } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { useEffect } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { PageHeader } from '@/components/page-header'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { ClipDetailResponse } from '@/lib/api/generated/model'
import { showClipApiV1ClipsCandidateIdGet } from '@/lib/api/generated/studio/studio'

import { reviewHref } from './ClipCard'

/**
 * The address a search result or an older link gives a clip, forwarded to review mode.
 *
 * A clip link carries one UUID; the backend answers which Project it belongs to, and the
 * member lands on that moment in review mode with the section the link asked for.
 */
export function ClipRedirect({ candidateId, tab }: { candidateId: string; tab: string | null }) {
  const { active } = useWorkspaceScope()
  const router = useRouter()
  const detail = useQuery<ClipDetailResponse, ApiError>({
    queryKey: ['/api/v1/clips/detail', active.id, candidateId],
    queryFn: ({ signal }) =>
      showClipApiV1ClipsCandidateIdGet(candidateId, { workspace_id: active.id }, { signal }),
    retry: false,
  })

  const target =
    detail.data === undefined
      ? null
      : reviewHref({ id: detail.data.candidate.id, projectId: detail.data.project.id }) +
        (tab === null ? '' : `&tab=${encodeURIComponent(tab)}`)

  useEffect(() => {
    if (target !== null) router.replace(target)
  }, [router, target])

  if (detail.isError) {
    return (
      <div className="space-y-4">
        <PageHeader title="Clip" crumbs={[{ href: '/dashboard/projects', label: 'Projects' }]} />
        <ErrorNotice error={detail.error} />
      </div>
    )
  }
  return <LoadingState label="Opening clip…" variant="cards" count={1} />
}
