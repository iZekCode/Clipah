import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { useStoryboard } from '@/features/media/use-storyboard'
import { useWaveform } from '@/features/media/use-waveform'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { ApiWrapper, stubApi } from './support/api'
import { currentUser, workspace } from './support/fixtures'

const PROJECT_ID = '44444444-4444-4444-8444-444444444444'

function wrapper({ children }: { children: ReactNode }) {
  return (
    <ApiWrapper>
      <WorkspaceProvider>{children}</WorkspaceProvider>
    </ApiWrapper>
  )
}

beforeEach(() => {
  window.sessionStorage.clear()
})

describe('media hooks', () => {
  test('a storyboard is asked for once, for the active workspace, and only when wanted', async () => {
    const api = stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
      [`GET /api/v1/projects/${PROJECT_ID}/storyboard`]: {
        body: {
          version: 1,
          intervalMs: 2000,
          tileWidth: 160,
          tileHeight: 90,
          columns: 10,
          rows: 10,
          durationMs: 4000,
          expiresAt: '2026-09-17T00:05:00+00:00',
          sheets: [],
        },
      },
    })

    const idle = renderHook(() => useStoryboard(PROJECT_ID, { enabled: false }), { wrapper })
    const wanted = renderHook(() => useStoryboard(PROJECT_ID, { enabled: true }), { wrapper })

    await waitFor(() => expect(wanted.result.current.data?.durationMs).toBe(4000))
    const reads = api.calls.filter((call) => call.path.endsWith('/storyboard'))
    expect(reads).toHaveLength(1)
    expect(reads[0]?.params.get('workspace_id')).toBe(workspace().id)
    expect(idle.result.current.fetchStatus).toBe('idle')
  })

  test('waveform peaks are read from the signed URL without credentials', async () => {
    stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
      [`GET /api/v1/projects/${PROJECT_ID}/waveform`]: {
        body: {
          version: 1,
          peaksPerSecond: 20,
          durationMs: 150,
          url: 'https://media.test/peaks.bin',
          expiresAt: '2026-09-17T00:05:00+00:00',
        },
      },
    })
    const apiFetch = globalThis.fetch
    const media = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
      async () => new Response(new Uint8Array([0, 128, 255])),
    )
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).startsWith('https://media.test/') ? media(input, init) : apiFetch(input, init),
    )

    const { result } = renderHook(() => useWaveform(PROJECT_ID, { enabled: true }), { wrapper })

    await waitFor(() => expect(result.current.peaks).not.toBeNull())
    expect(Array.from(result.current.peaks ?? [])).toEqual([0, 128, 255])
    expect(result.current.peaksPerSecond).toBe(20)
    expect(media).toHaveBeenCalledWith(
      'https://media.test/peaks.bin',
      expect.objectContaining({ credentials: 'omit' }),
    )
  })

  test('a project with no waveform reports an error instead of silence', async () => {
    stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
    })

    const { result } = renderHook(() => useWaveform(PROJECT_ID, { enabled: true }), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.peaks).toBeNull()
  })
})
