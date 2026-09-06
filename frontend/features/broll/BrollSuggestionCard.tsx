'use client'

import type { BrollSuggestionResponse, ProjectAssetResponse } from '@/lib/api/generated/model'

import { ProvenancePopover } from './ProvenancePopover'

/** The statuses in which a suggestion is currently drawn over the clip. */
const ON_TIMELINE = new Set(['placed', 'replaced'])

/** The statuses in which a model is already drawing something for this beat. */
const WORKING_STATUSES = new Set(['generation_requested', 'generating'])

/**
 * One proposal, with enough evidence beside it to disagree with the planner.
 *
 * Every word on this card except the timings was written by a language model reading
 * someone else's transcript, so all of it is rendered as React children. A subject
 * containing markup appears as that text and produces no element.
 */
export function BrollSuggestionCard({
  suggestion,
  clipStartMs,
  alternatives,
  busy,
  generationOffered,
  onAccept,
  onReject,
  onReplace,
  onRemove,
  onGenerate,
}: {
  suggestion: BrollSuggestionResponse
  clipStartMs: number
  alternatives: ProjectAssetResponse[]
  busy: boolean
  generationOffered: boolean
  onAccept: () => void
  onReject: () => void
  onReplace: (assetId: string) => void
  onRemove: () => void
  onGenerate: () => void
}) {
  const intent = suggestion.visualIntent
  const placed = ON_TIMELINE.has(suggestion.status)
  const hasMedia = suggestion.assetId !== null && suggestion.assetId !== undefined
  const swappable = alternatives.filter((asset) => asset.id !== suggestion.assetId)

  return (
    <article
      aria-label={intent.subject}
      className="flex flex-col gap-2 rounded-lg border p-3 text-xs"
    >
      <header className="flex flex-col gap-1">
        <p className="font-medium">{intent.subject}</p>
        <p className="text-muted-foreground">{intent.action}</p>
        <p className="flex flex-wrap gap-2 text-muted-foreground">
          <span>{formatTimecode(suggestion.startMs - clipStartMs)}</span>
          <span>{formatSeconds(suggestion.durationMs)}</span>
          <span>{formatConfidence(intent.confidence)} confident</span>
        </p>
      </header>

      <p>{suggestion.placementReason}</p>
      <p className="text-muted-foreground">
        {intent.setting} · {intent.mood}
      </p>

      {intent.factualRiskFlags.length === 0 ? null : (
        <p role="note" className="rounded border border-amber-500/40 bg-amber-500/10 p-2">
          Check this before publishing: {intent.factualRiskFlags.join(', ')}
        </p>
      )}

      {suggestion.provenance?.generated === true ? (
        <p className="w-fit rounded bg-muted px-2 py-0.5 font-medium">AI-generated</p>
      ) : null}

      {WORKING_STATUSES.has(suggestion.status) ? (
        <p role="status">Generating a picture for this beat. This continues in the background.</p>
      ) : null}

      {hasMedia ? (
        <ProvenancePopover provenance={suggestion.provenance} />
      ) : (
        <p className="text-muted-foreground">
          No picture found for this beat yet. Ask for B-roll again, or leave the moment as it is.
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        {placed ? (
          <button
            type="button"
            disabled={busy}
            onClick={onRemove}
            className="rounded border px-2 py-1"
          >
            Remove
          </button>
        ) : (
          <>
            <button
              type="button"
              disabled={busy || !hasMedia}
              onClick={onAccept}
              className="rounded border px-2 py-1"
            >
              Accept
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onReject}
              className="rounded border px-2 py-1"
            >
              Reject
            </button>
          </>
        )}
        {generationOffered ? (
          <button
            type="button"
            disabled={busy}
            onClick={onGenerate}
            className="rounded border px-2 py-1"
          >
            Generate still
          </button>
        ) : null}
        {placed && swappable.length > 0 ? (
          <label className="flex items-center gap-1">
            <span className="sr-only">Replace this picture</span>
            <select
              defaultValue=""
              disabled={busy}
              onChange={(event) => {
                if (event.target.value !== '') {
                  onReplace(event.target.value)
                }
              }}
              className="rounded border px-2 py-1"
            >
              <option value="">Replace this picture…</option>
              {swappable.map((asset) => (
                <option key={asset.id} value={asset.id}>
                  {asset.kind} · {formatSeconds(asset.durationMs ?? 0)}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
    </article>
  )
}

/** Where in the clip this shot begins, as a member reads a timeline. */
function formatTimecode(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1_000))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

/** How long a shot runs, in whole seconds. */
function formatSeconds(ms: number): string {
  return `${Math.round(ms / 1_000)}s`
}

/** How sure the planner was, as a percentage rather than a bare fraction. */
function formatConfidence(confidence: number): string {
  return `${Math.round(confidence * 100)}%`
}
