'use client'

import { useMutation, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
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
    <li className="surface flex flex-col gap-4 p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="rounded-full bg-accent px-2 py-0.5 font-semibold text-accent-foreground">
              #{candidate.rank}
            </span>
            <span>{CATEGORY_LABELS[candidate.category] ?? candidate.category}</span>
            <span aria-hidden="true">·</span>
            <span>{formatDuration(candidate.durationMs)}</span>
          </div>
          <h3 className="text-base font-semibold leading-snug">{candidate.hook}</h3>
          <p className="text-sm text-muted-foreground">{candidate.reason}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="text-2xl font-semibold tabular-nums" aria-label="Score">
            {Math.round(candidate.score * 100)}
          </p>
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">Score</p>
        </div>
      </div>

      {candidate.contextWarnings.length === 0 ? null : (
        <div
          role="group"
          aria-label="Context warnings"
          className="rounded-lg border border-warning/30 bg-warning-soft p-3"
        >
          <p className="text-xs font-semibold text-warning">Check before publishing</p>
          <ul className="mt-1 space-y-1 text-xs text-warning">
            {candidate.contextWarnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={open.isPending}
          onClick={() => open.mutate()}
          className="inline-flex h-9 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-50"
        >
          {open.isPending ? 'Opening the editor…' : 'Edit clip'}
        </button>
        <button
          type="button"
          aria-expanded={previewing}
          onClick={() => setPreviewing((current) => !current)}
          className="inline-flex h-9 items-center rounded-lg border bg-card px-3 text-sm font-medium hover:bg-secondary"
        >
          {previewing ? 'Hide preview' : 'Preview clip'}
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

      <details className="group rounded-lg border bg-secondary/30 px-3 py-2">
        <summary className="cursor-pointer text-sm font-medium text-muted-foreground hover:text-foreground">
          Details, scores, and look
        </summary>
        <div className="space-y-4 pb-2 pt-3">
          <div className="space-y-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">How it lands</p>
            <p className="text-sm">{candidate.payoff}</p>
          </div>
          <blockquote className="border-l-2 border-primary/40 pl-3 text-sm text-muted-foreground">
            {candidate.transcriptExcerpt}
          </blockquote>

          {candidate.tags.length === 0 ? null : (
            <ul aria-label="Tags" className="flex flex-wrap gap-1">
              {candidate.tags.map((tag) => (
                <li key={tag} className="rounded-full border bg-card px-2 py-0.5 text-xs">
                  {tag}
                </li>
              ))}
            </ul>
          )}

          <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
            {SCORE_DIMENSIONS.map(([label, key]) => (
              <div key={key} className="flex justify-between gap-2">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="tabular-nums">{Math.round(candidate.scoreBreakdown[key] * 100)}</dd>
              </div>
            ))}
          </dl>

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

          <div className="flex flex-wrap items-center gap-3">
            <LookSelection
              workspaceId={active.id}
              templateId={templateId}
              brandKitId={brandKitId}
              onTemplate={setTemplateId}
              onBrandKit={setBrandKitId}
            />
          </div>
          <Link
            href={`/dashboard/clips/${candidate.id}`}
            className="inline-block text-sm font-medium text-primary hover:underline"
          >
            Open clip page
          </Link>
        </div>
      </details>
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
            className="h-8 rounded-lg border bg-card px-2"
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
            className="h-8 rounded-lg border bg-card px-2"
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
