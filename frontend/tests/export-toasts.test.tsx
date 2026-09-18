import { act, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { Toaster } from '@/components/ui/sonner'
import { ExportList } from '@/features/exports/export-list'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { ApiWrapper, stubApi } from './support/api'
import { capabilities, currentUser, workspace } from './support/fixtures'

const EDIT_ID = '77777777-7777-4777-8777-777777777777'

function row(status: string) {
  return {
    id: '88888888-0000-4000-8000-000000000001',
    jobId: '88888888-0000-4000-8000-000000000002',
    status,
    errorCode: null,
    preset: '1080x1920',
    projectId: '44444444-4444-4444-8444-444444444444',
    projectName: 'Episode 12',
    candidateId: '55555555-5555-4555-8555-555555555551',
    editId: EDIT_ID,
    revisionId: '88888888-0000-4000-8000-000000000003',
    revision: 2,
    renderId: status === 'ready' ? '99999999-0000-4000-8000-000000000001' : null,
    durationMs: 30_000,
    sizeBytes: status === 'ready' ? 4_096_000 : null,
    createdAt: '2026-02-01T00:00:00+00:00',
    completedAt: status === 'ready' ? '2026-02-01T00:01:00+00:00' : null,
  }
}

beforeEach(() => {
  window.sessionStorage.clear()
})

describe('export toasts', () => {
  test('an export that finishes while it is watched says so with Download', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let status = 'rendering'
    stubApi({
      'GET /api/v1/me': { body: currentUser({ capabilities: capabilities({ socialPublishing: true }) }) },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
      'GET /api/v1/exports': () => ({ body: { exports: [row(status)], nextCursor: null } }),
    })
    render(
      <ApiWrapper>
        <WorkspaceProvider>
          <ExportList editId={EDIT_ID} />
        </WorkspaceProvider>
        <Toaster />
      </ApiWrapper>,
    )
    expect(await screen.findByText('Rendering')).toBeInTheDocument()

    status = 'ready'
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_100)
    })

    await waitFor(() => expect(screen.getByText('Export ready')).toBeInTheDocument())
    const toast = screen.getByText('Export ready').closest('[data-sonner-toast]') as HTMLElement
    expect(within(toast).getByRole('button', { name: 'Download' })).toBeInTheDocument()
    expect(within(toast).getByRole('button', { name: 'Publish' })).toBeInTheDocument()
    vi.useRealTimers()
  })

  test('an export already finished when the list opens raises nothing', async () => {
    stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
      'GET /api/v1/exports': { body: { exports: [row('ready')], nextCursor: null } },
    })
    render(
      <ApiWrapper>
        <WorkspaceProvider>
          <ExportList editId={EDIT_ID} />
        </WorkspaceProvider>
        <Toaster />
      </ApiWrapper>,
    )

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(screen.queryByText('Export ready')).not.toBeInTheDocument()
  })
})
