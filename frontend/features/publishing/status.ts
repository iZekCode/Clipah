import type { PublicationResponse } from '@/lib/api/generated/model'

import { providerLabel } from './gates'

/** The states one destination moves through, as the backend names them. */
export const CANCELLABLE_STATUSES = [
  'awaiting_approval',
  'scheduled',
  'preflighting',
  'retryable_failed',
  'reconnect_required',
] as const

const TERMINAL_STATUSES = ['published', 'permanent_failed', 'cancelled'] as const

/** What one destination's state is called where a member can read it. */
export function statusLabel(status: string, provider: string): string {
  const labels: Record<string, string> = {
    draft: 'Draft',
    awaiting_approval: 'Waiting for approval',
    scheduled: 'Scheduled',
    preflighting: 'Getting ready',
    transferring: `Sending to ${providerLabel(provider)}`,
    processing: `${providerLabel(provider)} is processing it`,
    published: 'Published',
    retryable_failed: 'Will retry',
    reconnect_required: 'Reconnect required',
    permanent_failed: 'Failed',
    cancelled: 'Cancelled',
  }
  return labels[status] ?? status
}

/** Whether this destination may still be stopped before it reaches its provider. */
export function mayCancel(publication: PublicationResponse): boolean {
  return (CANCELLABLE_STATUSES as readonly string[]).includes(publication.status)
}

/** Whether this destination is one the backend will accept a retry for. */
export function mayRetry(publication: PublicationResponse): boolean {
  return publication.status === 'retryable_failed'
}

/** Whether this destination is finished, whichever way it finished. */
export function isFinished(publication: PublicationResponse): boolean {
  return (TERMINAL_STATUSES as readonly string[]).includes(publication.status)
}

/** What a member should do about this destination, in their own words. */
export function actionableReason(publication: PublicationResponse, provider: string): string | null {
  if (publication.status === 'reconnect_required') {
    return `Clipah no longer has permission to post to this ${providerLabel(provider)} account.`
  }
  if (publication.sanitizedErrorMessage !== null) {
    return publication.sanitizedErrorMessage
  }
  if (publication.normalizedErrorCode !== null) {
    return `${providerLabel(provider)} refused this publication (${publication.normalizedErrorCode}).`
  }
  return null
}

/** One step of a destination's history, kept only where it actually happened. */
export interface TimelineStep {
  label: string
  instant: string
}

/** Every recorded moment of one destination, oldest first. */
export function timeline(publication: PublicationResponse, provider: string): TimelineStep[] {
  const steps: Array<[string, string | null]> = [
    ['Created', publication.createdAt],
    ['Approved', publication.approvedAt],
    ['Scheduled for', publication.scheduledFor],
    ['Sent', publication.dispatchedAt],
    ['Transferred', publication.transferredAt],
    [`Processing started at ${providerLabel(provider)}`, publication.processingAt],
    ['Published', publication.publishedAt],
    ['Failed', publication.failedAt],
    ['Cancelled', publication.cancelledAt],
  ]
  return steps
    .filter((step): step is [string, string] => step[1] !== null)
    .map(([label, instant]) => ({ label, instant }))
}
