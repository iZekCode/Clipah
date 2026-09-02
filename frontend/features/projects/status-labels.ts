import { ProjectStatus } from '@/lib/api/generated/model'

/**
 * What each backend Project status means to the person waiting on it.
 *
 * The labels name the stage the work is actually in, so a Project that is still being
 * transcribed never looks finished.
 */
const PROJECT_STATUS_LABELS: Record<string, string> = {
  [ProjectStatus.created]: 'Waiting for media',
  [ProjectStatus.uploading]: 'Uploading',
  [ProjectStatus.ingesting]: 'Importing',
  [ProjectStatus.transcribing]: 'Transcribing',
  [ProjectStatus.analyzing]: 'Finding moments',
  [ProjectStatus.ready]: 'Ready to review',
  [ProjectStatus.failed]: 'Failed',
  [ProjectStatus.archived]: 'Deleted',
}

/** Render one Project status, falling back to the raw status the backend reported. */
export function projectStatusLabel(status: string): string {
  return PROJECT_STATUS_LABELS[status] ?? status
}
