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
    <section aria-label="Assets" className="flex flex-col gap-2 rounded-lg border p-3">
      <h2 className="text-sm font-medium">Assets</h2>
      <p className="text-xs text-muted-foreground">
        Media this project owns. Upload more from the project page.
      </p>

      {assets.isError && assets.error.status !== 404 ? <ErrorNotice error={assets.error} /> : null}
      {assets.isError && assets.error.status === 404 ? (
        <p className="text-xs text-muted-foreground">This project has no media to place yet.</p>
      ) : null}
      {assets.isPending ? (
        <p role="status" className="text-xs text-muted-foreground">
          Loading this project&apos;s media…
        </p>
      ) : null}

      <ul className="flex flex-col gap-2">
        {(assets.data?.assets ?? []).map((asset) => (
          <li key={asset.id} className="flex flex-col gap-1 rounded border p-2 text-xs">
            <span className="font-mono">{asset.contentType}</span>
            <span className="text-muted-foreground">
              {asset.durationMs === null ? 'Still image' : `${Math.round(asset.durationMs / 1000)}s`}
              {asset.width === null || asset.height === null
                ? ''
                : ` · ${asset.width}×${asset.height}`}
            </span>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => onAdd(asset)} className="rounded border px-2 py-1">
                Add to a sound lane
              </button>
              <button
                type="button"
                onClick={() => onExtract(asset)}
                className="rounded border px-2 py-1"
              >
                Extract audio
              </button>
            </div>
          </li>
        ))}
      </ul>

      {assets.isSuccess && assets.data.assets.length === 0 ? (
        <p className="text-xs text-muted-foreground">This project has no media to place yet.</p>
      ) : null}
    </section>
  )
}
