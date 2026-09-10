/**
 * One destination's choices, kept per account and never shared between providers.
 *
 * Every field starts empty on purpose. A default privacy value, a default disclosure, or
 * a shared caption would be the product deciding something on a member's behalf that
 * only they can answer, so nothing is complete until they have answered all of it.
 */
import type {
  PublicationDestinationBody,
  SocialAccountResponse,
} from '@/lib/api/generated/model'

import { providerLabel } from './gates'
import { tiktokPrivacyLabel, type TikTokCreatorChoices } from './creator-capabilities'

export type Choice = '' | 'yes' | 'no'
export type CommercialContent = '' | 'none' | 'your_brand' | 'branded_content'

/** Everything a member may state about one destination, before anything is chosen. */
export interface DestinationDraft {
  title: string
  description: string
  caption: string
  privacyStatus: string
  shareToFeed: Choice
  madeForKids: Choice
  syntheticMedia: Choice
  privacyLevel: string
  allowComment: boolean
  allowDuet: boolean
  allowStitch: boolean
  commercialContent: CommercialContent
  brandedContentTerms: boolean
  musicUsageConfirmed: boolean
  consumerTermsAccepted: boolean
  madeWithAi: Choice
}

/** One destination with nothing answered yet. */
export function emptyDraft(): DestinationDraft {
  return {
    title: '',
    description: '',
    caption: '',
    privacyStatus: '',
    shareToFeed: '',
    madeForKids: '',
    syntheticMedia: '',
    privacyLevel: '',
    allowComment: false,
    allowDuet: false,
    allowStitch: false,
    commercialContent: '',
    brandedContentTerms: false,
    musicUsageConfirmed: false,
    consumerTermsAccepted: false,
    madeWithAi: '',
  }
}

/** Whether a member typed or chose anything that dropping this destination would lose. */
export function hasContent(draft: DestinationDraft): boolean {
  const empty = emptyDraft()
  return (Object.keys(empty) as Array<keyof DestinationDraft>).some(
    (key) => draft[key] !== empty[key],
  )
}

/** TikTok forbids a private branded-content post, so that option stops being offered. */
export function tiktokPrivacyDisabled(draft: DestinationDraft, option: string): boolean {
  return draft.commercialContent === 'branded_content' && option === 'SELF_ONLY'
}

/** Whether this destination carries every answer its provider requires. */
export function isComplete(
  provider: string,
  draft: DestinationDraft,
  choices: TikTokCreatorChoices | null,
): boolean {
  if (provider === 'youtube') {
    return (
      draft.title.trim().length > 0 &&
      draft.privacyStatus !== '' &&
      draft.madeForKids !== '' &&
      draft.syntheticMedia !== ''
    )
  }
  if (provider === 'instagram') {
    return draft.caption.trim().length > 0 && draft.shareToFeed !== ''
  }
  if (choices === null || choices.privacyLevelOptions.length === 0) {
    return false
  }
  return (
    draft.caption.trim().length > 0 &&
    draft.privacyLevel !== '' &&
    !tiktokPrivacyDisabled(draft, draft.privacyLevel) &&
    draft.commercialContent !== '' &&
    (draft.commercialContent !== 'branded_content' || draft.brandedContentTerms) &&
    draft.musicUsageConfirmed &&
    draft.consumerTermsAccepted &&
    draft.madeWithAi !== ''
  )
}

/** The destination body one prepared batch carries for this account. */
export function destinationPayload({
  account,
  draft,
  directPost,
  scheduledFor,
  displayTimezone,
  now,
}: {
  account: SocialAccountResponse
  draft: DestinationDraft
  directPost: boolean
  scheduledFor: string | null
  displayTimezone: string
  now: Date
}): PublicationDestinationBody {
  return {
    socialAccountId: account.id,
    metadata: metadataFor(account.provider, draft),
    providerOptions: providerOptionsFor(account.provider, draft, directPost),
    consent: consentFor(account.provider, draft, now),
    scheduledFor,
    displayTimezone,
  }
}

/** How one destination reads back in the confirmation, effect by effect. */
export function summaryLines(
  provider: string,
  draft: DestinationDraft,
  directPost: boolean,
): string[] {
  if (provider === 'youtube') {
    return [
      `Title: ${draft.title}`,
      `Visibility: ${capitalize(draft.privacyStatus)}`,
      draft.madeForKids === 'yes' ? 'Made for kids' : 'Not made for kids',
      draft.syntheticMedia === 'yes'
        ? 'Declared as synthetic or altered media'
        : 'Declared as not synthetic or altered media',
      'Delivery: uploaded to YouTube',
    ]
  }
  if (provider === 'instagram') {
    return [
      `Caption: ${draft.caption}`,
      draft.shareToFeed === 'yes' ? 'Also shared to the feed' : 'Reels tab only',
      'Delivery: published as a Reel',
    ]
  }
  return [
    `Caption: ${draft.caption}`,
    `Who can see it: ${tiktokPrivacyLabel(draft.privacyLevel)}`,
    `Interactions: ${interactionSummary(draft)}`,
    `Commercial content: ${commercialLabel(draft.commercialContent)}`,
    draft.madeWithAi === 'yes' ? 'Declared as made with AI' : 'Declared as not made with AI',
    directPost
      ? 'Delivery: posted straight to TikTok'
      : 'Delivery: saved to your TikTok inbox as a draft',
  ]
}

/** The one sentence that says what reaching this provider actually does. */
export function deliverySentence(provider: string, directPost: boolean): string {
  if (provider === 'tiktok') {
    return directPost
      ? 'This video is posted straight to TikTok and is visible as soon as TikTok finishes processing it.'
      : 'This video is saved to your TikTok inbox as a draft, and you publish it from the TikTok app.'
  }
  if (provider === 'instagram') {
    return 'This video is published as a Reel on Instagram as soon as Instagram finishes processing it.'
  }
  return `This video is uploaded to ${providerLabel(provider)} with the visibility you choose here.`
}

function metadataFor(provider: string, draft: DestinationDraft): Record<string, unknown> {
  if (provider === 'youtube') {
    return { title: draft.title, description: draft.description }
  }
  return { caption: draft.caption }
}

function providerOptionsFor(
  provider: string,
  draft: DestinationDraft,
  directPost: boolean,
): Record<string, unknown> {
  if (provider === 'youtube') {
    return {
      privacyStatus: draft.privacyStatus,
      selfDeclaredMadeForKids: draft.madeForKids === 'yes',
      containsSyntheticMedia: draft.syntheticMedia === 'yes',
    }
  }
  if (provider === 'instagram') {
    return { shareToFeed: draft.shareToFeed === 'yes' }
  }
  return {
    privacyLevel: draft.privacyLevel,
    disableComment: !draft.allowComment,
    disableDuet: !draft.allowDuet,
    disableStitch: !draft.allowStitch,
    brandContentToggle: draft.commercialContent === 'branded_content',
    brandOrganicToggle: draft.commercialContent === 'your_brand',
    isAigc: draft.madeWithAi === 'yes',
    deliveryMode: directPost ? 'direct_post' : 'draft_inbox',
  }
}

function consentFor(
  provider: string,
  draft: DestinationDraft,
  now: Date,
): Record<string, unknown> {
  if (provider !== 'tiktok') {
    return { confirmed: true, confirmedAt: now.toISOString() }
  }
  return {
    musicUsageConfirmed: draft.musicUsageConfirmed,
    consumerTermsAccepted: draft.consumerTermsAccepted,
    brandedContentTermsAccepted: draft.brandedContentTerms,
    confirmedAt: now.toISOString(),
  }
}

function interactionSummary(draft: DestinationDraft): string {
  const allowed = [
    draft.allowComment ? 'comments' : null,
    draft.allowDuet ? 'Duet' : null,
    draft.allowStitch ? 'Stitch' : null,
  ].filter((value): value is string => value !== null)
  return allowed.length === 0 ? 'all switched off' : allowed.join(', ')
}

function commercialLabel(value: CommercialContent): string {
  if (value === 'branded_content') return 'Branded content'
  if (value === 'your_brand') return 'Your own brand'
  return 'None declared'
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}
