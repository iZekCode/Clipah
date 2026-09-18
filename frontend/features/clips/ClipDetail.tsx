'use client'

import { useMutation, useQuery } from '@tanstack/react-query'
import { Scissors } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { TabList, TabPanel, useUrlTab } from '@/components/url-tabs'
import { BrollProvenanceList } from '@/features/broll/BrollProvenanceList'
import { CampaignPanel } from '@/features/campaigns/CampaignPanel'
import { ExportRow, ReadyActions } from '@/features/exports/export-list'
import { mayWriteProjects } from '@/features/workspaces/roles'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import {
  createApiV1ProjectsProjectIdCandidatesCandidateIdEditsPost,
  historyApiV1EditsEditIdRevisionsGet,
} from '@/lib/api/generated/edits/edits'
import type {
  ClipDetailResponse,
  EditResponse,
  RevisionHistoryResponse,
} from '@/lib/api/generated/model'
import { showClipApiV1ClipsCandidateIdGet } from '@/lib/api/generated/studio/studio'
import { formatClock } from '@/lib/media/time'
import { cn } from '@/lib/utils'

import { ClipPreview } from './ClipPreview'
import { EvidencePanel } from './EvidencePanel'
import { VariantLab } from './VariantLab'

const SECTIONS = [
  { id: 'exports', label: 'Exports' },
  { id: 'revisions', label: 'Revisions' },
  { id: 'broll', label: 'B-roll' },
  { id: 'variants', label: 'Variants' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'campaign', label: 'Campaign copy' },
] as const
type SectionId = (typeof SECTIONS)[number]['id']
const SECTION_IDS = SECTIONS.map((entry) => entry.id)

/**
 * One clip, seen from outside the editor, resolved from nothing but its own identifier.
 *
 * A link to a clip carries one UUID; the backend answers which Project it belongs to,
 * which Edits were opened from it, and which files were exported. From here a creator can
 * preview it, continue editing, download or publish an export, and — one tab away — look
 * at its revisions, B-roll, variants, the evidence behind its claims, and its campaign copy.
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
  const [section, choose] = useUrlTab<SectionId>(SECTION_IDS, 'exports')
  const ready = exports.find((entry) => entry.status === 'ready' && entry.renderId !== null)

  return (
    <article className="space-y-8">
      <div className="grid gap-8 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
        <section
          aria-label="Preview"
          className="relative aspect-[9/16] overflow-hidden rounded-lg bg-stage"
        >
          <ClipPreview
            projectId={project.id}
            startMs={candidate.startMs}
            endMs={candidate.endMs}
            className="absolute inset-0 size-full object-contain"
          />
        </section>
        <div className="min-w-0 space-y-6">
          <PageHeader
            title={candidate.hook}
            crumbs={[
              { href: '/dashboard/projects', label: 'Projects' },
              { href: `/dashboard/projects/${project.id}`, label: project.name },
            ]}
            meta={
              <>
                <StatusBadge
                  tone={exports.length > 0 ? 'success' : edit === null ? 'neutral' : 'accent'}
                >
                  {exports.length > 0 ? 'Exported' : edit === null ? 'Suggested' : 'In editing'}
                </StatusBadge>
                <span className="font-mono text-caption text-muted-foreground">
                  {formatClock(candidate.durationMs)} · Ranked #{candidate.rank}
                </span>
              </>
            }
            actions={
              <EditAction
                projectId={project.id}
                candidateId={candidate.id}
                editId={edit?.id ?? null}
              />
            }
          />
          <section aria-label="Why this moment" className="space-y-3">
            <h2 className="text-title">Why this moment</h2>
            <p className="text-body text-muted-foreground">{candidate.reason}</p>
            <blockquote className="border-l-2 border-primary pl-3 text-body">
              {candidate.transcriptExcerpt}
            </blockquote>
            {candidate.contextWarnings.length === 0 ? null : (
              <div
                role="group"
                aria-label="Context warnings"
                className="rounded-lg bg-warning-soft p-3 text-small text-warning"
              >
                <p className="font-semibold">Check before publishing</p>
                <ul className="mt-1 space-y-1">
                  {candidate.contextWarnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            )}
          </section>
          {ready === undefined || ready.renderId === null ? null : (
            <ReadyActions entry={ready} renderId={ready.renderId} />
          )}
        </div>
      </div>

      <div>
        <TabList
          label="Clip sections"
          tabs={SECTIONS}
          active={section}
          onChoose={choose}
          idPrefix="clip"
        />
        <TabPanel idPrefix="clip" id="exports" active={section}>
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
            <ul aria-label="Exports" className="divide-y rounded-lg border bg-card">
              {exports.map((entry) => (
                <ExportRow key={entry.id} entry={entry} showProject={false} />
              ))}
            </ul>
          )}
        </TabPanel>
        <TabPanel idPrefix="clip" id="revisions" active={section}>
          {edit === null ? (
            <EmptyState
              compact
              title="No revisions yet"
              description="Edit this clip to save its first version."
            />
          ) : (
            <RevisionHistory editId={edit.id} />
          )}
        </TabPanel>
        <TabPanel idPrefix="clip" id="broll" active={section}>
          <BrollProvenanceList projectId={project.id} candidateId={candidate.id} />
        </TabPanel>
        <TabPanel idPrefix="clip" id="variants" active={section}>
          <VariantLab
            projectId={project.id}
            candidateId={candidate.id}
            workspaceId={active.id}
            proxyUrl={`/api/v1/projects/${project.id}/proxy?workspace_id=${active.id}`}
          />
        </TabPanel>
        <TabPanel idPrefix="clip" id="evidence" active={section}>
          <EvidencePanel
            projectId={project.id}
            candidateId={candidate.id}
            workspaceId={active.id}
          />
        </TabPanel>
        <TabPanel idPrefix="clip" id="campaign" active={section}>
          {edit === null ? (
            <p className="text-small text-muted-foreground">
              Campaign copy is written from one saved cut. Edit this clip first, then come back
              to write copy for it.
            </p>
          ) : (
            <CampaignPanel editId={edit.id} revision={edit.currentRevision} />
          )}
        </TabPanel>
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
      <Button asChild size="lg">
        <Link href={`/editor/${editId}`}>
          <Scissors aria-hidden="true" />
          Continue editing
        </Link>
      </Button>
    )
  }
  if (!mayWriteProjects(active.role)) {
    return null
  }
  return (
    <div className="space-y-2">
      <Button type="button" size="lg" onClick={() => open.mutate()} disabled={open.isPending}>
        <Scissors aria-hidden="true" />
        {open.isPending ? 'Opening the editor…' : 'Edit clip'}
      </Button>
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
    <ol aria-label="Revisions" className="relative space-y-4 border-l border-line-strong pl-4">
      {history.data.revisions.map((revision, index) => (
        <li
          key={revision.id}
          className={cn(
            'relative flex flex-wrap items-baseline justify-between gap-3 text-small',
            'before:absolute before:-left-[21px] before:top-1.5 before:size-2 before:rounded-full',
            index === 0 ? 'before:bg-primary' : 'before:bg-line-strong',
          )}
        >
          <span className="font-semibold">Revision {revision.revision}</span>
          <span className="font-mono text-caption text-muted-foreground">
            {index === 0 ? 'Current · ' : ''}
            {new Date(revision.createdAt).toLocaleString()}
          </span>
        </li>
      ))}
    </ol>
  )
}
