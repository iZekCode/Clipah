'use client'

import { useMutation, useQuery } from '@tanstack/react-query'
import { Scissors } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import type { ReactNode } from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { PageHeader, Section } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { BrollProvenanceList } from '@/features/broll/BrollProvenanceList'
import { CampaignPanel } from '@/features/campaigns/CampaignPanel'
import { ExportRow } from '@/features/exports/export-list'
import { mayWriteProjects } from '@/features/workspaces/roles'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { createApiV1ProjectsProjectIdCandidatesCandidateIdEditsPost, historyApiV1EditsEditIdRevisionsGet } from '@/lib/api/generated/edits/edits'
import type {
  ClipDetailResponse,
  EditResponse,
  RevisionHistoryResponse,
} from '@/lib/api/generated/model'
import { showClipApiV1ClipsCandidateIdGet } from '@/lib/api/generated/studio/studio'

import { formatDuration } from './ClipCard'
import { ClipPreview } from './ClipPreview'
import { EvidencePanel } from './EvidencePanel'
import { VariantLab } from './VariantLab'

/**
 * One clip, seen from outside the editor, resolved from nothing but its own identifier.
 *
 * A link to a clip carries one UUID; the backend answers which Project it belongs to,
 * which Edits were opened from it, and which files were exported. From here a creator can
 * preview it, continue editing, download or publish an export, and — behind their own
 * sections — look at its variants, the evidence behind its claims, and its campaign copy.
 *
 * `projectId`, `editId`, and `revision` are accepted from older links but never needed.
 */
export function ClipDetail({
  candidateId,
}: {
  candidateId: string
  projectId?: string | null
  editId?: string | null
  revision?: number | null
}) {
  const { active } = useWorkspaceScope()
  const detail = useQuery<ClipDetailResponse, ApiError>({
    queryKey: ['/api/v1/clips/detail', active.id, candidateId],
    queryFn: ({ signal }) =>
      showClipApiV1ClipsCandidateIdGet(candidateId, { workspace_id: active.id }, { signal }),
    retry: false,
  })

  if (detail.isPending) {
    return <LoadingState label="Loading clip…" variant="cards" count={1} />
  }
  if (detail.isError) {
    return (
      <div className="space-y-4">
        <PageHeader title="Clip" crumbs={[{ href: '/dashboard/clips', label: 'Clips' }]} />
        <ErrorNotice error={detail.error} />
      </div>
    )
  }

  return <ResolvedClip detail={detail.data} />
}

function ResolvedClip({ detail }: { detail: ClipDetailResponse }) {
  const { active } = useWorkspaceScope()
  const { candidate, project, edits, exports } = detail
  const edit = edits[0] ?? null

  return (
    <article className="space-y-8">
      <PageHeader
        title={candidate.hook}
        crumbs={[
          { href: '/dashboard/projects', label: 'Projects' },
          { href: `/dashboard/projects/${project.id}`, label: project.name },
        ]}
        meta={
          <>
            <StatusBadge tone={exports.length > 0 ? 'success' : edit === null ? 'neutral' : 'accent'}>
              {exports.length > 0 ? 'Exported' : edit === null ? 'Suggested' : 'In editing'}
            </StatusBadge>
            <span className="text-sm text-muted-foreground">
              {formatDuration(candidate.durationMs)} · Ranked #{candidate.rank}
            </span>
          </>
        }
        actions={<EditAction projectId={project.id} candidateId={candidate.id} editId={edit?.id ?? null} />}
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section aria-label="Preview" className="surface space-y-3 p-4">
          <ClipPreview projectId={project.id} startMs={candidate.startMs} endMs={candidate.endMs} />
        </section>
        <section aria-label="Why this moment" className="surface space-y-3 p-5">
          <h2 className="text-sm font-semibold">Why this moment</h2>
          <p className="text-sm text-muted-foreground">{candidate.reason}</p>
          <blockquote className="border-l-2 border-primary/40 pl-3 text-sm">
            {candidate.transcriptExcerpt}
          </blockquote>
          {candidate.contextWarnings.length === 0 ? null : (
            <div role="group" aria-label="Context warnings" className="rounded-lg bg-warning-soft p-3 text-xs text-warning">
              <p className="font-semibold">Check before publishing</p>
              <ul className="mt-1 space-y-1">
                {candidate.contextWarnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </div>

      <Section title="Exports" description="Files made from this clip. Download them or send them to a connected account.">
        {exports.length === 0 ? (
          <EmptyState
            compact
            title="No exports yet"
            description={
              edit === null
                ? 'Edit this clip, then choose Export in the editor.'
                : 'Open the editor and choose Export to make a file.'
            }
          />
        ) : (
          <ul aria-label="Exports" className="surface divide-y">
            {exports.map((entry) => (
              <ExportRow key={entry.id} entry={entry} showProject={false} />
            ))}
          </ul>
        )}
      </Section>

      {edit === null ? null : (
        <Section title="Revision history" description="Every saved version of this edit. The newest is used for exports.">
          <RevisionHistory editId={edit.id} />
        </Section>
      )}

      <div className="space-y-3">
        <Disclosure title="B-roll in this clip">
          <BrollProvenanceList projectId={project.id} candidateId={candidate.id} />
        </Disclosure>
        <Disclosure title="Variants">
          <VariantLab
            projectId={project.id}
            candidateId={candidate.id}
            workspaceId={active.id}
            proxyUrl={`/api/v1/projects/${project.id}/proxy?workspace_id=${active.id}`}
          />
        </Disclosure>
        <Disclosure title="Sources and evidence">
          <EvidencePanel projectId={project.id} candidateId={candidate.id} workspaceId={active.id} />
        </Disclosure>
        <Disclosure title="Campaign copy">
          {edit === null ? (
            <p className="text-sm text-muted-foreground">
              Campaign copy is written from one saved cut. Edit this clip first, then come back
              to write copy for it.
            </p>
          ) : (
            <CampaignPanel editId={edit.id} revision={edit.currentRevision} />
          )}
        </Disclosure>
      </div>
    </article>
  )
}

/** Open the clip's Edit, creating it the first time — the backend converges repeats. */
function EditAction({
  projectId,
  candidateId,
  editId,
}: {
  projectId: string
  candidateId: string
  editId: string | null
}) {
  const { active } = useWorkspaceScope()
  const router = useRouter()
  const open = useMutation<EditResponse, ApiError>({
    mutationFn: () =>
      createApiV1ProjectsProjectIdCandidatesCandidateIdEditsPost(
        projectId,
        candidateId,
        { templateId: null, brandKitId: null },
        { workspace_id: active.id },
      ),
    onSuccess: (created) => router.push(`/editor/${created.id}`),
  })

  if (editId !== null) {
    return (
      <Link
        href={`/editor/${editId}`}
        className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
      >
        <Scissors aria-hidden="true" className="size-4" />
        Continue editing
      </Link>
    )
  }
  if (!mayWriteProjects(active.role)) {
    return null
  }
  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={() => open.mutate()}
        disabled={open.isPending}
        className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-50"
      >
        <Scissors aria-hidden="true" className="size-4" />
        {open.isPending ? 'Opening the editor…' : 'Edit clip'}
      </button>
      {open.isError ? <ErrorNotice error={open.error} /> : null}
    </div>
  )
}

function RevisionHistory({ editId }: { editId: string }) {
  const { active } = useWorkspaceScope()
  const history = useQuery<RevisionHistoryResponse, ApiError>({
    queryKey: ['/api/v1/edit-revisions', active.id, editId],
    queryFn: ({ signal }) =>
      historyApiV1EditsEditIdRevisionsGet(editId, { workspace_id: active.id }, { signal }),
    retry: false,
  })

  if (history.isPending) {
    return <LoadingState label="Loading revisions…" />
  }
  if (history.isError) {
    return <ErrorNotice error={history.error} />
  }
  return (
    <ol aria-label="Revisions" className="surface divide-y">
      {history.data.revisions.map((revision, index) => (
        <li key={revision.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
          <span className="font-medium">Revision {revision.revision}</span>
          <span className="text-xs text-muted-foreground">
            {index === 0 ? 'Current · ' : ''}
            {new Date(revision.createdAt).toLocaleString()}
          </span>
        </li>
      ))}
    </ol>
  )
}

function Disclosure({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="surface group px-5 py-3">
      <summary className="cursor-pointer text-sm font-semibold">{title}</summary>
      <div className="pt-4">{children}</div>
    </details>
  )
}
