'use client'

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { listCollectionApiV1BrandKitsGet } from '@/lib/api/generated/brand-kits/brand-kits'
import type {
  BrandKitListResponse,
  CandidateResponse,
  TemplateListResponse,
} from '@/lib/api/generated/model'
import { listCollectionApiV1TemplatesGet } from '@/lib/api/generated/templates/templates'

import { ScoreBars } from './ScoreBars'
import { useOpenEdit } from './use-open-edit'

const HEADING = 'text-caption font-medium uppercase tracking-wide text-subtle-foreground'

/**
 * Everything a reviewer needs to disagree with the analysis, one click behind the card.
 *
 * Every field here came from a language model reading someone else's words, so all of it
 * renders as text. The score is never shown here on its own: the dimensions behind it sit
 * with it, because a number a reviewer cannot argue with is not an explanation.
 */
export function WhyThisMoment({
  candidate,
  open,
  onOpenChange,
}: {
  candidate: CandidateResponse
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { active } = useWorkspaceScope()
  const [templateId, setTemplateId] = useState('')
  const [brandKitId, setBrandKitId] = useState('')
  const edit = useOpenEdit(candidate)

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Why this moment</SheetTitle>
          <SheetDescription>{candidate.hook}</SheetDescription>
        </SheetHeader>
        <div className="mt-6 space-y-6">
          <section className="space-y-2">
            <h3 className={HEADING}>Why it was chosen</h3>
            <p className="text-small">{candidate.reason}</p>
          </section>
          <section className="space-y-2">
            <h3 className={HEADING}>How it lands</h3>
            <p className="text-small">{candidate.payoff}</p>
          </section>
          <blockquote className="border-l-2 border-line-strong pl-3 text-small text-muted-foreground">
            {candidate.transcriptExcerpt}
          </blockquote>
          <section className="space-y-3">
            <h3 className={HEADING}>Score</h3>
            <ScoreBars breakdown={candidate.scoreBreakdown} />
          </section>
          {candidate.contextDependencies.length === 0 ? null : (
            <ul
              aria-label="Context this clip depends on"
              className="space-y-1 text-small text-muted-foreground"
            >
              {candidate.contextDependencies.map((dependency) => (
                <li key={dependency}>{dependency}</li>
              ))}
            </ul>
          )}
          {candidate.visualOpportunities.length === 0 ? null : (
            <ul aria-label="Visual opportunities" className="space-y-1 text-small text-muted-foreground">
              {candidate.visualOpportunities.map((opportunity) => (
                <li key={opportunity}>{opportunity}</li>
              ))}
            </ul>
          )}
          {candidate.tags.length === 0 ? null : (
            <ul aria-label="Tags" className="flex flex-wrap gap-1.5">
              {candidate.tags.map((tag) => (
                <li
                  key={tag}
                  className="rounded-sm border border-line-strong px-1.5 py-0.5 font-mono text-caption"
                >
                  {tag}
                </li>
              ))}
            </ul>
          )}
          <section className="space-y-3">
            <h3 className={HEADING}>Open with a look</h3>
            <div className="flex flex-wrap items-center gap-3">
              <LookSelection
                workspaceId={active.id}
                templateId={templateId}
                brandKitId={brandKitId}
                onTemplate={setTemplateId}
                onBrandKit={setBrandKitId}
              />
            </div>
            <Button
              loading={edit.isPending}
              onClick={() =>
                edit.open({
                  templateId: templateId === '' ? null : templateId,
                  brandKitId: brandKitId === '' ? null : brandKitId,
                })
              }
            >
              Edit clip
            </Button>
            {edit.error === null ? null : <ErrorNotice error={edit.error} />}
          </section>
        </div>
      </SheetContent>
    </Sheet>
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
        <label className="flex items-center gap-1.5 text-caption">
          <span className="text-muted-foreground">Look</span>
          <Select
            controlSize="sm"
            value={templateId}
            onChange={(event) => onTemplate(event.target.value)}
          >
            <option value="">None</option>
            {looks.map((look) => (
              <option key={look.id} value={look.id}>
                {look.name} (v{look.version})
              </option>
            ))}
          </Select>
        </label>
      )}
      {brands.length === 0 ? null : (
        <label className="flex items-center gap-1.5 text-caption">
          <span className="text-muted-foreground">Brand</span>
          <Select
            controlSize="sm"
            value={brandKitId}
            onChange={(event) => onBrandKit(event.target.value)}
          >
            <option value="">None</option>
            {brands.map((kit) => (
              <option key={kit.id} value={kit.id}>
                {kit.name} (v{kit.version})
              </option>
            ))}
          </Select>
        </label>
      )}
    </>
  )
}
