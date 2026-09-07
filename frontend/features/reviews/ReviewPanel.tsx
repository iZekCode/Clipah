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
    <section aria-labelledby="review-heading" className="space-y-3 rounded-lg border p-3">
      <div className="flex items-center justify-between gap-2">
        <h2 id="review-heading" className="text-sm font-semibold">Revision review</h2>
        <p aria-live="polite" className="text-xs font-medium">
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
            <button type="button" onClick={() => void decide('approve')} className="rounded bg-primary px-3 py-2 text-sm text-primary-foreground">
              Approve revision
            </button>
            <button type="button" onClick={() => void decide('request_changes')} className="rounded border px-3 py-2 text-sm">
              Request changes
            </button>
          </div>
          <label className="block text-sm font-medium" htmlFor="review-comment">Comment at current time</label>
          <textarea id="review-comment" value={comment} onChange={(event) => setComment(event.target.value)} maxLength={4000} className="min-h-20 w-full rounded border bg-background p-2 text-sm" />
          <button type="button" onClick={() => void submitComment()} disabled={comment.trim() === ''} className="rounded border px-3 py-2 text-sm disabled:opacity-50">
            Add comment
          </button>
        </div>
      ) : null}

      <ul aria-label="Review comments" className="space-y-2">
        {(summary.data?.comments ?? []).map((item) => (
          <li key={item.id} className="rounded border p-2 text-sm">
            <p>{item.text}</p>
            <p className="text-xs text-muted-foreground">
              {item.anchor.kind === 'timestamp' ? `At ${item.anchor.timestampMs ?? 0} ms` : `On ${item.anchor.itemId ?? 'item'}`}
            </p>
            {canReview ? (
              <button type="button" onClick={() => void setResolved(item.id, !item.resolved)} className="mt-1 text-xs underline">
                {item.resolved ? 'Reopen comment' : 'Resolve comment'}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  )
}
