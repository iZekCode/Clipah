'use client'

import { useMutation, useQuery } from '@tanstack/react-query'
import { Download, FileVideo, Send } from 'lucide-react'
import Link from 'next/link'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { StatusBadge, type StatusTone } from '@/components/status-badge'
import { useSession } from '@/features/auth/session'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { ExportPageResponse, ExportResponse, RenderDownloadResponse } from '@/lib/api/generated/model'
import { downloadApiV1RendersRenderIdDownloadUrlGet } from '@/lib/api/generated/renders/renders'
import { exportCollectionApiV1ExportsGet } from '@/lib/api/generated/studio/studio'

/** What each export preset is called by a creator choosing where a clip will go. */
export const PRESET_LABELS: Record<string, { name: string; ratio: string; use: string }> = {
  '1080x1920': { name: 'Vertical', ratio: '9:16', use: 'Shorts, Reels, TikTok' },
  '1080x1350': { name: 'Portrait', ratio: '4:5', use: 'Instagram and Facebook feeds' },
  '1080x1080': { name: 'Square', ratio: '1:1', use: 'Feeds and carousels' },
  '1920x1080': { name: 'Landscape', ratio: '16:9', use: 'YouTube and websites' },
}

const STATUS: Record<string, { label: string; tone: StatusTone }> = {
  queued: { label: 'Queued', tone: 'neutral' },
  rendering: { label: 'Rendering', tone: 'progress' },
  ready: { label: 'Ready', tone: 'success' },
  failed: { label: 'Failed', tone: 'danger' },
  canceled: { label: 'Canceled', tone: 'neutral' },
}

const IN_PROGRESS = new Set(['queued', 'rendering'])

/** The query key one export list is cached under. */
export function exportsQueryKey(workspaceId: string, scope: { editId?: string; projectId?: string }) {
  return ['/api/v1/exports', workspaceId, scope.editId ?? null, scope.projectId ?? null] as const
}

/**
 * Read one Edit's or one Project's exports, and keep reading while any is still encoding.
 *
 * Exports are durable rows, so a refresh or a return visit shows exactly what the backend
 * holds; polling stops the moment nothing is left in progress.
 */
export function useExports(scope: { editId?: string; projectId?: string }) {
  const { active } = useWorkspaceScope()
  return useQuery<ExportPageResponse, ApiError>({
    queryKey: exportsQueryKey(active.id, scope),
    queryFn: ({ signal }) =>
      exportCollectionApiV1ExportsGet(
        {
          workspace_id: active.id,
          limit: 50,
          ...(scope.editId === undefined ? {} : { editId: scope.editId }),
          ...(scope.projectId === undefined ? {} : { projectId: scope.projectId }),
        },
        { signal },
      ),
    retry: false,
    refetchInterval: (query) =>
      (query.state.data?.exports ?? []).some((entry) => IN_PROGRESS.has(entry.status)) ? 4_000 : false,
  })
}

/** The exports of one Edit or one Project, each with what can be done with it now. */
export function ExportList({
  editId,
  projectId,
  showProject = false,
  emptyDescription = 'Open a clip in the editor and choose Export to make a file you can download or publish.',
}: {
  editId?: string
  projectId?: string
  showProject?: boolean
  emptyDescription?: string
}) {
  const exports = useExports({ editId, projectId })

  if (exports.isPending) {
    return <LoadingState label="Loading exports…" variant="rows" count={2} />
  }
  if (exports.isError) {
    return <ErrorNotice error={exports.error} onRetry={() => void exports.refetch()} />
  }
  if (exports.data.exports.length === 0) {
    return (
      <EmptyState compact icon={FileVideo} title="No exports yet" description={emptyDescription} />
    )
  }
  return (
    <ul aria-label="Exports" className="surface divide-y">
      {exports.data.exports.map((entry) => (
        <ExportRow key={entry.id} entry={entry} showProject={showProject} />
      ))}
    </ul>
  )
}

/** One export: its shape, its state, and — once finished — Download and Publish. */
export function ExportRow({ entry, showProject }: { entry: ExportResponse; showProject: boolean }) {
  const preset = PRESET_LABELS[entry.preset]
  const status = STATUS[entry.status] ?? { label: entry.status, tone: 'neutral' as const }

  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3">
      <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground">
        <FileVideo aria-hidden="true" className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">
          {preset === undefined ? entry.preset : `${preset.name} ${preset.ratio}`}
          <span className="font-normal text-muted-foreground"> · Revision {entry.revision}</span>
        </p>
        <p className="truncate text-xs text-muted-foreground">
          {showProject ? `${entry.projectName} · ` : ''}
          {formatInstant(entry.createdAt)}
          {entry.durationMs === null ? '' : ` · ${formatSeconds(entry.durationMs)}`}
          {entry.sizeBytes === null ? '' : ` · ${formatBytes(entry.sizeBytes)}`}
        </p>
        {entry.status === 'failed' ? (
          <p className="text-xs text-destructive">
            This export could not be finished{entry.errorCode === null ? '.' : ` (${entry.errorCode}).`}{' '}
            Export again from the editor.
          </p>
        ) : null}
      </div>
      <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
      {entry.status === 'ready' && entry.renderId !== null ? (
        <ReadyActions entry={entry} renderId={entry.renderId} />
      ) : null}
    </li>
  )
}

function ReadyActions({ entry, renderId }: { entry: ExportResponse; renderId: string }) {
  const { active } = useWorkspaceScope()
  const session = useSession()
  const download = useMutation<RenderDownloadResponse, ApiError>({
    // The capability is asked for at the moment of the click, because it lives five minutes.
    mutationFn: () => downloadApiV1RendersRenderIdDownloadUrlGet(renderId, { workspace_id: active.id }),
    onSuccess: (signed) => {
      window.location.assign(signed.url)
    },
  })
  const publishing = session.data?.capabilities.socialPublishing === true

  return (
    <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
      <button
        type="button"
        onClick={() => download.mutate()}
        disabled={download.isPending}
        className="inline-flex h-9 items-center gap-1.5 rounded-lg border bg-card px-3 text-sm font-medium hover:bg-secondary disabled:opacity-50"
      >
        <Download aria-hidden="true" className="size-4" />
        {download.isPending ? 'Preparing…' : 'Download'}
      </button>
      {publishing ? (
        <Link
          href={publishHref(entry, renderId)}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          <Send aria-hidden="true" className="size-4" />
          Publish
        </Link>
      ) : null}
      {download.isError ? <ErrorNotice error={download.error} /> : null}
    </div>
  )
}

/** The composer route for one finished export, preselected. */
export function publishHref(entry: ExportResponse, renderId: string): string {
  const query = new URLSearchParams({
    editId: entry.editId,
    revision: String(entry.revision),
    renderArtifactId: renderId,
    durationMs: String(entry.durationMs ?? 0),
  })
  return `/dashboard/publishing/new?${query.toString()}`
}

export function formatInstant(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

function formatSeconds(durationMs: number): string {
  const total = Math.round(durationMs / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}
