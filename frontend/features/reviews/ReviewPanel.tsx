'use client'

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import type { ApiError } from '@/lib/api/client'
import {
  createReviewCommentApiV1EditsEditIdReviewCommentsPost,
  createReviewDecisionApiV1EditsEditIdReviewsPost,
  showEditReviewApiV1EditsEditIdReviewsGet,
  updateCommentResolutionApiV1EditReviewCommentsCommentIdResolutionPost,
} from '@/lib/api/generated/edit-reviews/edit-reviews'
import type { ReviewSummaryResponse } from '@/lib/api/generated/model'

/** Comments and append-only decisions for one exact immutable Edit Revision. */
export function ReviewPanel({
  editId,
  revisionId,
  workspaceId,
  canReview,
  playheadMs = 0,
}: {
  editId: string
  revisionId: string
  workspaceId: string
  canReview: boolean
  playheadMs?: number
}) {
  const [comment, setComment] = useState('')
  const [notice, setNotice] = useState('')
  const [mutationError, setMutationError] = useState<ApiError | null>(null)
  const summary = useQuery<ReviewSummaryResponse, ApiError>({
    queryKey: ['/api/v1/edit-reviews', workspaceId, editId],
    queryFn: ({ signal }) =>
      showEditReviewApiV1EditsEditIdReviewsGet(editId, { workspace_id: workspaceId }, { signal }),
    retry: false,
  })

  async function decide(decision: 'approve' | 'request_changes') {
    setMutationError(null)
    try {
      await createReviewDecisionApiV1EditsEditIdReviewsPost(
        editId,
        { revisionId, decision },
        { workspace_id: workspaceId },
      )
      setNotice(decision === 'approve' ? 'Revision approved.' : 'Changes requested.')
      await summary.refetch()
    } catch (error) {
      setMutationError(error as ApiError)
    }
  }

  async function submitComment() {
    const text = comment.trim()
    if (text === '') return
    setMutationError(null)
    try {
      await createReviewCommentApiV1EditsEditIdReviewCommentsPost(
        editId,
        { revisionId, text, anchor: { kind: 'timestamp', timestampMs: playheadMs } },
        { workspace_id: workspaceId },
      )
      setComment('')
      setNotice('Comment added.')
      await summary.refetch()
    } catch (error) {
      setMutationError(error as ApiError)
    }
  }

  async function setResolved(commentId: string, resolved: boolean) {
    setMutationError(null)
    try {
      await updateCommentResolutionApiV1EditReviewCommentsCommentIdResolutionPost(
        commentId,
        { resolved },
        { workspace_id: workspaceId },
      )
      setNotice(resolved ? 'Comment resolved.' : 'Comment reopened.')
      await summary.refetch()
    } catch (error) {
      setMutationError(error as ApiError)
    }
  }

  return (
    <section aria-labelledby="review-heading" className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 id="review-heading" className="text-sm font-semibold">Revision review</h2>
        <p aria-live="polite" className="text-caption font-medium">
          {summary.data?.approved ? 'Approved' : 'Awaiting approval'}
        </p>
      </div>
      {summary.isPending ? <p role="status">Loading review…</p> : null}
      {summary.isError ? <ErrorNotice error={summary.error} /> : null}
      {mutationError === null ? null : <ErrorNotice error={mutationError} />}
      <p aria-live="polite" className="sr-only">{notice}</p>

      {canReview ? (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void decide('approve')} className="inline-flex h-8 items-center rounded-md bg-primary px-3 text-small font-semibold text-primary-foreground hover:bg-primary-hover disabled:opacity-40">
              Approve revision
            </button>
            <button type="button" onClick={() => void decide('request_changes')} className="inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40">
              Request changes
            </button>
          </div>
          <label className="block text-small font-medium" htmlFor="review-comment">Comment at current time</label>
          <textarea id="review-comment" value={comment} onChange={(event) => setComment(event.target.value)} maxLength={4000} className="min-h-20 w-full rounded-md border border-input bg-secondary p-2 text-small" />
          <button type="button" onClick={() => void submitComment()} disabled={comment.trim() === ''} className="inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40">
            Add comment
          </button>
        </div>
      ) : null}

      <ul aria-label="Review comments" className="space-y-2">
        {(summary.data?.comments ?? []).map((item) => (
          <li key={item.id} className="py-2 text-small">
            <p>{item.text}</p>
            <p className="text-caption text-muted-foreground">
              {item.anchor.kind === 'timestamp' ? `At ${item.anchor.timestampMs ?? 0} ms` : `On ${item.anchor.itemId ?? 'item'}`}
            </p>
            {canReview ? (
              <button type="button" onClick={() => void setResolved(item.id, !item.resolved)} className="mt-1 text-caption underline">
                {item.resolved ? 'Reopen comment' : 'Resolve comment'}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  )
}
