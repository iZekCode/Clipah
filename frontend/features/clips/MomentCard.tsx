'use client'

import { AlertTriangle, Crosshair } from 'lucide-react'
import Link from 'next/link'
import { useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { Poster } from '@/components/media/poster'
import { Button } from '@/components/ui/button'
import { IconButton } from '@/components/ui/icon-button'
import type { CandidateResponse } from '@/lib/api/generated/model'
import { formatClock } from '@/lib/media/time'
import { cn } from '@/lib/utils'

import { ClipPreview } from './ClipPreview'
import { useOpenEdit } from './use-open-edit'
import { WhyThisMoment } from './WhyThisMoment'

/** What each analysis category is called on a page a person reads. */
export const CATEGORY_LABELS: Record<string, string> = {
  story: 'Story',
  insight: 'Insight',
  how_to: 'How-to',
  opinion: 'Opinion',
  question_answer: 'Question and answer',
  humour: 'Humour',
  data: 'Data',
  announcement: 'Announcement',
}

/**
 * One proposed moment: its picture, its rank and score, its hook, and the warnings against it.
 *
 * The reasons and every score dimension sit one click away in "Why this moment"; context
 * warnings never do, because a reviewer must not be able to miss them. Every field here came
 * from a language model, so all of it renders as text.
 */
export function MomentCard({
  candidate,
  selected = false,
  onSelect,
}: {
  candidate: CandidateResponse
  selected?: boolean
  onSelect?: () => void
}) {
  const [previewing, setPreviewing] = useState(false)
  const [explaining, setExplaining] = useState(false)
  const edit = useOpenEdit(candidate)

  return (
    <li
      aria-current={selected ? 'true' : undefined}
      className={cn(
        'flex flex-col overflow-hidden rounded-lg border bg-card transition-colors duration-fast ease-signal',
        selected ? 'border-primary' : 'hover:border-line-strong',
      )}
    >
      <div className="relative aspect-[9/16] overflow-hidden bg-stage">
        {previewing ? (
          <ClipPreview
            projectId={candidate.projectId}
            startMs={candidate.startMs}
            endMs={candidate.endMs}
          />
        ) : (
          <Poster
            projectId={candidate.projectId}
            startMs={candidate.startMs}
            endMs={candidate.endMs}
            aspect="portrait"
            rank={candidate.rank}
            durationMs={candidate.durationMs}
          />
        )}
        {onSelect === undefined ? null : (
          <IconButton
            label="Show in source"
            icon={<Crosshair strokeWidth={1.75} />}
            variant="secondary"
            size="sm"
            onClick={onSelect}
            className="absolute bottom-2 right-2 z-10"
          />
        )}
      </div>
      <div className="flex flex-1 flex-col gap-3 p-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <p className="text-caption text-muted-foreground">
              {CATEGORY_LABELS[candidate.category] ?? candidate.category} ·{' '}
              <span className="tabular font-mono">{formatClock(candidate.durationMs)}</span>
            </p>
            <h3 className="text-small font-semibold leading-snug">{candidate.hook}</h3>
          </div>
          <p aria-label="Score" className="font-display tabular shrink-0 text-h2">
            {Math.round(candidate.score * 100)}
          </p>
        </div>
        <p className="line-clamp-2 text-caption text-muted-foreground">{candidate.reason}</p>
        {candidate.contextWarnings.length === 0 ? null : (
          <div
            role="group"
            aria-label="Context warnings"
            className="flex gap-2 rounded-md bg-warning-soft p-2 text-caption text-warning"
          >
            <AlertTriangle
              aria-hidden="true"
              strokeWidth={1.75}
              className="mt-0.5 size-3.5 shrink-0"
            />
            <ul className="space-y-0.5">
              {candidate.contextWarnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-auto flex flex-wrap items-center gap-2">
          <Button size="sm" loading={edit.isPending} onClick={() => edit.open()}>
            Edit clip
          </Button>
          <Button
            size="sm"
            variant="secondary"
            aria-expanded={previewing}
            onClick={() => setPreviewing((current) => !current)}
          >
            {previewing ? 'Hide preview' : 'Preview clip'}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setExplaining(true)}>
            Why this moment
          </Button>
        </div>
        {edit.error === null ? null : <ErrorNotice error={edit.error} />}
        <Link
          href={`/dashboard/clips/${candidate.id}`}
          className="text-caption font-medium text-muted-foreground hover:text-foreground"
        >
          Open clip page
        </Link>
      </div>
      <WhyThisMoment candidate={candidate} open={explaining} onOpenChange={setExplaining} />
    </li>
  )
}
