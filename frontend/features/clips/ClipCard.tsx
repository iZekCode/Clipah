'use client'

import { useMutation, useQuery } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { listCollectionApiV1BrandKitsGet } from '@/lib/api/generated/brand-kits/brand-kits'
import { createApiV1ProjectsProjectIdCandidatesCandidateIdEditsPost } from '@/lib/api/generated/edits/edits'
import type {
  BrandKitListResponse,
  CandidateResponse,
  EditResponse,
  TemplateListResponse,
} from '@/lib/api/generated/model'
import { listCollectionApiV1TemplatesGet } from '@/lib/api/generated/templates/templates'

import { ClipPreview } from './ClipPreview'

/** What each analysis category is called on a page a person reads. */
const CATEGORY_LABELS: Record<string, string> = {
  story: 'Story',
  insight: 'Insight',
  how_to: 'How-to',
  opinion: 'Opinion',
  question_answer: 'Question and answer',
  humour: 'Humour',
  data: 'Data',
  announcement: 'Announcement',
}

/** The seven dimensions behind a score, in the order the analysis reasons about them. */
const SCORE_DIMENSIONS = [
  ['Hook', 'hook'],
  ['Payoff', 'payoff'],
  ['Narrative completeness', 'narrativeCompleteness'],
  ['Context safety', 'contextSafety'],
  ['Platform fit', 'platformFit'],
  ['Transcript confidence', 'transcriptConfidence'],
  ['Visual opportunity', 'visualOpportunity'],
] as const

/**
 * One proposed moment, with the evidence a reviewer needs to disagree with it.
 *
 * Every field here came from a language model reading someone else's words, so all of it
 * is rendered as text. The score is never shown on its own: the dimensions behind it and
 * the warnings against it sit next to it, because a number a reviewer cannot argue with
 * is not an explanation.
 */
export function ClipCard({ candidate }: { candidate: CandidateResponse }) {
  const [previewing, setPreviewing] = useState(false)
  const [templateId, setTemplateId] = useState('')
  const [brandKitId, setBrandKitId] = useState('')
  const { active } = useWorkspaceScope()
  const router = useRouter()

  // One clip has one Edit: the backend converges a repeated request on the Edit it
  // already created, so a second click opens the same work rather than a rival copy.
  const open = useMutation<EditResponse, ApiError, void>({
    mutationFn: () =>
      createApiV1ProjectsProjectIdCandidatesCandidateIdEditsPost(
        candidate.projectId,
        candidate.id,
        // The look and the brand are chosen once, here, and the Revision records the exact
        // versions it was built from. Choosing neither is the ordinary case.
        {
          templateId: templateId === '' ? null : templateId,
          brandKitId: brandKitId === '' ? null : brandKitId,
        },
        { workspace_id: active.id },
      ),
    onSuccess: (created) => {
      router.push(`/editor/${created.id}?workspace_id=${active.id}`)
    },
  })

  return (
    <li className="space-y-3 px-4 py-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <p className="text-xs text-muted-foreground">
            #{candidate.rank} · {CATEGORY_LABELS[candidate.category] ?? candidate.category} ·{' '}
            {formatDuration(candidate.durationMs)}
          </p>
          <h3 className="text-base font-medium">{candidate.hook}</h3>
          <p className="text-sm">{candidate.payoff}</p>
        </div>
        <p className="shrink-0 text-sm font-semibold" aria-label="Score">
          {Math.round(candidate.score * 100)}
        </p>
      </div>

      <p className="text-sm text-muted-foreground">{candidate.reason}</p>
      <blockquote className="border-l-2 pl-3 text-sm text-muted-foreground">
        {candidate.transcriptExcerpt}
      </blockquote>

      {candidate.tags.length === 0 ? null : (
        <ul aria-label="Tags" className="flex flex-wrap gap-1">
          {candidate.tags.map((tag) => (
            <li key={tag} className="rounded-full border px-2 py-0.5 text-xs">
              {tag}
            </li>
          ))}
        </ul>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
        {SCORE_DIMENSIONS.map(([label, key]) => (
          <div key={key} className="flex justify-between gap-2">
            <dt className="text-muted-foreground">{label}</dt>
            <dd>{Math.round(candidate.scoreBreakdown[key] * 100)}</dd>
          </div>
        ))}
      </dl>

      {candidate.contextWarnings.length === 0 ? null : (
        <div
          role="group"
          aria-label="Context warnings"
          className="rounded-md border border-destructive/40 bg-destructive/10 p-2"
        >
          <ul className="space-y-1 text-xs text-destructive">
            {candidate.contextWarnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      {candidate.contextDependencies.length === 0 ? null : (
        <ul aria-label="Context this clip depends on" className="space-y-1 text-xs text-muted-foreground">
          {candidate.contextDependencies.map((dependency) => (
            <li key={dependency}>{dependency}</li>
          ))}
        </ul>
      )}

      {candidate.visualOpportunities.length === 0 ? null : (
        <ul aria-label="Visual opportunities" className="space-y-1 text-xs text-muted-foreground">
          {candidate.visualOpportunities.map((opportunity) => (
            <li key={opportunity}>{opportunity}</li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <LookSelection
          workspaceId={active.id}
          templateId={templateId}
          brandKitId={brandKitId}
          onTemplate={setTemplateId}
          onBrandKit={setBrandKitId}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          aria-expanded={previewing}
          onClick={() => setPreviewing((open) => !open)}
          className="rounded-md border px-3 py-1 text-sm"
        >
          Preview clip
        </button>
        <button
          type="button"
          disabled={open.isPending}
          onClick={() => open.mutate()}
          className="rounded-md border px-3 py-1 text-sm disabled:opacity-50"
        >
          {open.isPending ? 'Opening the editor…' : 'Edit this clip'}
        </button>
      </div>
      {open.isError ? <ErrorNotice error={open.error} /> : null}
      {previewing ? (
        <ClipPreview
          projectId={candidate.projectId}
          startMs={candidate.startMs}
          endMs={candidate.endMs}
        />
      ) : null}
    </li>
  )
}

/**
 * The look and the brand this clip will be opened with.
 *
 * Both lists are read best-effort: a Workspace that has published neither, or a read that
 * fails, must not stop somebody opening their own clip. Choosing nothing is the ordinary
 * case, and it is what the control starts on.
 */
function LookSelection({
  workspaceId,
  templateId,
  brandKitId,
  onTemplate,
  onBrandKit,
}: {
  workspaceId: string
  templateId: string
  brandKitId: string
  onTemplate: (value: string) => void
  onBrandKit: (value: string) => void
}) {
  const templates = useQuery<TemplateListResponse, ApiError>({
    queryKey: ['/api/v1/templates', workspaceId],
    queryFn: ({ signal }) =>
      listCollectionApiV1TemplatesGet({ workspace_id: workspaceId }, { signal }),
    retry: false,
  })
  const kits = useQuery<BrandKitListResponse, ApiError>({
    queryKey: ['/api/v1/brand-kits', workspaceId],
    queryFn: ({ signal }) =>
      listCollectionApiV1BrandKitsGet({ workspace_id: workspaceId }, { signal }),
    retry: false,
  })

  const looks = templates.data?.templates ?? []
  const brands = kits.data?.brandKits ?? []
  if (looks.length === 0 && brands.length === 0) {
    return null
  }

  return (
    <>
      {looks.length === 0 ? null : (
        <label className="flex items-center gap-1 text-xs">
          <span className="text-muted-foreground">Look</span>
          <select
            className="rounded-md border px-2 py-1"
            value={templateId}
            onChange={(event) => onTemplate(event.target.value)}
          >
            <option value="">None</option>
            {looks.map((look) => (
              <option key={look.id} value={look.id}>
                {look.name} (v{look.version})
              </option>
            ))}
          </select>
        </label>
      )}
      {brands.length === 0 ? null : (
        <label className="flex items-center gap-1 text-xs">
          <span className="text-muted-foreground">Brand</span>
          <select
            className="rounded-md border px-2 py-1"
            value={brandKitId}
            onChange={(event) => onBrandKit(event.target.value)}
          >
            <option value="">None</option>
            {brands.map((kit) => (
              <option key={kit.id} value={kit.id}>
                {kit.name} (v{kit.version})
              </option>
            ))}
          </select>
        </label>
      )}
    </>
  )
}

/** Render a length the way a reviewer reads one, as minutes and seconds. */
export function formatDuration(durationMs: number): string {
  const totalSeconds = Math.round(durationMs / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}
