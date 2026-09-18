'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Film, ImageIcon, Music } from 'lucide-react'
import Link from 'next/link'
import { useId, useState } from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Select } from '@/components/ui/select'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type {
  AssetKind,
  AssetPageResponse,
  LibraryAssetResponse,
  MediaPreviewResponse,
  ProjectPageResponse,
} from '@/lib/api/generated/model'
import { listCollectionApiV1ProjectsGet } from '@/lib/api/generated/projects/projects'
import {
  assetCollectionApiV1AssetsGet,
  previewAssetApiV1AssetsAssetIdPreviewUrlGet,
} from '@/lib/api/generated/studio/studio'

const PAGE_SIZE = 24

const KINDS: { value: AssetKind | ''; label: string }[] = [
  { value: '', label: 'All media' },
  { value: 'source', label: 'Source videos' },
  { value: 'broll', label: 'B-roll' },
  { value: 'render', label: 'Exports' },
]

const KIND_LABELS: Record<string, string> = {
  source: 'Source video',
  broll: 'B-roll',
  render: 'Export',
}

const SOURCE_LABELS: Record<string, string> = {
  user_upload: 'Uploaded',
  source_import: 'Imported',
  derived: 'Made by Clipah',
  generated: 'AI-generated',
  stock: 'Stock',
}

/**
 * The media this Workspace holds, browsable by Project and by kind.
 *
 * Every file still belongs to exactly one Project; this page lists them side by side but
 * never offers to reuse one across Projects. A file's origin and licence are shown where
 * the backend recorded them, and a preview is signed only when a member asks for it.
 */
export function AssetBrowser() {
  const { active } = useWorkspaceScope()
  const [projectId, setProjectId] = useState('')
  const [kind, setKind] = useState<AssetKind | ''>('')
  const [previewing, setPreviewing] = useState<LibraryAssetResponse | null>(null)
  const projectFieldId = useId()
  const kindFieldId = useId()

  const assets = useInfiniteQuery<AssetPageResponse, ApiError>({
    queryKey: ['/api/v1/assets', active.id, projectId, kind],
    queryFn: ({ pageParam, signal }) =>
      assetCollectionApiV1AssetsGet(
        {
          workspace_id: active.id,
          limit: PAGE_SIZE,
          ...(projectId === '' ? {} : { projectId }),
          ...(kind === '' ? {} : { kind }),
          ...(typeof pageParam === 'string' ? { cursor: pageParam } : {}),
        },
        { signal },
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor,
    retry: false,
  })
  const projects = useQuery<ProjectPageResponse, ApiError>({
    queryKey: ['/api/v1/projects', active.id, 'filter'],
    queryFn: ({ signal }) =>
      listCollectionApiV1ProjectsGet({ workspace_id: active.id, limit: 100 }, { signal }),
    retry: false,
  })

  const listed = assets.data?.pages.flatMap((page) => page.assets) ?? []
  const filtered = projectId !== '' || kind !== ''

  return (
    <section className="space-y-6">
      <PageHeader
        title="Assets"
        description="Source videos, B-roll, and exports from your projects, with where each one came from."
      />

      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={projectFieldId} className="sr-only">
          Project
        </label>
        <Select
          id={projectFieldId}
          value={projectId}
          onChange={(event) => setProjectId(event.target.value)}
          controlSize="sm"
        >
          <option value="">All projects</option>
          {(projects.data?.projects ?? []).map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </Select>
        <label htmlFor={kindFieldId} className="sr-only">
          Type
        </label>
        <Select
          id={kindFieldId}
          value={kind}
          onChange={(event) => setKind(event.target.value as AssetKind | '')}
          controlSize="sm"
        >
          {KINDS.map((entry) => (
            <option key={entry.value} value={entry.value}>
              {entry.label}
            </option>
          ))}
        </Select>
      </div>

      {assets.isPending ? (
        <LoadingState label="Loading assets…" variant="cards" count={6} />
      ) : assets.isError ? (
        <ErrorNotice error={assets.error} onRetry={() => void assets.refetch()} />
      ) : listed.length === 0 ? (
        <EmptyState
          icon={ImageIcon}
          title={filtered ? 'No assets match these filters' : 'No assets yet'}
          description={
            filtered
              ? 'Try another project or type.'
              : 'Media appears here once you add a video to a project.'
          }
        />
      ) : (
        <ul aria-label="Assets" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {listed.map((asset) => (
            <li key={asset.id}>
              <AssetCard asset={asset} onPreview={() => setPreviewing(asset)} />
            </li>
          ))}
        </ul>
      )}

      {assets.hasNextPage ? (
        <div className="flex justify-center">
          <button
            type="button"
            onClick={() => void assets.fetchNextPage()}
            disabled={assets.isFetchingNextPage}
            className="h-10 rounded-lg border bg-card px-4 text-sm font-medium hover:bg-secondary disabled:opacity-50"
          >
            {assets.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </button>
        </div>
      ) : null}

      {previewing === null ? null : (
        <AssetPreview asset={previewing} onClose={() => setPreviewing(null)} />
      )}
    </section>
  )
}

function AssetCard({ asset, onPreview }: { asset: LibraryAssetResponse; onPreview: () => void }) {
  const Icon = asset.contentType.startsWith('image/')
    ? ImageIcon
    : asset.contentType.startsWith('audio/')
      ? Music
      : Film
  return (
    <article className="surface flex h-full flex-col overflow-hidden">
      <button
        type="button"
        onClick={onPreview}
        aria-label={`Preview ${KIND_LABELS[asset.kind] ?? asset.kind} from ${asset.projectName}`}
        className="flex aspect-video items-center justify-center bg-gradient-to-br from-accent to-secondary text-accent-foreground hover:opacity-90"
      >
        <Icon aria-hidden="true" className="size-8 opacity-70" />
      </button>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge tone="accent">{KIND_LABELS[asset.kind] ?? asset.kind}</StatusBadge>
          <StatusBadge tone={asset.sourceType === 'generated' ? 'attention' : 'neutral'}>
            {SOURCE_LABELS[asset.sourceType] ?? asset.sourceType}
          </StatusBadge>
        </div>
        <Link
          href={`/dashboard/projects/${asset.projectId}`}
          className="truncate text-sm font-semibold hover:underline"
        >
          {asset.projectName}
        </Link>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          <dt>Size</dt>
          <dd>{formatBytes(asset.sizeBytes)}</dd>
          {asset.durationMs === null ? null : (
            <>
              <dt>Length</dt>
              <dd>{formatLength(asset.durationMs)}</dd>
            </>
          )}
          {asset.width === null || asset.height === null ? null : (
            <>
              <dt>Resolution</dt>
              <dd>
                {asset.width}×{asset.height}
              </dd>
            </>
          )}
          <dt>Added</dt>
          <dd>{new Date(asset.createdAt).toLocaleDateString()}</dd>
        </dl>
        {asset.provenance === null ? null : (
          <div className="mt-auto rounded-lg bg-secondary/60 p-2.5 text-xs">
            <p className="font-medium">{asset.provenance.attributionText}</p>
            <p className="text-muted-foreground">
              {asset.provenance.provider} ·{' '}
              <a
                href={asset.provenance.licenseUrl}
                target="_blank"
                rel="noreferrer noopener"
                className="underline"
              >
                {asset.provenance.licenseName}
              </a>
            </p>
          </div>
        )}
      </div>
    </article>
  )
}

/** One asset shown full size, through a capability signed when the member opened it. */
function AssetPreview({ asset, onClose }: { asset: LibraryAssetResponse; onClose: () => void }) {
  const { active } = useWorkspaceScope()
  const signed = useQuery<MediaPreviewResponse, ApiError>({
    queryKey: ['/api/v1/assets/preview-url', active.id, asset.id],
    queryFn: ({ signal }) =>
      previewAssetApiV1AssetsAssetIdPreviewUrlGet(asset.id, { workspace_id: active.id }, { signal }),
    retry: false,
    gcTime: 0,
    staleTime: 0,
  })
  const title = `${KIND_LABELS[asset.kind] ?? asset.kind} from ${asset.projectName}`

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>This preview link works for five minutes.</DialogDescription>
        </DialogHeader>
        {signed.isError ? (
          <ErrorNotice error={signed.error} onRetry={() => void signed.refetch()} />
        ) : signed.data === undefined ? (
          <LoadingState label="Opening preview…" />
        ) : signed.data.contentType.startsWith('image/') ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={signed.data.url} alt={title} className="max-h-[70vh] w-full rounded-lg object-contain" />
        ) : (
          <video
            src={signed.data.url}
            controls
            preload="metadata"
            onError={() => void signed.refetch()}
            className="max-h-[70vh] w-full rounded-lg bg-black"
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function formatLength(durationMs: number): string {
  const total = Math.round(durationMs / 1000)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    : `${minutes}:${String(seconds).padStart(2, '0')}`
}
