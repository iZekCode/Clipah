import type { StatusTone } from '@/components/status-badge'
import { ProjectStatus } from '@/lib/api/generated/model'

/**
 * What each backend Project status means to the person waiting on it.
 *
 * The labels name the stage the work is actually in, so a Project that is still being
 * transcribed never looks finished.
 */
const PROJECT_STATUS_LABELS: Record<string, string> = {
  [ProjectStatus.created]: 'Waiting for a video',
  [ProjectStatus.uploading]: 'Uploading',
  [ProjectStatus.ingesting]: 'Preparing video',
  [ProjectStatus.transcribing]: 'Transcribing',
  [ProjectStatus.analyzing]: 'Finding moments',
  [ProjectStatus.ready]: 'Ready to review',
  [ProjectStatus.failed]: 'Failed',
  [ProjectStatus.archived]: 'Deleted',
}

const PROJECT_STATUS_TONES: Record<string, StatusTone> = {
  [ProjectStatus.created]: 'attention',
  [ProjectStatus.uploading]: 'progress',
  [ProjectStatus.ingesting]: 'progress',
  [ProjectStatus.transcribing]: 'progress',
  [ProjectStatus.analyzing]: 'progress',
  [ProjectStatus.ready]: 'success',
  [ProjectStatus.failed]: 'danger',
  [ProjectStatus.archived]: 'neutral',
}

/**
 * Render one Project status, falling back to the raw status the backend reported.
 *
 * `uploading` covers both ways a video arrives, so the Project's source says which: a
 * YouTube link is imported, a file is uploaded.
 */
export function projectStatusLabel(status: string, sourceKind?: string): string {
  if (status === ProjectStatus.uploading && sourceKind === 'public_url') {
    return 'Importing'
  }
  return PROJECT_STATUS_LABELS[status] ?? status
}

/** How loudly one Project status should be shown. */
export function projectStatusTone(status: string): StatusTone {
  return PROJECT_STATUS_TONES[status] ?? 'neutral'
}

/** Whether the pipeline is still working on this Project. */
export function projectIsProcessing(status: string): boolean {
  return (
    status === ProjectStatus.uploading ||
    status === ProjectStatus.ingesting ||
    status === ProjectStatus.transcribing ||
    status === ProjectStatus.analyzing
  )
}
