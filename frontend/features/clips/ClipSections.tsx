'use client'

import { useQuery } from '@tanstack/react-query'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { TabList, TabPanel, useUrlTab } from '@/components/url-tabs'
import { BrollProvenanceList } from '@/features/broll/BrollProvenanceList'
import { CampaignPanel } from '@/features/campaigns/CampaignPanel'
import { ExportRow, ReadyActions } from '@/features/exports/export-list'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { historyApiV1EditsEditIdRevisionsGet } from '@/lib/api/generated/edits/edits'
import type {
  ClipDetailResponse,
  ProxyPlaybackResponse,
  RevisionHistoryResponse,
} from '@/lib/api/generated/model'
import { showApiV1ProjectsProjectIdProxyGet } from '@/lib/api/generated/playback/playback'
import { showClipApiV1ClipsCandidateIdGet } from '@/lib/api/generated/studio/studio'
import { cn } from '@/lib/utils'

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
 * Everything a clip has gathered on its way to a published file, one tab per kind.
 *
 * It resolves the clip from its identifier alone — its Project, its Edit, and its exports —
 * so review mode can show it for whichever moment is on screen.
 */
export function ClipSections({ candidateId }: { candidateId: string }) {
  const { active } = useWorkspaceScope()
  const detail = useQuery<ClipDetailResponse, ApiError>({
    queryKey: ['/api/v1/clips/detail', active.id, candidateId],
    queryFn: ({ signal }) =>
      showClipApiV1ClipsCandidateIdGet(candidateId, { workspace_id: active.id }, { signal }),
    retry: false,
  })

  if (detail.isPending) {
    return <LoadingState label="Loading clip…" />
  }
  if (detail.isError) {
    return <ErrorNotice error={detail.error} />
  }
  return <ResolvedSections detail={detail.data} />
}

function ResolvedSections({ detail }: { detail: ClipDetailResponse }) {
  const { active } = useWorkspaceScope()
  const { candidate, project, edits, exports } = detail
  const edit = edits[0] ?? null
  const [section, choose] = useUrlTab<SectionId>(SECTION_IDS, 'exports')
  const ready = exports.find((entry) => entry.status === 'ready' && entry.renderId !== null)

  return (
    <div className="space-y-4">
      {ready === undefined || ready.renderId === null ? null : (
        <ReadyActions entry={ready} renderId={ready.renderId} />
      )}
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
          <VariantsTab projectId={project.id} candidateId={candidate.id} />
        </TabPanel>
        <TabPanel idPrefix="clip" id="evidence" active={section}>
          <EvidencePanel projectId={project.id} candidateId={candidate.id} workspaceId={active.id} />
        </TabPanel>
        <TabPanel idPrefix="clip" id="campaign" active={section}>
          {edit === null ? (
            <p className="text-small text-muted-foreground">
              Campaign copy is written from one saved cut. Edit this clip first, then come back to
              write copy for it.
            </p>
          ) : (
            <CampaignPanel editId={edit.id} revision={edit.currentRevision} />
          )}
        </TabPanel>
      </div>
    </div>
  )
}

/**
 * Variants over the Project's proxy, played from its signed URL.
 *
 * The proxy route answers with a short-lived capability, not the media itself, so the player
 * needs the URL inside that answer. An expired one is asked for again when the player fails.
 */
function VariantsTab({ projectId, candidateId }: { projectId: string; candidateId: string }) {
  const { active } = useWorkspaceScope()
  const playback = useQuery<ProxyPlaybackResponse, ApiError>({
    queryKey: ['/api/v1/projects/proxy', active.id, projectId],
    queryFn: ({ signal }) =>
      showApiV1ProjectsProjectIdProxyGet(projectId, { workspace_id: active.id }, { signal }),
    retry: false,
    gcTime: 0,
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
  return (
    <VariantLab
      projectId={projectId}
      candidateId={candidateId}
      workspaceId={active.id}
      proxyUrl={playback.data?.url ?? null}
      onPlaybackError={() => void playback.refetch()}
    />
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
