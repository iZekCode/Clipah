'use client'

import { AlertTriangle, Crosshair } from 'lucide-react'
import Link from 'next/link'

import { ErrorNotice } from '@/components/error-notice'
import { MediaCard } from '@/components/media-card'
import { Poster } from '@/components/media/poster'
import { Button } from '@/components/ui/button'
import { IconButton } from '@/components/ui/icon-button'
import type { CandidateResponse } from '@/lib/api/generated/model'
import { cn } from '@/lib/utils'

import { useOpenEdit } from './use-open-edit'

/** Where one moment is looked at in full: review mode, opened on that moment. */
export function reviewHref(candidate: Pick<CandidateResponse, 'id' | 'projectId'>): string {
  return `/dashboard/projects/${candidate.projectId}/review?moment=${candidate.id}`
}

/**
 * One proposed moment in a Project's grid: its picture, and a way to edit or view it.
 *
 * The reasons, the score, and the transcript live in review mode, one click away. A context
 * warning is the exception: it is flagged on the picture, because a reviewer must not be able
 * to miss that cutting here could mislead.
 */
export function ClipCard({
  candidate,
  selected = false,
  onSelect,
  posterUrl,
}: {
  candidate: CandidateResponse
  /** The moment's sharp poster, once it has been drawn. */
  posterUrl?: string
  selected?: boolean
  onSelect?: () => void
}) {
  // One clip has one Edit: the backend converges a repeated request on the one it created.
  const edit = useOpenEdit(candidate)
  const warned = candidate.contextWarnings.length > 0

  return (
    <li
      aria-current={selected ? 'true' : undefined}
      className={cn('rounded-lg', selected && 'ring-1 ring-primary')}
    >
      <MediaCard
        href={reviewHref(candidate)}
        title={candidate.hook}
        hideTitle
        aspect="portrait"
        // The warning sits below the picture: the hook fills the picture's lower edge.
        subtitle={
          warned ? (
            <span
              title={candidate.contextWarnings.join(' ')}
              className="inline-flex items-center gap-1 rounded-sm bg-warning-soft px-1.5 py-0.5 text-caption font-medium text-warning"
            >
              <AlertTriangle aria-hidden="true" strokeWidth={1.75} className="size-3.5" />
              Check context
            </span>
          ) : undefined
        }
        thumbnail={
          <Poster
            projectId={candidate.projectId}
            startMs={candidate.startMs}
            endMs={candidate.endMs}
            aspect="portrait"
            rank={candidate.rank}
            score={candidate.score}
            durationMs={candidate.durationMs}
            hook={candidate.hook}
            fullHook
            cornerReserved={onSelect !== undefined}
            largeBadges
            imageUrl={posterUrl}
            hoverOverlay
          />
        }
        corner={
          onSelect === undefined ? undefined : (
            <IconButton
              label="Show in source"
              icon={<Crosshair strokeWidth={1.75} />}
              variant="secondary"
              size="sm"
              onClick={onSelect}
            />
          )
        }
        footer={
          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-2">
              <Button size="sm" loading={edit.isPending} onClick={() => edit.open()}>
                Edit clip
              </Button>
              <Button size="sm" variant="secondary" asChild>
                <Link href={reviewHref(candidate)}>View clip</Link>
              </Button>
            </div>
            {edit.error === null ? null : <ErrorNotice error={edit.error} />}
            {/* Last, so it adds no gap: the poster is decoration to assistive technology. */}
            <span className="sr-only">Score {Math.round(candidate.score * 100)}</span>
          </div>
        }
      />
    </li>
  )
}
