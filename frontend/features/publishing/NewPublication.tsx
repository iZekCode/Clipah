'use client'

import { useQuery } from '@tanstack/react-query'
import { FileVideo } from 'lucide-react'
import Link from 'next/link'
import { useState } from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { Poster } from '@/components/media/poster'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { useSession } from '@/features/auth/session'
import { PRESET_LABELS, formatInstant } from '@/features/exports/export-list'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { ExportPageResponse, ExportResponse, RenderDownloadResponse } from '@/lib/api/generated/model'
import { downloadApiV1RendersRenderIdDownloadUrlGet } from '@/lib/api/generated/renders/renders'
import { exportCollectionApiV1ExportsGet } from '@/lib/api/generated/studio/studio'
import { formatClock } from '@/lib/media/time'

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
        <div className="grid gap-6 xl:grid-cols-[280px_minmax(0,1fr)_minmax(0,1.2fr)]">
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
          <Button asChild variant="secondary">
            <Link href="/dashboard/clips">Go to clips</Link>
          </Button>
        }
      />
    )
  }
  return (
    <fieldset className="space-y-3">
      <legend className="text-title">1. Choose an export</legend>
      <ul aria-label="Finished exports" className="divide-y divide-border rounded-lg border">
        {ready.map((entry) => (
          <li key={entry.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
            <span className="relative aspect-video w-20 shrink-0 overflow-hidden rounded-sm">
              <Poster projectId={entry.projectId} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-small font-medium">{entry.projectName}</p>
              <p className="font-mono text-caption text-muted-foreground">
                {presetName(entry)} · Revision {entry.revision} · {formatInstant(entry.createdAt)}
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              onClick={() =>
                onChoose({
                  editId: entry.editId,
                  revision: entry.revision,
                  renderArtifactId: entry.renderId ?? '',
                  durationMs: entry.durationMs ?? 0,
                })
              }
            >
              Use this export
            </Button>
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
    <section aria-label="Export to publish" className="space-y-4">
      <div className="relative mx-auto aspect-[9/16] w-full max-w-[260px] overflow-hidden rounded-[28px] border-[6px] border-secondary bg-stage">
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
      <div className="space-y-2 text-center">
        <p className="text-caption font-medium uppercase tracking-wide text-subtle-foreground">
          Publishing this export
        </p>
        <p className="tabular font-mono text-caption text-muted-foreground">
          Revision {artifact.revision} · {formatClock(artifact.durationMs)}
        </p>
        {preview.isError ? <ErrorNotice error={preview.error} /> : null}
        <Button type="button" variant="ghost" size="sm" onClick={onChange}>
          Choose a different export
        </Button>
      </div>
    </section>
  )
}

function presetName(entry: ExportResponse): string {
  const preset = PRESET_LABELS[entry.preset]
  return preset === undefined ? entry.preset : `${preset.name} ${preset.ratio}`
}
