import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test } from 'vitest'

import { ReviewPanel } from '@/features/reviews/ReviewPanel'

import { renderWithApi, stubApi } from './support/api'

const EDIT_ID = '77777777-7777-4777-8777-777777777777'
const REVISION_ID = '88888888-8888-4888-8888-888888888888'
const WORKSPACE_ID = '33333333-3333-4333-8333-333333333333'
const REVIEWS = `GET /api/v1/edits/${EDIT_ID}/reviews`

describe('revision review panel', () => {
  test('renders comment text as text and submits approval for the exact revision', async () => {
    const api = stubApi({
      [REVIEWS]: {
        body: {
          approved: false,
          comments: [
            {
              id: '99999999-9999-4999-8999-999999999999',
              revisionId: REVISION_ID,
              actorUserId: '11111111-1111-4111-8111-111111111111',
              text: '<img src=x onerror=alert(1)>',
              anchor: { kind: 'timestamp', timestampMs: 1_200 },
              resolved: false,
              createdAt: '2026-09-07T00:00:00+00:00',
            },
          ],
          decisions: [],
        },
      },
      [`POST /api/v1/edits/${EDIT_ID}/reviews`]: {
        status: 201,
        body: {
          id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
          revisionId: REVISION_ID,
          actorUserId: '11111111-1111-4111-8111-111111111111',
          decision: 'approve',
          current: true,
          createdAt: '2026-09-07T00:00:00+00:00',
        },
      },
    })
    renderWithApi(
      <ReviewPanel
        editId={EDIT_ID}
        revisionId={REVISION_ID}
        workspaceId={WORKSPACE_ID}
        canReview
      />,
    )

    expect(await screen.findByText('<img src=x onerror=alert(1)>')).toBeVisible()
    expect(document.querySelector('img')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /approve revision/i }))

    await waitFor(() =>
      expect(
        api.calls.some(
          (call) =>
            call.method === 'POST' &&
            (call.body as { revisionId?: string }).revisionId === REVISION_ID,
        ),
      ).toBe(true),
    )
  })

  test('keeps review mutations hidden from viewers', async () => {
    stubApi({ [REVIEWS]: { body: { approved: false, comments: [], decisions: [] } } })
    renderWithApi(
      <ReviewPanel
        editId={EDIT_ID}
        revisionId={REVISION_ID}
        workspaceId={WORKSPACE_ID}
        canReview={false}
      />,
    )

    await screen.findByText(/awaiting approval/i)
    expect(screen.queryByRole('button', { name: /approve revision/i })).not.toBeInTheDocument()
  })
})
