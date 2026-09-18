import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

const STAGES = ['Uploading', 'Importing', 'Transcribing', 'Finding moments'] as const

const KIND_STAGE: Record<string, number> = {
  source_import: 1,
  ingest: 1,
  transcribe: 2,
  analyze: 3,
}

/** Job kinds that are stages of the pipeline a member waits on. */
export const PIPELINE_KINDS: ReadonlySet<string> = new Set(Object.keys(KIND_STAGE))

/** Which stage one job kind is, or −1 for work that is not a pipeline stage. */
export function pipelineStageIndex(kind: string | null): number {
  return kind === null ? -1 : (KIND_STAGE[kind] ?? -1)
}

/**
 * The pipeline a video passes through, marked only from what was reported.
 *
 * The only number is the upload percentage the browser measures itself; a backend stage is
 * done, in progress, or not started, never a guessed fraction.
 */
export function StageBar({
  kind,
  status,
  uploadPercent = null,
  reconnecting = false,
  actions,
  className,
}: {
  kind: string | null
  status: string | null
  uploadPercent?: number | null
  reconnecting?: boolean
  actions?: ReactNode
  className?: string
}) {
  const reported = pipelineStageIndex(kind)
  const current = uploadPercent !== null ? 0 : reported
  const finished = status === 'succeeded' && reported === STAGES.length - 1

  return (
    <div className={cn('space-y-2', className)}>
      <ol aria-label="Processing stages" className="grid grid-cols-4 gap-1.5">
        {STAGES.map((label, index) => {
          const done = finished || index < current
          const active = !finished && index === current
          const state = done ? ' (done)' : active ? ' (in progress)' : ' (not started)'
          return (
            <li key={label} className="min-w-0 space-y-1.5">
              <span
                aria-hidden="true"
                className={cn(
                  'block h-1 rounded-full',
                  done
                    ? 'bg-primary'
                    : active
                      ? 'bg-foreground motion-safe:animate-pulse'
                      : 'bg-line-strong',
                )}
              />
              <span
                className={cn(
                  'block truncate text-caption',
                  active ? 'text-foreground' : 'text-muted-foreground',
                )}
              >
                {label}
                {index === 0 && active && uploadPercent !== null ? (
                  <span className="tabular font-mono"> {uploadPercent}%</span>
                ) : null}
                <span className="sr-only">{state}</span>
              </span>
            </li>
          )
        })}
      </ol>
      {reconnecting || actions !== undefined ? (
        <div className="flex flex-wrap items-center gap-3">
          {reconnecting ? (
            <p className="text-caption text-warning">Reconnecting to live updates…</p>
          ) : null}
          {actions}
        </div>
      ) : null}
    </div>
  )
}
