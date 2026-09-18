'use client'

import { useQuery } from '@tanstack/react-query'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { WaveformResponse } from '@/lib/api/generated/model'
import { waveformApiV1ProjectsProjectIdWaveformGet } from '@/lib/api/generated/studio/studio'

import { SIGNED_MEDIA_STALE_MS } from './use-storyboard'

/**
 * One Project's waveform peaks.
 *
 * The manifest is an API read; the bytes come from the signed object URL it names, fetched
 * without credentials. Peaks are immutable per version, so the bytes are read once.
 */
export function useWaveform(projectId: string, { enabled }: { enabled: boolean }) {
  const { active } = useWorkspaceScope()
  const manifest = useQuery<WaveformResponse, ApiError>({
    queryKey: ['/api/v1/projects/waveform', active.id, projectId],
    queryFn: ({ signal }) =>
      waveformApiV1ProjectsProjectIdWaveformGet(projectId, { workspace_id: active.id }, { signal }),
    enabled,
    retry: false,
    staleTime: SIGNED_MEDIA_STALE_MS,
  })
  const url = manifest.data?.url ?? null
  const peaks = useQuery<Uint8Array, Error>({
    queryKey: ['waveform-peaks', active.id, projectId, manifest.data?.version ?? 0],
    queryFn: async ({ signal }) => {
      const response = await fetch(url ?? '', { signal, credentials: 'omit' })
      if (!response.ok) throw new Error(`waveform ${response.status}`)
      return new Uint8Array(await response.arrayBuffer())
    },
    enabled: url !== null,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  })
  return {
    peaks: peaks.data ?? null,
    peaksPerSecond: manifest.data?.peaksPerSecond ?? null,
    durationMs: manifest.data?.durationMs ?? null,
    isError: manifest.isError || peaks.isError,
  }
}
