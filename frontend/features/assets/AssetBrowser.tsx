'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { ImageIcon } from 'lucide-react'
import Link from 'next/link'
import { useId, useState } from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { DesignedFrame, Poster } from '@/components/media/poster'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
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
import { formatClock } from '@/lib/media/time'

const PAGE_SIZE = 24

const KINDS: { value: AssetKind | ''; label: string }[] = [
  { value: '', label: 'All media' },
  { value: 'source', label: 'Source videos' },
  { value: 'broll', label: 'B-roll' },
  { value: 'picture', label: 'Pictures' },
  { value: 'render', label: 'Exports' },
]

const KIND_LABELS: Record<string, string> = {
  source: 'Source video',
  broll: 'B-roll',
  picture: 'Picture',
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
        <ul aria-label="Assets" className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
          {listed.map((asset) => (
            <li key={asset.id}>
              <AssetCard asset={asset} onPreview={() => setPreviewing(asset)} />
            </li>
          ))}
        </ul>
      )}

      {assets.hasNextPage ? (
        <div className="flex justify-center">
          <Button
            type="button"
            variant="secondary"
            onClick={() => void assets.fetchNextPage()}
            disabled={assets.isFetchingNextPage}
          >
            {assets.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </Button>
        </div>
      ) : null}

      {previewing === null ? null : (
        <AssetPreview asset={previewing} onClose={() => setPreviewing(null)} />
      )}
    </section>
  )
}

function AssetCard({ asset, onPreview }: { asset: LibraryAssetResponse; onPreview: () => void }) {
  const kindLabel = KIND_LABELS[asset.kind] ?? asset.kind
  const durationMs = asset.durationMs ?? undefined
  return (
    <article className="flex h-full flex-col gap-2">
      <button
        type="button"
        onClick={onPreview}
        aria-label={`Preview ${kindLabel} from ${asset.projectName}`}
        className="relative block aspect-video overflow-hidden rounded-md border border-border transition-colors duration-fast ease-signal hover:border-line-strong"
      >
        {asset.kind === 'source' ? (
          <Poster projectId={asset.projectId} durationMs={durationMs} />
        ) : (
          // Storyboards exist only for source videos, so other kinds show a designed frame.
          <DesignedFrame durationMs={durationMs} />
        )}
      </button>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusBadge tone="neutral">{kindLabel}</StatusBadge>
          <StatusBadge tone={asset.sourceType === 'generated' ? 'attention' : 'neutral'}>
            {SOURCE_LABELS[asset.sourceType] ?? asset.sourceType}
          </StatusBadge>
        </div>
        <Link
          href={`/dashboard/projects/${asset.projectId}`}
          className="truncate text-small font-semibold hover:underline"
        >
          {asset.projectName}
        </Link>
        <p className="tabular font-mono text-caption text-muted-foreground">
          {asset.durationMs === null ? '' : `${formatClock(asset.durationMs)} · `}
          {formatBytes(asset.sizeBytes)}
        </p>
        {asset.provenance === null ? null : <Provenance provenance={asset.provenance} />}
      </div>
    </article>
  )
}

/** Where a file came from and the licence it is used under, as the backend recorded it. */
function Provenance({ provenance }: { provenance: NonNullable<LibraryAssetResponse['provenance']> }) {
  return (
    <div className="text-caption">
      <p className="font-medium">{provenance.attributionText}</p>
      <p className="text-muted-foreground">
        {provenance.provider} ·{' '}
        <a href={provenance.licenseUrl} target="_blank" rel="noreferrer noopener" className="underline">
          {provenance.licenseName}
        </a>
      </p>
    </div>
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
    <Sheet open onOpenChange={(open) => (open ? undefined : onClose())}>
      <SheetContent side="right" className="w-full space-y-5 overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
          <SheetDescription>This preview link works for five minutes.</SheetDescription>
        </SheetHeader>
        {signed.isError ? (
          <ErrorNotice error={signed.error} onRetry={() => void signed.refetch()} />
        ) : signed.data === undefined ? (
          <LoadingState label="Opening preview…" />
        ) : signed.data.contentType.startsWith('image/') ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={signed.data.url} alt={title} className="max-h-[60vh] w-full rounded-md bg-stage object-contain" />
        ) : (
          <video
            src={signed.data.url}
            controls
            preload="metadata"
            onError={() => void signed.refetch()}
            className="max-h-[60vh] w-full rounded-md bg-stage"
          />
        )}
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 font-mono text-caption">
          <dt className="text-subtle-foreground">Type</dt>
          <dd>{asset.contentType}</dd>
          {asset.width === null || asset.height === null ? null : (
            <>
              <dt className="text-subtle-foreground">Size</dt>
              <dd>
                {asset.width} × {asset.height}
              </dd>
            </>
          )}
          <dt className="text-subtle-foreground">File</dt>
          <dd>{formatBytes(asset.sizeBytes)}</dd>
          {asset.durationMs === null ? null : (
            <>
              <dt className="text-subtle-foreground">Length</dt>
              <dd>{formatClock(asset.durationMs)}</dd>
            </>
          )}
          <dt className="text-subtle-foreground">Added</dt>
          <dd>{new Date(asset.createdAt).toLocaleDateString()}</dd>
        </dl>
        {asset.provenance === null ? null : <Provenance provenance={asset.provenance} />}
      </SheetContent>
    </Sheet>
  )
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}
