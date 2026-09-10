/**
 * What one connected account is currently allowed to do, read from the provider.
 *
 * These values are the provider's, not ours: TikTok decides which privacy levels a
 * creator has and whether Duet is available to them, and a snapshot that does not say
 * is never treated as permission. Anything unreadable disables the control instead.
 */
import type { SocialCapabilitiesResponse } from '@/lib/api/generated/model'

export const TIKTOK_PRIVACY_LABELS: Record<string, string> = {
  PUBLIC_TO_EVERYONE: 'Everyone',
  MUTUAL_FOLLOW_FRIENDS: 'Friends who follow each other',
  FOLLOWER_OF_CREATOR: 'Followers',
  SELF_ONLY: 'Only you',
}

/** The choices TikTok currently permits for one creator. */
export interface TikTokCreatorChoices {
  creatorUsername: string | null
  privacyLevelOptions: string[]
  commentDisabled: boolean
  duetDisabled: boolean
  stitchDisabled: boolean
  maxVideoPostDurationSec: number | null
}

/** Read one TikTok snapshot, treating every unreadable value as a refusal. */
export function tiktokChoices(snapshot: SocialCapabilitiesResponse | undefined): TikTokCreatorChoices {
  const values = (snapshot?.capabilities ?? {}) as Record<string, unknown>
  const privacy = values.privacy_level_options
  return {
    creatorUsername: typeof values.creator_username === 'string' ? values.creator_username : null,
    privacyLevelOptions: Array.isArray(privacy)
      ? privacy.filter((option): option is string => typeof option === 'string')
      : [],
    commentDisabled: values.comment_disabled !== false,
    duetDisabled: values.duet_disabled !== false,
    stitchDisabled: values.stitch_disabled !== false,
    maxVideoPostDurationSec:
      typeof values.max_video_post_duration_sec === 'number'
        ? values.max_video_post_duration_sec
        : null,
  }
}

/** The provider's own word for one TikTok privacy level. */
export function tiktokPrivacyLabel(option: string): string {
  return TIKTOK_PRIVACY_LABELS[option] ?? option
}
