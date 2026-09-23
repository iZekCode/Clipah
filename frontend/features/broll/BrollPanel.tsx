'use client'

import { useQuery } from '@tanstack/react-query'
import { useCallback, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import type { BrollPlacement } from '@/features/editor/store'
import type { ApiError } from '@/lib/api/client'
import {
  createGenerationApiV1BrollSuggestionsSuggestionIdGeneratePost,
  createGenerationEstimateApiV1BrollSuggestionsSuggestionIdGenerationEstimatesPost,
  createPlanApiV1ProjectsProjectIdCandidatesCandidateIdBrollPlansPost,
  listCollectionApiV1ProjectsProjectIdCandidatesCandidateIdBrollSuggestionsGet,
} from '@/lib/api/generated/broll/broll'
import { indexApiV1ProjectsProjectIdAssetsGet } from '@/lib/api/generated/assets/assets'
import type {
  BrollCoverage,
  BrollSuggestionListResponse,
  BrollSuggestionResponse,
  GenerationMediaKind,
  GenerationOfferResponse,
  ProjectAssetsResponse,
} from '@/lib/api/generated/model'

import { BrollSuggestionCard } from './BrollSuggestionCard'
import { CoverageControl } from './CoverageControl'
import { GenerationConfirmDialog } from './GenerationConfirmDialog'

/** The relevance at or above which a retrieved picture answers the beat well enough. */
const SUFFICIENT_RELEVANCE = 0.5

/** What a member decided about one suggestion, in the words the backend records. */
export type BrollAction = 'accept' | 'replace' | 'remove' | 'reject'

/** How the panel describes a beat once a member has agreed to it. */
export interface DecisionRequest {
  suggestion: BrollSuggestionResponse
  action: BrollAction
  placement?: BrollPlacement
  assetId?: string
}

/** How often the panel looks again while ideas or pictures are still being found. */
const SEARCH_POLL_MS = 2_000

/** A fresh identity for one press of the button. */
function newRequestId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`
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
  const [working, setWorking] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [generating, setGenerating] = useState<string | null>(null)
  const [offer, setOffer] = useState<GenerationOfferResponse | null>(null)
  const [pricing, setPricing] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [generationRefusal, setGenerationRefusal] = useState<string | null>(null)

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
    // Ideas and then pictures arrive in the background; keep looking until both are done.
    refetchInterval: (query) => (query.state.data?.searching === true ? SEARCH_POLL_MS : false),
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
    // Every press is a new request: it replaces the ideas nobody decided on, and the
    // server starts the search for pictures once the new plan exists. The button is
    // disabled while one request is in flight, so a double click cannot buy two plans.
    const key = `plan:broll:${candidateId}:${coverage}:${newRequestId()}`
    try {
      await createPlanApiV1ProjectsProjectIdCandidatesCandidateIdBrollPlansPost(
        projectId,
        candidateId,
        { coverage },
        { workspace_id: workspaceId },
        { headers: { 'Idempotency-Key': key } },
      )
      await suggestions.refetch()
    } catch (error) {
      const refused = error as ApiError
      if (refused.code === 'CONCURRENCY_LIMIT') {
        setRefusal('This Workspace is already running as many jobs as it can. Try again shortly.')
      } else {
        setFailure(refused)
      }
    } finally {
      setWorking(false)
    }
  }, [candidateId, coverage, projectId, suggestions, workspaceId])

  /** Ask the server what one media kind would cost for one suggestion. */
  const priceGeneration = useCallback(
    async (suggestionId: string, mediaKind: GenerationMediaKind) => {
      setGenerating(suggestionId)
      setOffer(null)
      setGenerationRefusal(null)
      setPricing(true)
      try {
        const answer =
          await createGenerationEstimateApiV1BrollSuggestionsSuggestionIdGenerationEstimatesPost(
            suggestionId,
            { mediaKind },
            { workspace_id: workspaceId },
          )
        setOffer(answer)
      } catch (error) {
        setGenerationRefusal(refusalCopy(error as ApiError))
      } finally {
        setPricing(false)
      }
    },
    [workspaceId],
  )

  /** Spend the sealed confirmation a member just read, exactly once. */
  const confirmGeneration = useCallback(async () => {
    const token = offer?.confirmationToken
    const kind = offer?.estimate?.mediaKind
    if (generating === null || token === null || token === undefined || kind === undefined) {
      return
    }
    setSubmitting(true)
    setGenerationRefusal(null)
    try {
      await createGenerationApiV1BrollSuggestionsSuggestionIdGeneratePost(
        generating,
        { confirmationToken: token, videoConfirmed: kind === 'video' },
        { workspace_id: workspaceId },
        { headers: { 'Idempotency-Key': `generate:${generating}:${kind}` } },
      )
      setGenerating(null)
      setOffer(null)
      setRefusal('Generating a picture. This continues in the background; the clip is unchanged.')
      await suggestions.refetch()
    } catch (error) {
      setGenerationRefusal(refusalCopy(error as ApiError))
    } finally {
      setSubmitting(false)
    }
  }, [generating, offer, suggestions, workspaceId])

  const found = suggestions.data?.suggestions ?? []
  const searching = suggestions.data?.searching === true
  const busy = working || deciding || searching
  // Absence is the backend's one answer for a clip nobody has planned yet and for a clip
  // this member has no standing on, and only the first is possible on a clip they already
  // have open. Every other failure still raises the alert carrying the request identifier.
  const unreadable = suggestions.isError && suggestions.error.status !== 404

  return (
    <section aria-label="B-roll" className="space-y-3">
      <header>
        <h2 className="text-title">B-roll</h2>
        <p className="text-caption text-muted-foreground">
          Suggested cutaways. Nothing is added to your clip until you accept it.
        </p>
      </header>

      <CoverageControl
        coverage={coverage}
        busy={busy}
        onCoverage={setCoverage}
        onSuggest={() => {
          void suggest()
        }}
      />

      {searching ? (
        <p role="status" className="text-caption text-muted-foreground">
          Ideas arrive first, then pictures for them. This can take a minute.
        </p>
      ) : null}
      {refusal === null ? null : (
        <p role="status" className="py-2 text-small">
          {refusal}
        </p>
      )}
      {failure === null ? null : <ErrorNotice error={failure} />}
      {unreadable ? <ErrorNotice error={suggestions.error} /> : null}

      {suggestions.isPending ? (
        <p role="status" className="text-caption text-muted-foreground">
          Reading this clip&apos;s suggestions…
        </p>
      ) : null}

      {!suggestions.isPending && !unreadable && !searching && found.length === 0 ? (
        <p className="text-caption text-muted-foreground">
          No B-roll suggestions yet. Ask for some when this moment would be clearer with a picture.
        </p>
      ) : null}

      {generating === null ? null : (
        <GenerationConfirmDialog
          offer={offer}
          loading={pricing}
          submitting={submitting}
          videoOffered={offer?.videoOffered === true}
          failure={generationRefusal}
          onConfirm={() => {
            void confirmGeneration()
          }}
          onConsiderVideo={() => {
            void priceGeneration(generating, 'video')
          }}
          onClose={() => {
            setGenerating(null)
            setOffer(null)
            setGenerationRefusal(null)
          }}
        />
      )}

      {found.map((suggestion) => (
        <BrollSuggestionCard
          key={suggestion.id}
          suggestion={suggestion}
          clipStartMs={clipStartMs}
          alternatives={assets.data?.assets ?? []}
          busy={busy}
          generationOffered={generationOffered(suggestion)}
          onGenerate={() => {
            void priceGeneration(suggestion.id, 'image')
          }}
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

/**
 * Whether this proposal is one generation could still help with.
 *
 * The server decides this too, and refuses admission either way; the panel mirrors the
 * same rule so a member is never offered a button whose only answer is a refusal.
 */
function generationOffered(suggestion: BrollSuggestionResponse): boolean {
  if (suggestion.status !== 'proposed') {
    return false
  }
  if (suggestion.assetId === null || suggestion.assetId === undefined) {
    return true
  }
  return (suggestion.relevanceScore ?? 0) < SUFFICIENT_RELEVANCE
}

/** Say what a refusal means in words a member can act on, or keep the request ID. */
function refusalCopy(error: ApiError): string {
  if (error.code === 'QUOTA_EXCEEDED') {
    return 'This Workspace has spent its monthly generated-media allowance. It resets next month.'
  }
  if (error.code === 'CONCURRENCY_LIMIT') {
    return 'This Workspace is already running as many jobs as it can. Try again shortly.'
  }
  if (error.code === 'GENERATION_NOT_ELIGIBLE') {
    return 'This suggestion can no longer be generated.'
  }
  if (error.code === 'GENERATION_CONFIRMATION_INVALID') {
    return 'This estimate expired. Ask for a new one.'
  }
  return `Generation could not be started. Reference ${error.requestId}.`
}

/** Read one suggestion as the placement the planner chose for it. */
function placementOf(suggestion: BrollSuggestionResponse): BrollPlacement {
  return {
    suggestionId: suggestion.id,
    assetId: suggestion.assetId ?? '',
    // A generated still is placed as an image; everything retrieved so far is video.
    mediaKind: suggestion.sourceType === 'generated' ? 'image' : 'video',
    startMs: suggestion.startMs,
    endMs: suggestion.endMs,
  }
}
