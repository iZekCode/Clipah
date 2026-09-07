'use client'

import { useQuery } from '@tanstack/react-query'
import { useCallback, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import {
  createApiV1EditsEditIdCampaignOutputsPost,
  listCollectionApiV1EditsEditIdCampaignOutputsGet,
} from '@/lib/api/generated/campaigns/campaigns'
import type {
  CampaignLanguage,
  CampaignOutputListResponse,
  CampaignOutputResponse,
  Platform,
} from '@/lib/api/generated/model'

/** The destinations Clipah packages copy for, without publishing to any of them. */
const PLATFORMS: Platform[] = ['tiktok', 'instagram_reels', 'youtube_shorts']
/** The languages this product writes copy in. Indonesian first, because that is who it is for. */
const LANGUAGES: { value: CampaignLanguage; label: string }[] = [
  { value: 'id', label: 'Indonesian' },
  { value: 'en', label: 'English' },
]

/**
 * Supporting copy derived from one approved cut of this clip.
 *
 * Two things are deliberate. The copy is derived from one exact immutable Revision, which
 * is named on every piece of it, so copy can never quietly describe a later cut. And
 * nothing on this panel posts anything: publishing is a composer a member opens with this
 * Revision preselected, and the decision to send is made there.
 */
export function CampaignPanel({ editId, revision }: { editId: string; revision: number }) {
  const { active } = useWorkspaceScope()
  const workspaceId = active.id
  const [platforms, setPlatforms] = useState<Platform[]>(['tiktok'])
  const [languages, setLanguages] = useState<CampaignLanguage[]>(['id'])
  const [working, setWorking] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const outputs = useQuery<CampaignOutputListResponse, ApiError>({
    queryKey: ['/api/v1/campaign-outputs', workspaceId, editId, revision],
    queryFn: ({ signal }) =>
      listCollectionApiV1EditsEditIdCampaignOutputsGet(
        editId,
        { workspace_id: workspaceId, revision },
        { signal },
      ),
    retry: false,
  })

  const write = useCallback(async () => {
    setWorking(true)
    setFailure(null)
    try {
      await createApiV1EditsEditIdCampaignOutputsPost(
        editId,
        { revision, platforms, languages },
        { workspace_id: workspaceId },
      )
      await outputs.refetch()
    } catch (error) {
      setFailure(error as ApiError)
    } finally {
      setWorking(false)
    }
  }, [editId, languages, outputs, platforms, revision, workspaceId])

  const copy = useCallback(async (output: CampaignOutputResponse) => {
    await navigator.clipboard.writeText(output.postCopy)
    setCopied(output.id)
  }, [])

  const found = outputs.data?.campaignOutputs ?? []

  return (
    <section className="space-y-3">
      <header className="space-y-1">
        <h2 className="text-sm font-medium">Campaign copy</h2>
        <p className="text-xs text-muted-foreground">
          Written from this clip’s own transcript and the analysis that chose it. Quotes stay
          in the language they were spoken in.
        </p>
      </header>

      <fieldset className="space-y-1">
        <legend className="text-xs text-muted-foreground">Destinations</legend>
        {PLATFORMS.map((platform) => (
          <label key={platform} className="mr-3 inline-flex items-center gap-1 text-sm">
            <input
              type="checkbox"
              checked={platforms.includes(platform)}
              onChange={() => setPlatforms(toggle(platforms, platform))}
            />
            <span>{platform.replace('_', ' ')}</span>
          </label>
        ))}
      </fieldset>

      <fieldset className="space-y-1">
        <legend className="text-xs text-muted-foreground">Languages</legend>
        {LANGUAGES.map((language) => (
          <label key={language.value} className="mr-3 inline-flex items-center gap-1 text-sm">
            <input
              type="checkbox"
              checked={languages.includes(language.value)}
              onChange={() => setLanguages(toggle(languages, language.value))}
            />
            <span>{language.label}</span>
          </label>
        ))}
      </fieldset>

      <button
        type="button"
        disabled={working || platforms.length === 0 || languages.length === 0}
        className="rounded-md border px-3 py-1 text-sm font-medium"
        onClick={() => void write()}
      >
        Write campaign copy
      </button>

      {outputs.isError ? <ErrorNotice error={outputs.error} /> : null}
      {failure === null ? null : <ErrorNotice error={failure} />}

      {found.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No copy has been written for this cut yet.
        </p>
      ) : (
        <ul className="space-y-3">
          {found.map((output) => (
            <li key={output.id} className="space-y-2 rounded-lg border p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-sm font-medium">
                  {output.platform.replace('_', ' ')} · {output.language}
                </span>
                <span className="text-xs text-muted-foreground">Revision {output.revision}</span>
              </div>
              <p className="text-sm font-medium">{output.title}</p>
              <p className="whitespace-pre-wrap text-sm">{output.postCopy}</p>
              <p className="text-sm">{output.cta}</p>
              <p className="text-xs text-muted-foreground">{output.hashtags.join(' ')}</p>
              <div className="rounded-md bg-muted/40 p-2 text-xs">
                <p className="font-medium">Thumbnail</p>
                <p>{output.thumbnailBrief.text}</p>
                <p className="text-muted-foreground">{output.thumbnailBrief.visualDirection}</p>
                {output.thumbnailBrief.avoid.length === 0 ? null : (
                  <p className="text-muted-foreground">
                    Avoid: {output.thumbnailBrief.avoid.join(', ')}
                  </p>
                )}
              </div>
              {output.warnings.length === 0 ? null : (
                <div role="note" className="rounded-md border border-amber-500/40 p-2 text-xs">
                  {output.warnings.map((warning) => (
                    <p key={`${warning.type}-${warning.detail}`}>
                      <span className="font-medium">{warning.type.replace(/_/g, ' ')}</span>:{' '}
                      {warning.detail}
                    </p>
                  ))}
                </div>
              )}
              <p className="text-xs text-muted-foreground">
                {output.modelMetadata.deterministic
                  ? 'Written from the transcript, without a language model.'
                  : `Written with ${output.modelMetadata.provider ?? 'a provider'} ${
                      output.modelMetadata.model ?? ''
                    }`}
              </p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="rounded-md border px-2 py-1 text-xs"
                  onClick={() => void copy(output)}
                >
                  Copy post
                </button>
                {copied === output.id ? (
                  <span className="text-xs text-muted-foreground">Copied.</span>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}

      <p className="text-xs text-muted-foreground">
        Nothing here is posted anywhere.{' '}
        <a
          className="underline"
          href={`/dashboard/publishing?editId=${editId}&revision=${revision}`}
        >
          Open the publishing composer
        </a>{' '}
        when this cut is ready to send.
      </p>
    </section>
  )
}

/** Add or remove one choice, leaving the rest of the selection alone. */
function toggle<Value>(values: Value[], value: Value): Value[] {
  return values.includes(value)
    ? values.filter((entry) => entry !== value)
    : [...values, value]
}
