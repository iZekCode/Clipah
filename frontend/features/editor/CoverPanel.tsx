'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, ImageIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { ApiError } from '@/lib/api/client'
import {
  createApiV1EditsEditIdCoverPost,
  showApiV1EditsEditIdCoverGet,
} from '@/lib/api/generated/covers/covers'
import type { CoverResponse } from '@/lib/api/generated/model'
import { formatClock } from '@/lib/media/time'
import { cn } from '@/lib/utils'

import type { CompositionCover } from './store'

type Preset = CompositionCover['preset']

/** The four designs, each with the one line that tells them apart. */
const PRESETS: ReadonlyArray<{ value: Preset; label: string; hint: string }> = [
  { value: 'bold', label: 'Bold', hint: 'Big yellow headline low on the frame' },
  { value: 'clean', label: 'Clean', hint: 'White title on a dark band' },
  { value: 'topTitle', label: 'Top title', hint: 'Tall title at the top' },
  { value: 'minimal', label: 'Minimal', hint: 'The frame and your watermark only' },
]

/** How often a cover being drawn is looked at again. */
const DRAWING_POLL_MS = 2_000

/**
 * The clip's cover: the frame it is known by, a design, and a title. The player shows the
 * design over the cover frame while this panel is open, and the picture — drawn by the
 * worker from the saved clip — can be downloaded once it exists.
 */
export function CoverPanel({
  cover,
  editId,
  workspaceId,
  playheadMs,
  durationMs,
  dirty,
  onChange,
  onShowFrame,
}: {
  cover: CompositionCover | null
  editId: string
  workspaceId: string
  playheadMs: number
  durationMs: number
  /** Unsaved changes: the picture is drawn from the saved clip, so it waits for a save. */
  dirty: boolean
  onChange: (cover: CompositionCover | null) => void
  onShowFrame: (atMs: number) => void
}) {
  const client = useQueryClient()
  const queryKey = ['/api/v1/edits/cover', workspaceId, editId]
  const picture = useQuery<CoverResponse, ApiError>({
    queryKey,
    queryFn: ({ signal }) =>
      showApiV1EditsEditIdCoverGet(editId, { workspace_id: workspaceId }, { signal }),
    retry: false,
    refetchInterval: (query) => (query.state.data?.status === 'drawing' ? DRAWING_POLL_MS : false),
  })
  const draw = useMutation<CoverResponse, ApiError>({
    mutationFn: () => createApiV1EditsEditIdCoverPost(editId, { workspace_id: workspaceId }),
    onSuccess: (state) => client.setQueryData(queryKey, state),
  })
  const download = useMutation<CoverResponse, ApiError>({
    // The link is signed at the moment of the click, because it lives five minutes.
    mutationFn: () =>
      showApiV1EditsEditIdCoverGet(editId, { workspace_id: workspaceId, download: true }),
    onSuccess: (signed) => {
      if (signed.url !== null) window.location.assign(signed.url)
    },
  })
  // A save makes a new Revision, whose picture may not exist yet.
  useEffect(() => {
    if (!dirty) void picture.refetch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty])
  const [title, setTitle] = useState(cover?.title ?? '')
  useEffect(() => setTitle(cover?.title ?? ''), [cover?.title])

  if (cover === null) {
    return (
      <section aria-label="Cover" className="space-y-3">
        <h2 className="text-title">Cover</h2>
        <p className="text-caption text-muted-foreground">
          A cover is the picture your clip is shown by before it plays: one of its frames, with
          a title.
        </p>
        <Button
          size="sm"
          onClick={() =>
            onChange({ atMs: Math.min(playheadMs, durationMs - 1), preset: 'bold', title: 'Your title' })
          }
        >
          Design a cover
        </Button>
      </section>
    )
  }

  const state = picture.data
  const status = dirty ? 'unsaved' : (state?.status ?? 'none')

  return (
    <section aria-label="Cover" className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-title">Cover</h2>
        <Button size="sm" variant="ghost" onClick={() => onChange(null)}>
          Remove cover
        </Button>
      </div>

      <div role="radiogroup" aria-label="Cover design" className="grid grid-cols-2 gap-2">
        {PRESETS.map((preset) => {
          const chosen = cover.preset === preset.value
          return (
            <button
              key={preset.value}
              type="button"
              role="radio"
              aria-checked={chosen}
              onClick={() => onChange({ ...cover, preset: preset.value })}
              className={cn(
                'rounded-md border px-3 py-2 text-left transition-colors duration-fast ease-signal',
                chosen ? 'border-primary bg-primary-soft' : 'border-line-strong hover:border-input',
              )}
            >
              <span className="block text-small font-medium">{preset.label}</span>
              <span className="block text-caption text-muted-foreground">{preset.hint}</span>
            </button>
          )
        })}
      </div>

      {cover.preset === 'minimal' ? null : (
        <label className="block space-y-1.5">
          <span className="text-caption text-muted-foreground">Title</span>
          <Input
            aria-label="Cover title"
            value={title}
            maxLength={120}
            onChange={(event) => {
              setTitle(event.target.value)
              const text = event.target.value.trim()
              onChange({ ...cover, title: text === '' ? null : event.target.value })
            }}
          />
        </label>
      )}

      <div className="space-y-2">
        <p className="text-caption text-muted-foreground">
          Cover frame at <span className="tabular font-mono">{formatClock(cover.atMs)}</span>
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => onChange({ ...cover, atMs: Math.min(playheadMs, durationMs - 1) })}
          >
            Use current frame
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onShowFrame(cover.atMs)}>
            Show cover frame
          </Button>
        </div>
      </div>

      <div className="space-y-2 border-t pt-4">
        <h3 className="text-small font-medium">Picture</h3>
        {status === 'unsaved' ? (
          <p className="text-caption text-muted-foreground">
            Save the clip first — the picture is drawn from the saved version.
          </p>
        ) : null}
        {status === 'drawing' ? (
          <p role="status" className="text-caption text-muted-foreground">
            Drawing the cover picture…
          </p>
        ) : null}
        {status === 'failed' ? (
          <p className="text-caption text-destructive">
            The cover picture could not be drawn{state?.errorCode ? ` (${state.errorCode})` : ''}.
            Try again.
          </p>
        ) : null}
        {status === 'ready' && state?.url ? (
          // eslint-disable-next-line @next/next/no-img-element -- a signed, short-lived link
          <img
            src={state.url}
            alt="Cover picture"
            className="aspect-[9/16] w-32 rounded-md border object-cover"
          />
        ) : null}
        <div className="flex flex-wrap gap-2">
          {status === 'ready' ? (
            <Button
              size="sm"
              loading={download.isPending}
              onClick={() => download.mutate()}
            >
              <Download aria-hidden="true" strokeWidth={1.75} />
              Download cover
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={status === 'unsaved' || status === 'drawing'}
              loading={draw.isPending}
              onClick={() => draw.mutate()}
            >
              <ImageIcon aria-hidden="true" strokeWidth={1.75} />
              Create cover picture
            </Button>
          )}
        </div>
        {draw.error === null ? null : <ErrorNotice error={draw.error} />}
        {download.error === null ? null : <ErrorNotice error={download.error} />}
        {picture.isError ? <ErrorNotice error={picture.error} /> : null}
      </div>
    </section>
  )
}
