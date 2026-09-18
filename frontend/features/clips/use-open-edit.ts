'use client'

import { useMutation } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { createApiV1ProjectsProjectIdCandidatesCandidateIdEditsPost } from '@/lib/api/generated/edits/edits'
import type { CandidateResponse, EditResponse } from '@/lib/api/generated/model'

type Look = { templateId: string | null; brandKitId: string | null }

/**
 * Open one candidate in the editor.
 *
 * One clip has one Edit: the backend converges a repeated request on the Edit it already
 * created, so a second press opens the same work rather than a rival copy. The look and the
 * brand are chosen once, here, and the Revision records the exact versions it was built from.
 */
export function useOpenEdit(candidate: Pick<CandidateResponse, 'id' | 'projectId'>) {
  const { active } = useWorkspaceScope()
  const router = useRouter()
  const mutation = useMutation<EditResponse, ApiError, Look>({
    mutationFn: (look) =>
      createApiV1ProjectsProjectIdCandidatesCandidateIdEditsPost(
        candidate.projectId,
        candidate.id,
        look,
        { workspace_id: active.id },
      ),
    onSuccess: (created) => router.push(`/editor/${created.id}?workspace_id=${active.id}`),
  })
  return {
    open: (look: Look = { templateId: null, brandKitId: null }) => mutation.mutate(look),
    isPending: mutation.isPending,
    error: mutation.error,
  }
}
