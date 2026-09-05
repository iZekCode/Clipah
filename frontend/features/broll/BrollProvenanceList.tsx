'use client'

import { useQuery } from '@tanstack/react-query'

import { ErrorNotice } from '@/components/error-notice'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { listCollectionApiV1ProjectsProjectIdCandidatesCandidateIdBrollSuggestionsGet } from '@/lib/api/generated/broll/broll'
import type { BrollSuggestionListResponse } from '@/lib/api/generated/model'

import { ProvenancePopover } from './ProvenancePopover'

/** The statuses in which a picture is actually part of the finished clip. */
const IN_THE_CLIP = new Set(['placed', 'replaced'])

/**
 * Every picture this clip carries, and the licence that traces each one.
 *
 * The editor answers "should I accept this?"; this answers "what am I publishing?" —
 * which is the question somebody asks months later, usually about a clip they no longer
 * remember editing. Only pictures actually in the clip appear: a proposal a member
 * refused licenses nothing.
 */
export function BrollProvenanceList({
  projectId,
  candidateId,
}: {
  projectId: string
  candidateId: string
}) {
  const { active } = useWorkspaceScope()
  const suggestions = useQuery<BrollSuggestionListResponse, ApiError>({
    queryKey: ['/api/v1/broll-suggestions', active.id, projectId, candidateId],
    queryFn: ({ signal }) =>
      listCollectionApiV1ProjectsProjectIdCandidatesCandidateIdBrollSuggestionsGet(
        projectId,
        candidateId,
        { workspace_id: active.id },
        { signal },
      ),
    retry: false,
  })

  if (suggestions.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Reading this clip&apos;s B-roll…
      </p>
    )
  }
  if (suggestions.isError && suggestions.error.status !== 404) {
    return <ErrorNotice error={suggestions.error} />
  }

  const used = (suggestions.data?.suggestions ?? []).filter(
    (suggestion) => IN_THE_CLIP.has(suggestion.status) && suggestion.provenance != null,
  )
  if (used.length === 0) {
    return <p className="text-sm text-muted-foreground">No B-roll in this clip.</p>
  }

  return (
    <div className="flex flex-col gap-2">
      {used.map((suggestion) => (
        <article
          key={suggestion.id}
          aria-label={suggestion.visualIntent.subject}
          className="flex flex-col gap-1 rounded-lg border p-3 text-xs"
        >
          <p className="font-medium">{suggestion.visualIntent.subject}</p>
          <p className="text-muted-foreground">On this clip since it was accepted.</p>
          {suggestion.provenance?.generated === true ? (
            <p className="w-fit rounded bg-muted px-2 py-0.5 font-medium">AI-generated</p>
          ) : null}
          <ProvenancePopover provenance={suggestion.provenance} />
        </article>
      ))}
    </div>
  )
}
