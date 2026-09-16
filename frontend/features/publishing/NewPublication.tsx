'use client'

import { useQuery } from '@tanstack/react-query'
import { FileVideo } from 'lucide-react'
import Link from 'next/link'
import { useState } from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { PageHeader } from '@/components/page-header'
import { useSession } from '@/features/auth/session'
import { PRESET_LABELS, formatInstant } from '@/features/exports/export-list'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { ExportPageResponse, ExportResponse, RenderDownloadResponse } from '@/lib/api/generated/model'
import { downloadApiV1RendersRenderIdDownloadUrlGet } from '@/lib/api/generated/renders/renders'
import { exportCollectionApiV1ExportsGet } from '@/lib/api/generated/studio/studio'

import { PublicationComposer } from './PublicationComposer'

/** The finished export a publication will send, as the composer needs to name it. */
export interface ChosenArtifact {
  editId: string
  revision: number
  renderArtifactId: string
  durationMs: number
}

/**
 * Start a publication from a finished export.
 *
 * A publication is always one exact rendered file, so the first step is choosing that
 * file. Arriving from an export's Publish action preselects it; either way the creator
 * sees the file they are about to send before choosing where it goes.
 */
export function NewPublication({ preselected }: { preselected: ChosenArtifact | null }) {
  const session = useSession()
  const [chosen, setChosen] = useState<ChosenArtifact | null>(preselected)

  return (
    <section className="space-y-6">
      <PageHeader
        title="New publication"
        crumbs={[{ href: '/dashboard/publishing', label: 'Publishing' }]}
        description="Choose an exported clip, pick where it goes, fill in what each platform asks for, and confirm."
      />
      {session.data !== undefined && !session.data.capabilities.socialPublishing ? (
        <EmptyState
          title="Social publishing is not enabled for this deployment."
          description="You can still download any finished export and upload it yourself."
        />
      ) : chosen === null ? (
        <ExportPicker onChoose={setChosen} />
      ) : (
        <div className="space-y-6">
          <ChosenExport artifact={chosen} onChange={() => setChosen(null)} />
          <PublicationComposer
            editId={chosen.editId}
            revision={chosen.revision}
            renderArtifactId={chosen.renderArtifactId}
            renderDigest={null}
            durationMs={chosen.durationMs}
          />
        </div>
      )}
    </section>
  )
}

/** Step one: the finished exports this Workspace can publish. */
function ExportPicker({ onChoose }: { onChoose: (artifact: ChosenArtifact) => void }) {
  const { active } = useWorkspaceScope()
  const exports = useQuery<ExportPageResponse, ApiError>({
    queryKey: ['/api/v1/exports', active.id, 'ready'],
    queryFn: ({ signal }) =>
      exportCollectionApiV1ExportsGet({ workspace_id: active.id, state: 'ready', limit: 50 }, { signal }),
    retry: false,
  })

  if (exports.isPending) {
    return <LoadingState label="Loading finished exports…" variant="rows" />
  }
  if (exports.isError) {
    return <ErrorNotice error={exports.error} onRetry={() => void exports.refetch()} />
  }
  const ready = exports.data.exports.filter((entry) => entry.renderId !== null)
  if (ready.length === 0) {
    return (
      <EmptyState
        icon={FileVideo}
        title="No finished exports yet"
        description="Open a clip in the editor and choose Export. Finished exports appear here to publish."
        action={
          <Link href="/dashboard/clips" className="text-sm font-medium text-primary hover:underline">
            Go to clips
          </Link>
        }
      />
    )
  }
  return (
    <fieldset className="space-y-3">
      <legend className="text-sm font-semibold">1. Choose an export</legend>
      <ul aria-label="Finished exports" className="surface divide-y">
        {ready.map((entry) => (
          <li key={entry.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
            <span className="flex size-10 items-center justify-center rounded-lg bg-accent text-accent-foreground">
              <FileVideo aria-hidden="true" className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{entry.projectName}</p>
              <p className="text-xs text-muted-foreground">
                {presetName(entry)} · Revision {entry.revision} · {formatInstant(entry.createdAt)}
              </p>
            </div>
            <button
              type="button"
              onClick={() =>
                onChoose({
                  editId: entry.editId,
                  revision: entry.revision,
                  renderArtifactId: entry.renderId ?? '',
                  durationMs: entry.durationMs ?? 0,
                })
              }
              className="h-9 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              Use this export
            </button>
          </li>
        ))}
      </ul>
    </fieldset>
  )
}

/** The file about to be published, previewed through a short-lived capability. */
function ChosenExport({ artifact, onChange }: { artifact: ChosenArtifact; onChange: () => void }) {
  const { active } = useWorkspaceScope()
  const preview = useQuery<RenderDownloadResponse, ApiError>({
    queryKey: ['/api/v1/renders/download-url', active.id, artifact.renderArtifactId],
    queryFn: ({ signal }) =>
      downloadApiV1RendersRenderIdDownloadUrlGet(
        artifact.renderArtifactId,
        { workspace_id: active.id },
        { signal },
      ),
    retry: false,
    gcTime: 0,
    staleTime: 0,
  })

  return (
    <section aria-label="Export to publish" className="surface flex flex-col gap-4 p-5 sm:flex-row">
      <div className="aspect-[9/16] w-full max-w-[180px] overflow-hidden rounded-lg bg-foreground/90">
        {preview.data === undefined ? null : (
          <video
            src={preview.data.url}
            controls
            preload="metadata"
            onError={() => void preview.refetch()}
            className="size-full object-contain"
          />
        )}
      </div>
      <div className="flex-1 space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-primary">Publishing this export</p>
        <p className="text-sm">
          Revision {artifact.revision} · {Math.round(artifact.durationMs / 1000)} seconds
        </p>
        {preview.isError ? <ErrorNotice error={preview.error} /> : null}
        <button
          type="button"
          onClick={onChange}
          className="text-sm font-medium text-primary hover:underline"
        >
          Choose a different export
        </button>
      </div>
    </section>
  )
}

function presetName(entry: ExportResponse): string {
  const preset = PRESET_LABELS[entry.preset]
  return preset === undefined ? entry.preset : `${preset.name} ${preset.ratio}`
}
