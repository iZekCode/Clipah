'use client'

import { useQuery } from '@tanstack/react-query'
import { useCallback, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import type { BrollPlacement } from '@/features/editor/store'
import type { ApiError } from '@/lib/api/client'
import {
  createPlanApiV1ProjectsProjectIdCandidatesCandidateIdBrollPlansPost,
  createRetrievalApiV1ProjectsProjectIdCandidatesCandidateIdBrollRetrievalsPost,
  listCollectionApiV1ProjectsProjectIdCandidatesCandidateIdBrollSuggestionsGet,
} from '@/lib/api/generated/broll/broll'
import { indexApiV1ProjectsProjectIdAssetsGet } from '@/lib/api/generated/assets/assets'
import type {
  BrollCoverage,
  BrollSuggestionListResponse,
  BrollSuggestionResponse,
  ProjectAssetsResponse,
} from '@/lib/api/generated/model'

import { BrollSuggestionCard } from './BrollSuggestionCard'
import { CoverageControl } from './CoverageControl'

/** What a member decided about one suggestion, in the words the backend records. */
export type BrollAction = 'accept' | 'replace' | 'remove' | 'reject'

/** How the panel describes a beat once a member has agreed to it. */
export interface DecisionRequest {
  suggestion: BrollSuggestionResponse
  action: BrollAction
  placement?: BrollPlacement
  assetId?: string
}

/**
 * The B-roll copilot beside the editor: ask for suggestions, then decide on them.
 *
 * Nothing here edits the clip. The panel reads proposals, and hands one decision at a
 * time to the editor, which owns the document and the Revision that decision produces.
 */
export function BrollPanel({
  projectId,
  candidateId,
  workspaceId,
  clipStartMs,
  deciding,
  onDecide,
}: {
  projectId: string
  candidateId: string
  workspaceId: string
  clipStartMs: number
  deciding: boolean
  onDecide: (request: DecisionRequest) => void
}) {
  const [coverage, setCoverage] = useState<BrollCoverage>('balanced')
  const [asked, setAsked] = useState(false)
  const [working, setWorking] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const suggestions = useQuery<BrollSuggestionListResponse, ApiError>({
    queryKey: ['/api/v1/broll-suggestions', workspaceId, projectId, candidateId],
    queryFn: ({ signal }) =>
      listCollectionApiV1ProjectsProjectIdCandidatesCandidateIdBrollSuggestionsGet(
        projectId,
        candidateId,
        { workspace_id: workspaceId },
        { signal },
      ),
    retry: false,
  })

  // Replacing a picture means naming another asset this Project already holds, so the
  // panel offers the same library the assets panel does rather than a second search.
  const assets = useQuery<ProjectAssetsResponse, ApiError>({
    queryKey: ['/api/v1/projects/assets', workspaceId, projectId],
    queryFn: ({ signal }) =>
      indexApiV1ProjectsProjectIdAssetsGet(projectId, { workspace_id: workspaceId }, { signal }),
    retry: false,
  })

  const suggest = useCallback(async () => {
    setWorking(true)
    setRefusal(null)
    setFailure(null)
    // One key per clip and coverage, so a second press reaches the work already admitted
    // rather than buying a second plan and a second search of a provider's catalogue.
    const key = `broll:${candidateId}:${coverage}`
    try {
      await createPlanApiV1ProjectsProjectIdCandidatesCandidateIdBrollPlansPost(
        projectId,
        candidateId,
        { coverage },
        { workspace_id: workspaceId },
        { headers: { 'Idempotency-Key': `plan:${key}` } },
      )
      await createRetrievalApiV1ProjectsProjectIdCandidatesCandidateIdBrollRetrievalsPost(
        projectId,
        candidateId,
        { coverage },
        { workspace_id: workspaceId },
        { headers: { 'Idempotency-Key': `retrieve:${key}` } },
      )
      setAsked(true)
      await suggestions.refetch()
    } catch (error) {
      const refused = error as ApiError
      if (refused.code === 'QUOTA_EXCEEDED') {
        setRefusal(
          'This Workspace has spent its monthly stock allowance. B-roll can be searched for again next month.',
        )
      } else if (refused.code === 'CONCURRENCY_LIMIT') {
        setRefusal('This Workspace is already running as many jobs as it can. Try again shortly.')
      } else {
        setFailure(refused)
      }
    } finally {
      setWorking(false)
    }
  }, [candidateId, coverage, projectId, suggestions, workspaceId])

  const found = suggestions.data?.suggestions ?? []
  const busy = working || deciding
  // Absence is the backend's one answer for a clip nobody has planned yet and for a clip
  // this member has no standing on, and only the first is possible on a clip they already
  // have open. Every other failure still raises the alert carrying the request identifier.
  const unreadable = suggestions.isError && suggestions.error.status !== 404

  return (
    <section aria-label="B-roll" className="flex flex-col gap-3 rounded-lg border p-3">
      <header>
        <h2 className="text-sm font-semibold">B-roll</h2>
        <p className="text-xs text-muted-foreground">
          Suggested cutaways. Nothing is added to your clip until you accept it.
        </p>
      </header>

      <CoverageControl
        coverage={coverage}
        enabled={asked || found.length > 0}
        busy={busy}
        onCoverage={setCoverage}
        onSuggest={() => {
          void suggest()
        }}
      />

      {refusal === null ? null : (
        <p role="status" className="rounded border p-2 text-xs">
          {refusal}
        </p>
      )}
      {failure === null ? null : <ErrorNotice error={failure} />}
      {unreadable ? <ErrorNotice error={suggestions.error} /> : null}

      {suggestions.isPending ? (
        <p role="status" className="text-xs text-muted-foreground">
          Reading this clip&apos;s suggestions…
        </p>
      ) : null}

      {!suggestions.isPending && !unreadable && found.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No B-roll suggestions yet. Ask for some when this moment would be clearer with a picture.
        </p>
      ) : null}

      {found.map((suggestion) => (
        <BrollSuggestionCard
          key={suggestion.id}
          suggestion={suggestion}
          clipStartMs={clipStartMs}
          alternatives={assets.data?.assets ?? []}
          busy={busy}
          onAccept={() =>
            onDecide({ suggestion, action: 'accept', placement: placementOf(suggestion) })
          }
          onReject={() => onDecide({ suggestion, action: 'reject' })}
          onRemove={() => onDecide({ suggestion, action: 'remove' })}
          onReplace={(assetId) => onDecide({ suggestion, action: 'replace', assetId })}
        />
      ))}
    </section>
  )
}

/** Read one suggestion as the placement the planner chose for it. */
function placementOf(suggestion: BrollSuggestionResponse): BrollPlacement {
  return {
    suggestionId: suggestion.id,
    assetId: suggestion.assetId ?? '',
    // Only video is retrieved today; a still would carry an image content type, and the
    // task that generates one owns telling the editor which it produced.
    mediaKind: 'video',
    startMs: suggestion.startMs,
    endMs: suggestion.endMs,
  }
}
