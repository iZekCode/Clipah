import { act, render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'

import { Toaster } from '@/components/ui/sonner'
import { ApiError } from '@/lib/api/client'
import { notify } from '@/lib/notify'

describe('notify', () => {
  test('shows a confirmation with its action', async () => {
    render(<Toaster />)

    act(() => {
      notify.success('Export ready', { action: { label: 'Download', onClick: () => undefined } })
    })

    expect(await screen.findByText('Export ready')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument()
  })

  test('a repeated id replaces the toast, and a second action is offered', async () => {
    render(<Toaster />)
    act(() => {
      notify.success('Export ready', { id: 'export-ready-1', secondaryAction: { label: 'Publish', onClick: () => undefined } })
      notify.success('Export ready', { id: 'export-ready-1', secondaryAction: { label: 'Publish', onClick: () => undefined } })
    })

    expect(await screen.findAllByText('Export ready')).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Publish' })).toBeInTheDocument()
  })

  test('says what failed in the backend’s words and keeps the reference for support', async () => {
    render(<Toaster />)
    const error = new ApiError({
      status: 409,
      code: 'EDIT_REVISION_CONFLICT',
      message: 'This clip changed since you opened it.',
      requestId: 'request-77',
    })

    act(() => {
      notify.failure(error)
    })

    expect(await screen.findByText('This clip changed since you opened it.')).toBeInTheDocument()
    expect(screen.getByText('Ref request-77')).toBeInTheDocument()
  })
})
