import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

const STAGE_COUNT = 4

/** The first stage is how the video arrives: uploaded from a file or imported from a link. */
function stageLabels(sourceKind: string | null): readonly string[] {
  return [
    sourceKind === 'public_url' ? 'Importing' : 'Uploading',
    'Preparing video',
    'Transcribing',
    'Finding moments',
  ]
}

const KIND_STAGE: Record<string, number> = {
  source_import: 0,
  ingest: 1,
  transcribe: 2,
  analyze: 3,
}

/** Where each Project status sits on the bar; `ready` is past the last stage. */
const STATUS_STAGE: Record<string, number> = {
  uploading: 0,
  ingesting: 1,
  transcribing: 2,
  analyzing: 3,
  ready: STAGE_COUNT,
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
  projectStatus = null,
  sourceKind = null,
  uploadPercent = null,
  reconnecting = false,
  actions,
  className,
}: {
  kind: string | null
  status: string | null
  /**
   * The Project's own status, which places the bar even before any job has reported, so
   * the bar never vanishes between one stage finishing and the next announcing itself.
   */
  projectStatus?: string | null
  sourceKind?: string | null
  uploadPercent?: number | null
  reconnecting?: boolean
  actions?: ReactNode
  className?: string
}) {
  const reported = pipelineStageIndex(kind)
  const fromProject = projectStatus === null ? -1 : (STATUS_STAGE[projectStatus] ?? -1)
  const current = uploadPercent !== null ? 0 : Math.max(reported, fromProject)
  const finished =
    current >= STAGE_COUNT || (status === 'succeeded' && reported === STAGE_COUNT - 1)

  return (
    <div className={cn('space-y-2', className)}>
      <ol aria-label="Processing stages" className="grid grid-cols-4 gap-1.5">
        {stageLabels(sourceKind).map((label, index) => {
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
