import { toast } from 'sonner'

import { ApiError } from '@/lib/api/client'

export interface NotifyOptions {
  description?: string
  action?: { label: string; onClick: () => void }
  secondaryAction?: { label: string; onClick: () => void }
  /** A toast with the same id replaces the one already showing. */
  id?: string
}

function toSonner({ secondaryAction, ...options }: NotifyOptions) {
  return secondaryAction === undefined ? options : { ...options, cancel: secondaryAction }
}

const FALLBACK = 'Something went wrong. Try again.'

/**
 * The one way product code raises a toast.
 *
 * Failures show the backend's public message and keep the request reference in the
 * description, so a member can quote it without the sentence being about identifiers.
 */
export const notify = {
  success(message: string, options: NotifyOptions = {}): void {
    toast.success(message, toSonner(options))
  },
  info(message: string, options: NotifyOptions = {}): void {
    toast(message, toSonner(options))
  },
  failure(error: unknown, fallback: string = FALLBACK): void {
    if (error instanceof ApiError) {
      toast.error(error.message, {
        description: error.requestId === null ? undefined : `Ref ${error.requestId}`,
      })
      return
    }
    toast.error(fallback)
  },
}
