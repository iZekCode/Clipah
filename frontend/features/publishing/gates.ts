import type { CapabilitiesResponse, WorkspaceResponse } from '@/lib/api/generated/model'

/** The providers this product can publish to, in the order they were rolled out. */
export const PUBLISHING_PROVIDERS = ['youtube', 'instagram', 'tiktok'] as const

export type PublishingProvider = (typeof PUBLISHING_PROVIDERS)[number]

const PROVIDER_LABELS: Record<PublishingProvider, string> = {
  youtube: 'YouTube',
  instagram: 'Instagram',
  tiktok: 'TikTok',
}

/** The provider's own name for itself, which is the only name a member recognises. */
export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider as PublishingProvider] ?? provider
}

/** Whether a value names one of the providers this product knows how to publish to. */
export function isPublishingProvider(provider: string): provider is PublishingProvider {
  return (PUBLISHING_PROVIDERS as readonly string[]).includes(provider)
}

/**
 * The providers this deployment has actually switched on.
 *
 * Each gate is opened on its own and in a fixed order, so a deployment that is allowed to
 * publish Reels is not thereby allowed to post to TikTok. A closed gate hides the
 * provider entirely rather than offering a control the backend would refuse.
 */
export function openProviders(capabilities: CapabilitiesResponse): PublishingProvider[] {
  if (!capabilities.socialPublishing) {
    return []
  }
  return PUBLISHING_PROVIDERS.filter((provider) => {
    if (provider === 'youtube') return capabilities.youtubePublishing
    if (provider === 'instagram') return capabilities.instagramPublishing
    return capabilities.tiktokPublishing
  })
}

/**
 * Whether this member's role may publish in this Workspace.
 *
 * Publishing authority is not the ordinary write authority: a Workspace chooses whether
 * its editors carry it. Hiding the control is a courtesy, and the backend refuses the
 * same request for the same role however it is reached.
 */
export function mayPublish(workspace: WorkspaceResponse): boolean {
  if (workspace.role === 'owner' || workspace.role === 'admin') {
    return true
  }
  return workspace.role === 'editor' && workspace.publishingRolePolicy === 'owner_admin_editor'
}

/** Whether this member's role may connect, refresh, or end a Social Account. */
export function mayManageConnections(workspace: WorkspaceResponse): boolean {
  return workspace.role === 'owner' || workspace.role === 'admin'
}
