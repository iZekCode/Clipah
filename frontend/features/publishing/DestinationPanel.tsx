'use client'

import type { SocialAccountResponse } from '@/lib/api/generated/model'

import { providerLabel } from './gates'
import { tiktokPrivacyLabel, type TikTokCreatorChoices } from './creator-capabilities'
import {
  deliverySentence,
  tiktokPrivacyDisabled,
  type Choice,
  type CommercialContent,
  type DestinationDraft,
} from './destination-draft'

/** One destination's own controls, built from what its provider currently permits. */
export function DestinationPanel({
  account,
  draft,
  choices,
  publicPrivacyAllowed,
  directPost,
  onChange,
}: {
  account: SocialAccountResponse
  draft: DestinationDraft
  choices: TikTokCreatorChoices | null
  publicPrivacyAllowed: boolean
  directPost: boolean
  onChange: (change: Partial<DestinationDraft>) => void
}) {
  return (
    <fieldset className="space-y-4 rounded border p-4">
      <legend className="px-1 text-sm font-medium">
        {account.displayName} ({providerLabel(account.provider)})
      </legend>
      <p className="text-sm text-muted-foreground">
        {deliverySentence(account.provider, directPost)}
      </p>
      {account.provider === 'youtube' ? (
        <YouTubeFields
          draft={draft}
          publicPrivacyAllowed={publicPrivacyAllowed}
          onChange={onChange}
        />
      ) : null}
      {account.provider === 'instagram' ? (
        <InstagramFields draft={draft} onChange={onChange} />
      ) : null}
      {account.provider === 'tiktok' ? (
        <TikTokFields draft={draft} choices={choices} onChange={onChange} />
      ) : null}
    </fieldset>
  )
}

function YouTubeFields({
  draft,
  publicPrivacyAllowed,
  onChange,
}: {
  draft: DestinationDraft
  publicPrivacyAllowed: boolean
  onChange: (change: Partial<DestinationDraft>) => void
}) {
  return (
    <div className="space-y-4">
      <Text
        label="Title"
        value={draft.title}
        onChange={(title) => onChange({ title })}
      />
      <Text
        label="Description"
        value={draft.description}
        multiline
        onChange={(description) => onChange({ description })}
      />
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium">Visibility</legend>
        {(['private', 'unlisted', 'public'] as const).map((value) => (
          <Radio
            key={value}
            group="youtube-visibility"
            label={value === 'private' ? 'Private' : value === 'unlisted' ? 'Unlisted' : 'Public'}
            value={value}
            checked={draft.privacyStatus === value}
            disabled={value !== 'private' && !publicPrivacyAllowed}
            onSelect={() => onChange({ privacyStatus: value })}
          />
        ))}
        {publicPrivacyAllowed ? null : (
          <p className="text-xs text-muted-foreground">
            This workspace may only publish privately until YouTube completes its audit of
            this application.
          </p>
        )}
      </fieldset>
      <YesNo
        legend="Audience"
        group="youtube-kids"
        yesLabel="Made for kids"
        noLabel="Not made for kids"
        value={draft.madeForKids}
        onSelect={(madeForKids) => onChange({ madeForKids })}
      />
      <YesNo
        legend="Synthetic or altered media"
        group="youtube-synthetic"
        yesLabel="Contains synthetic or altered media"
        noLabel="No synthetic or altered media"
        value={draft.syntheticMedia}
        onSelect={(syntheticMedia) => onChange({ syntheticMedia })}
      />
    </div>
  )
}

function InstagramFields({
  draft,
  onChange,
}: {
  draft: DestinationDraft
  onChange: (change: Partial<DestinationDraft>) => void
}) {
  return (
    <div className="space-y-4">
      <Text
        label="Caption"
        value={draft.caption}
        multiline
        onChange={(caption) => onChange({ caption })}
      />
      <YesNo
        legend="Where it appears"
        group="instagram-feed"
        yesLabel="Also share to the feed"
        noLabel="Reels tab only"
        value={draft.shareToFeed}
        onSelect={(shareToFeed) => onChange({ shareToFeed })}
      />
    </div>
  )
}

function TikTokFields({
  draft,
  choices,
  onChange,
}: {
  draft: DestinationDraft
  choices: TikTokCreatorChoices | null
  onChange: (change: Partial<DestinationDraft>) => void
}) {
  if (choices === null) {
    return <p role="status">Reading what TikTok currently allows for this account…</p>
  }
  if (choices.privacyLevelOptions.length === 0) {
    return (
      <p>
        TikTok did not say which privacy levels this creator has. Refresh the connection
        before publishing.
      </p>
    )
  }
  return (
    <div className="space-y-4">
      <Text
        label="Caption"
        value={draft.caption}
        multiline
        onChange={(caption) => onChange({ caption })}
      />
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium">Who can see this video</legend>
        {choices.privacyLevelOptions.map((option) => (
          <Radio
            key={option}
            group="tiktok-privacy"
            label={tiktokPrivacyLabel(option)}
            value={option}
            checked={draft.privacyLevel === option}
            disabled={tiktokPrivacyDisabled(draft, option)}
            onSelect={() => onChange({ privacyLevel: option })}
          />
        ))}
        {draft.commercialContent === 'branded_content' ? (
          <p className="text-xs text-muted-foreground">
            Branded content cannot be visible to only you.
          </p>
        ) : null}
      </fieldset>
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium">Interactions</legend>
        <Check
          label="Allow comments"
          checked={draft.allowComment}
          disabled={choices.commentDisabled}
          onToggle={(allowComment) => onChange({ allowComment })}
        />
        <Check
          label="Allow Duet"
          checked={draft.allowDuet}
          disabled={choices.duetDisabled}
          onToggle={(allowDuet) => onChange({ allowDuet })}
        />
        <Check
          label="Allow Stitch"
          checked={draft.allowStitch}
          disabled={choices.stitchDisabled}
          onToggle={(allowStitch) => onChange({ allowStitch })}
        />
        {choices.commentDisabled ? (
          <p className="text-xs text-muted-foreground">
            TikTok has switched Comment off for this account.
          </p>
        ) : null}
        {choices.duetDisabled ? (
          <p className="text-xs text-muted-foreground">
            TikTok has switched Duet off for this account.
          </p>
        ) : null}
        {choices.stitchDisabled ? (
          <p className="text-xs text-muted-foreground">
            TikTok has switched Stitch off for this account.
          </p>
        ) : null}
      </fieldset>
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium">Commercial content</legend>
        {(
          [
            ['none', 'No commercial content'],
            ['your_brand', 'Promotes your own brand'],
            ['branded_content', 'Branded content for another brand'],
          ] as Array<[CommercialContent, string]>
        ).map(([value, label]) => (
          <Radio
            key={value}
            group="tiktok-commercial"
            label={label}
            value={value}
            checked={draft.commercialContent === value}
            onSelect={() =>
              onChange({
                commercialContent: value,
                privacyLevel:
                  value === 'branded_content' && draft.privacyLevel === 'SELF_ONLY'
                    ? ''
                    : draft.privacyLevel,
              })
            }
          />
        ))}
      </fieldset>
      <div className="space-y-1">
        <Check
          label="I confirm this post follows TikTok's Music Usage Confirmation"
          checked={draft.musicUsageConfirmed}
          onToggle={(musicUsageConfirmed) => onChange({ musicUsageConfirmed })}
        />
        <Check
          label="I accept TikTok's Terms of Service"
          checked={draft.consumerTermsAccepted}
          onToggle={(consumerTermsAccepted) => onChange({ consumerTermsAccepted })}
        />
        {draft.commercialContent === 'branded_content' ? (
          <Check
            label="I accept TikTok's Branded Content Policy"
            checked={draft.brandedContentTerms}
            onToggle={(brandedContentTerms) => onChange({ brandedContentTerms })}
          />
        ) : null}
      </div>
      <YesNo
        legend="Made with AI"
        group="tiktok-aigc"
        yesLabel="Made with AI"
        noLabel="Not made with AI"
        value={draft.madeWithAi}
        onSelect={(madeWithAi) => onChange({ madeWithAi })}
      />
    </div>
  )
}

function Text({
  label,
  value,
  multiline = false,
  onChange,
}: {
  label: string
  value: string
  multiline?: boolean
  onChange: (value: string) => void
}) {
  const shared = {
    value,
    onChange: (event: { target: { value: string } }) => onChange(event.target.value),
    className: 'w-full rounded border p-2 text-sm',
  }
  return (
    <label className="block space-y-1 text-sm font-medium">
      <span>{label}</span>
      {multiline ? <textarea rows={3} {...shared} /> : <input type="text" {...shared} />}
    </label>
  )
}

function Radio({
  group,
  label,
  value,
  checked,
  disabled = false,
  onSelect,
}: {
  group: string
  label: string
  value: string
  checked: boolean
  disabled?: boolean
  onSelect: () => void
}) {
  return (
    <label className="flex items-center gap-2 text-sm font-normal">
      <input
        type="radio"
        name={group}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={onSelect}
      />
      <span>{label}</span>
    </label>
  )
}

function Check({
  label,
  checked,
  disabled = false,
  onToggle,
}: {
  label: string
  checked: boolean
  disabled?: boolean
  onToggle: (checked: boolean) => void
}) {
  return (
    <label className="flex items-center gap-2 text-sm font-normal">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onToggle(event.target.checked)}
      />
      <span>{label}</span>
    </label>
  )
}

function YesNo({
  legend,
  group,
  yesLabel,
  noLabel,
  value,
  onSelect,
}: {
  legend: string
  group: string
  yesLabel: string
  noLabel: string
  value: Choice
  onSelect: (value: Choice) => void
}) {
  return (
    <fieldset className="space-y-1">
      <legend className="text-sm font-medium">{legend}</legend>
      <Radio
        group={group}
        label={yesLabel}
        value="yes"
        checked={value === 'yes'}
        onSelect={() => onSelect('yes')}
      />
      <Radio
        group={group}
        label={noLabel}
        value="no"
        checked={value === 'no'}
        onSelect={() => onSelect('no')}
      />
    </fieldset>
  )
}
