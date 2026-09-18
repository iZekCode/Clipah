'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { TranscriptResponse } from '@/lib/api/generated/model'
import { transcriptApiV1ProjectsProjectIdTranscriptGet } from '@/lib/api/generated/studio/studio'

/** One Project's transcript; it never changes after analysis, so it is read once per visit. */
export function useTranscript(
  projectId: string,
  { enabled }: { enabled: boolean },
): UseQueryResult<TranscriptResponse, ApiError> {
  const { active } = useWorkspaceScope()
  return useQuery<TranscriptResponse, ApiError>({
    queryKey: ['/api/v1/projects/transcript', active.id, projectId],
    queryFn: ({ signal }) =>
      transcriptApiV1ProjectsProjectIdTranscriptGet(
        projectId,
        { workspace_id: active.id },
        { signal },
      ),
    enabled,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  })
}
