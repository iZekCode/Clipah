'use client'

import { useEffect, useRef } from 'react'

import type { GenerationEstimateResponse, GenerationOfferResponse } from '@/lib/api/generated/model'

/** Why a deployment or a suggestion cannot be generated, in words a member can act on. */
const UNAVAILABLE_COPY: Record<string, string> = {
  provider_unavailable: 'Generated media is not available on this deployment.',
  video_disabled: 'Generated video is not available on this deployment.',
  stock_sufficient: 'This beat already has a picture good enough to use.',
  not_reviewable: 'This suggestion is no longer waiting for a decision.',
}

/**
 * The one place a member reads what a generation costs before agreeing to it.
 *
 * The dialog shows the server's own estimate and hands back the sealed confirmation it
 * came with. It never composes a prompt, names a model, or chooses a size: everything a
 * provider will be asked for was decided by the server and is only being reported here.
 */
export function GenerationConfirmDialog({
  offer,
  loading,
  submitting,
  videoOffered,
  failure,
  onConfirm,
  onConsiderVideo,
  onClose,
}: {
  offer: GenerationOfferResponse | null
  loading: boolean
  submitting: boolean
  videoOffered: boolean
  failure: string | null
  onConfirm: () => void
  onConsiderVideo: () => void
  onClose: () => void
}) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const isVideo = offer?.estimate?.mediaKind === 'video'

  useEffect(() => {
    closeRef.current?.focus()
  }, [])

  const estimate = offer?.estimate ?? null
  const busy = loading || submitting

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={isVideo ? 'Generate a clip' : 'Generate a still'}
      className="flex flex-col gap-3 rounded-lg border p-3 text-xs"
    >
      <header className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">
            {isVideo ? 'Generate a clip' : 'Generate a still'}
          </h3>
          <p className="text-muted-foreground">
            Nothing is generated until you confirm the estimate below.
          </p>
        </div>
        <button
          ref={closeRef}
          type="button"
          disabled={submitting}
          onClick={onClose}
          className="rounded border px-2 py-1"
        >
          Close
        </button>
      </header>

      {loading ? (
        <p role="status" className="text-muted-foreground">
          Asking what this would cost…
        </p>
      ) : null}

      {!loading && offer !== null && offer.available !== true ? (
        <p role="status">
          {UNAVAILABLE_COPY[offer.reason ?? ''] ??
            'Generated media is not available for this suggestion.'}
        </p>
      ) : null}

      {estimate === null ? null : <EstimateFacts estimate={estimate} />}

      {failure === null ? null : <p role="status">{failure}</p>}

      <div className="flex flex-wrap gap-2">
        {estimate === null ? null : (
          <button
            type="button"
            disabled={busy}
            onClick={onConfirm}
            className="rounded border px-2 py-1 font-medium"
          >
            {isVideo
              ? `Generate video for $${estimate.costUsd}`
              : `Generate for $${estimate.costUsd}`}
          </button>
        )}
        {videoOffered && !isVideo ? (
          <button
            type="button"
            disabled={busy}
            onClick={onConsiderVideo}
            className="rounded border px-2 py-1"
          >
            Consider video instead
          </button>
        ) : null}
      </div>
    </div>
  )
}

/** Every provider-neutral fact the server priced this request with. */
function EstimateFacts({ estimate }: { estimate: GenerationEstimateResponse }) {
  return (
    <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
      <dt className="text-muted-foreground">Estimated cost</dt>
      <dd>${estimate.costUsd}</dd>

      <dt className="text-muted-foreground">Size</dt>
      <dd>
        {estimate.width} × {estimate.height}
      </dd>

      <dt className="text-muted-foreground">Outputs</dt>
      <dd>{estimate.outputCount}</dd>

      {estimate.durationMs === null || estimate.durationMs === undefined ? null : (
        <>
          <dt className="text-muted-foreground">Length</dt>
          <dd>{Math.round(estimate.durationMs / 1_000)}s</dd>
        </>
      )}

      <dt className="text-muted-foreground">Counts against</dt>
      <dd>
        {estimate.mediaKind === 'image'
          ? `${estimate.imageUnits} generated image`
          : `${estimate.videoUnits} generated video · ${estimate.generatedSeconds}s`}
      </dd>

      <dt className="text-muted-foreground">Provider credits</dt>
      <dd>{estimate.providerCredits}</dd>

      <dt className="text-muted-foreground">Speed</dt>
      <dd>{estimate.latencyClass === 'slow' ? 'Slower' : 'Standard'}</dd>
    </dl>
  )
}
