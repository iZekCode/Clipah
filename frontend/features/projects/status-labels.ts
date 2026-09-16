import type { StatusTone } from '@/components/status-badge'
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

/** Render one Project status, falling back to the raw status the backend reported. */
export function projectStatusLabel(status: string): string {
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

/** The next useful thing to do with a Project, decided only by its durable state. */
export interface NextStep {
  title: string
  description: string
  action: 'add-media' | 'follow' | 'review' | 'retry'
}

export function projectNextStep(status: string): NextStep {
  if (status === ProjectStatus.created) {
    return {
      title: 'Add your video',
      description: 'Upload a file or import a public YouTube link to get started.',
      action: 'add-media',
    }
  }
  if (status === ProjectStatus.failed) {
    return {
      title: 'Processing stopped',
      description: 'Something went wrong with this video. Add it again to retry.',
      action: 'retry',
    }
  }
  if (status === ProjectStatus.ready) {
    return {
      title: 'Choose your moments',
      description: 'Review the suggested moments and open the ones you like in the editor.',
      action: 'review',
    }
  }
  return {
    title: 'Processing your video',
    description: 'You can leave this page — the work continues and shows up here when it is done.',
    action: 'follow',
  }
}
