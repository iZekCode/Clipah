'use client'

import { useQuery } from '@tanstack/react-query'

import { ErrorNotice } from '@/components/error-notice'
import type { ApiError } from '@/lib/api/client'
import { indexApiV1ProjectsProjectIdAssetsGet } from '@/lib/api/generated/assets/assets'
import type { ProjectAssetResponse, ProjectAssetsResponse } from '@/lib/api/generated/model'

/**
 * The media this Project holds, and the two ways it reaches the timeline.
 *
 * The list is exactly what a save would authorize: a composition may only name assets
 * of its own Project, so offering anything wider here would be offering a change the
 * backend refuses. Importing new media stays where it already lives — the Project page —
 * because an import is ingest work, not an editing decision.
 */
export function AssetsPanel({
  projectId,
  workspaceId,
  onAdd,
  onExtract,
}: {
  projectId: string
  workspaceId: string
  onAdd: (asset: ProjectAssetResponse) => void
  onExtract: (asset: ProjectAssetResponse) => void
}) {
  const assets = useQuery<ProjectAssetsResponse, ApiError>({
    queryKey: ['/api/v1/projects/assets', workspaceId, projectId],
    queryFn: ({ signal }) =>
      indexApiV1ProjectsProjectIdAssetsGet(projectId, { workspace_id: workspaceId }, { signal }),
    retry: false,
  })

  return (
    <section aria-label="Assets" className="space-y-3">
      <h2 className="text-title">Assets</h2>
      <p className="text-caption text-muted-foreground">
        Media this project owns. Upload more from the project page.
      </p>

      {assets.isError && assets.error.status !== 404 ? <ErrorNotice error={assets.error} /> : null}
      {assets.isError && assets.error.status === 404 ? (
        <p className="text-caption text-muted-foreground">This project has no media to place yet.</p>
      ) : null}
      {assets.isPending ? (
        <p role="status" className="text-caption text-muted-foreground">
          Loading this project&apos;s media…
        </p>
      ) : null}

      <ul className="flex flex-col gap-2">
        {(assets.data?.assets ?? []).map((asset) => (
          <li key={asset.id} className="flex flex-col gap-1.5 py-2 text-small">
            <span className="font-mono">{asset.contentType}</span>
            <span className="text-muted-foreground">
              {asset.durationMs === null ? 'Still image' : `${Math.round(asset.durationMs / 1000)}s`}
              {asset.width === null || asset.height === null
                ? ''
                : ` · ${asset.width}×${asset.height}`}
            </span>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => onAdd(asset)} className="inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40">
                Add to a sound lane
              </button>
              <button
                type="button"
                onClick={() => onExtract(asset)}
                className="inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40"
              >
                Extract audio
              </button>
            </div>
          </li>
        ))}
      </ul>

      {assets.isSuccess && assets.data.assets.length === 0 ? (
        <p className="text-caption text-muted-foreground">This project has no media to place yet.</p>
      ) : null}
    </section>
  )
}
