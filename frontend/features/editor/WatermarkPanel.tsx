'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NumberScrub } from '@/components/ui/number-scrub'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { apiFetch, type ApiError } from '@/lib/api/client'
import { indexApiV1ProjectsProjectIdAssetsGet } from '@/lib/api/generated/assets/assets'
import type { ProjectAssetResponse, ProjectAssetsResponse } from '@/lib/api/generated/model'
import { getCreateApiV1ProjectsProjectIdPicturesPostUrl } from '@/lib/api/generated/pictures/pictures'
import { cn } from '@/lib/utils'

import type { CompositionWatermark } from './store'

type Position = CompositionWatermark['position']
type Kind = CompositionWatermark['kind']

/** The nine cells, in reading order, as the grid below draws them. */
const POSITIONS: ReadonlyArray<{ value: Position; label: string }> = [
  { value: 'topLeft', label: 'Top left' },
  { value: 'topCenter', label: 'Top centre' },
  { value: 'topRight', label: 'Top right' },
  { value: 'middleLeft', label: 'Middle left' },
  { value: 'center', label: 'Centre' },
  { value: 'middleRight', label: 'Middle right' },
  { value: 'bottomLeft', label: 'Bottom left' },
  { value: 'bottomCenter', label: 'Bottom centre' },
  { value: 'bottomRight', label: 'Bottom right' },
]

/** The mark a clip gets back when a member switches the watermark on again. */
export const CLIPAH_WATERMARK: CompositionWatermark = {
  kind: 'clipah',
  position: 'bottomRight',
  size: 0.2,
  opacity: 0.9,
  assetId: null,
  text: null,
}

/** A share of the frame width a newly chosen kind starts at: pictures wide, text short. */
const STARTING_SIZE: Record<Kind, number> = { clipah: 0.2, image: 0.16, text: 0.04 }

/** The stills the server reads; anything else is refused before it is sent. */
const PICTURE_TYPES = 'image/png,image/jpeg,image/webp'

/**
 * The clip's watermark: Clipah's own mark, one of this Project's pictures, or a line of
 * text, in any of nine places over the frame. Whatever is chosen here is what the export
 * burns in, at the same size and position.
 */
export function WatermarkPanel({
  watermark,
  projectId,
  workspaceId,
  onChange,
}: {
  watermark: CompositionWatermark | null
  projectId: string
  workspaceId: string
  onChange: (watermark: CompositionWatermark | null) => void
}) {
  const client = useQueryClient()
  const assetsKey = ['/api/v1/projects/assets', workspaceId, projectId]
  const assets = useQuery<ProjectAssetsResponse, ApiError>({
    queryKey: assetsKey,
    queryFn: ({ signal }) =>
      indexApiV1ProjectsProjectIdAssetsGet(projectId, { workspace_id: workspaceId }, { signal }),
    retry: false,
  })
  const pictures = (assets.data?.assets ?? []).filter((asset) =>
    asset.contentType.startsWith('image/'),
  )
  // Text is saved as it is typed only once it has something in it; a blank line is no mark.
  const [draft, setDraft] = useState(watermark?.text ?? '')
  useEffect(() => {
    if (watermark?.kind === 'text') setDraft(watermark.text ?? '')
  }, [watermark?.kind, watermark?.text])

  const picker = useRef<HTMLInputElement>(null)
  const upload = useMutation<ProjectAssetResponse, ApiError, File>({
    // The file goes as its own bytes; the server decodes it and keeps a PNG of its own.
    mutationFn: (file) =>
      apiFetch<ProjectAssetResponse>(
        getCreateApiV1ProjectsProjectIdPicturesPostUrl(projectId, { workspace_id: workspaceId }),
        { method: 'POST', headers: { 'content-type': 'application/octet-stream' }, body: file },
      ),
    onSuccess: (picture) => {
      client.setQueryData<ProjectAssetsResponse>(assetsKey, (current) => ({
        assets: [...(current?.assets ?? []), picture],
      }))
      void client.invalidateQueries({ queryKey: assetsKey })
      if (watermark === null) return
      const size = watermark.kind === 'image' ? watermark.size : STARTING_SIZE.image
      onChange({ ...watermark, kind: 'image', size, assetId: picture.id, text: null })
    },
  })

  /** Switch what the mark draws, keeping where it sits and how opaque it is. */
  function chooseKind(kind: Kind) {
    if (watermark === null || kind === watermark.kind) return
    const base = { position: watermark.position, opacity: watermark.opacity, size: STARTING_SIZE[kind] }
    if (kind === 'clipah') onChange({ ...base, kind, assetId: null, text: null })
    if (kind === 'text') onChange({ ...base, kind, assetId: null, text: draft.trim() || 'Clipah' })
    if (kind === 'image') {
      const first = pictures[0]
      // A Project with no picture yet asks for one, rather than ignoring the tap.
      if (first === undefined) picker.current?.click()
      else onChange({ ...base, kind, assetId: first.id, text: null })
    }
  }

  return (
    <section aria-label="Watermark" className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-title">Watermark</h2>
        <Switch
          aria-label="Show a watermark"
          checked={watermark !== null}
          onCheckedChange={(on) => onChange(on ? CLIPAH_WATERMARK : null)}
        />
      </div>
      {watermark === null ? (
        <p className="text-caption text-muted-foreground">
          This clip exports without a watermark. Switch it on to add Clipah&apos;s mark, a logo,
          or a line of text.
        </p>
      ) : (
        <>
          <SegmentedControl<Kind>
            label="Watermark kind"
            size="sm"
            value={watermark.kind}
            options={[
              { value: 'clipah', label: 'Clipah logo' },
              { value: 'image', label: 'Image' },
              { value: 'text', label: 'Text' },
            ]}
            onChange={chooseKind}
          />

          <input
            ref={picker}
            type="file"
            accept={PICTURE_TYPES}
            className="sr-only"
            tabIndex={-1}
            aria-label="Upload a watermark picture"
            onChange={(event) => {
              const file = event.target.files?.[0]
              event.target.value = ''
              if (file !== undefined) upload.mutate(file)
            }}
          />
          {watermark.kind === 'image' && pictures.length > 0 ? (
            <label className="block space-y-1.5">
              <span className="text-caption text-muted-foreground">Picture</span>
              <Select
                aria-label="Watermark picture"
                value={watermark.assetId ?? ''}
                onChange={(event) => onChange({ ...watermark, assetId: event.target.value })}
              >
                {pictures.map((asset, index) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.kind === 'picture' ? 'Uploaded picture' : 'B-roll picture'} {index + 1}
                    {asset.width === null || asset.height === null
                      ? ''
                      : ` · ${asset.width}×${asset.height}`}
                  </option>
                ))}
              </Select>
            </label>
          ) : null}
          {watermark.kind === 'image' || pictures.length === 0 ? (
            <div className="space-y-1.5">
              <Button
                size="sm"
                variant="secondary"
                loading={upload.isPending}
                onClick={() => picker.current?.click()}
              >
                <Upload aria-hidden="true" strokeWidth={1.75} />
                Upload picture
              </Button>
              <p className="text-caption text-muted-foreground">
                PNG, JPEG, or WebP up to 5 MB. A PNG with a transparent background looks best.
              </p>
            </div>
          ) : null}
          {upload.error === null ? null : <ErrorNotice error={upload.error} />}

          {watermark.kind === 'text' ? (
            <label className="block space-y-1.5">
              <span className="text-caption text-muted-foreground">Text</span>
              <Input
                aria-label="Watermark text"
                value={draft}
                maxLength={64}
                onChange={(event) => {
                  setDraft(event.target.value)
                  const text = event.target.value.trim()
                  if (text !== '') onChange({ ...watermark, text })
                }}
              />
            </label>
          ) : null}

          <fieldset className="space-y-1.5">
            <legend className="text-caption text-muted-foreground">Position</legend>
            <div role="radiogroup" aria-label="Watermark position" className="grid w-28 grid-cols-3 gap-1">
              {POSITIONS.map((position) => {
                const chosen = watermark.position === position.value
                return (
                  <button
                    key={position.value}
                    type="button"
                    role="radio"
                    aria-checked={chosen}
                    aria-label={position.label}
                    title={position.label}
                    onClick={() => onChange({ ...watermark, position: position.value })}
                    className={cn(
                      'aspect-square rounded-sm border transition-colors duration-fast ease-signal',
                      chosen
                        ? 'border-primary bg-primary'
                        : 'border-line-strong bg-secondary hover:border-input',
                    )}
                  />
                )
              })}
            </div>
          </fieldset>

          <NumberScrub
            label="Size"
            accessibleName="Watermark size"
            value={Math.round(watermark.size * 100)}
            min={2}
            max={50}
            step={1}
            unit="%"
            onCommit={(size) => onChange({ ...watermark, size: size / 100 })}
          />
          <NumberScrub
            label="Opacity"
            accessibleName="Watermark opacity"
            value={Math.round(watermark.opacity * 100)}
            min={10}
            max={100}
            step={5}
            unit="%"
            onCommit={(opacity) => onChange({ ...watermark, opacity: opacity / 100 })}
          />
        </>
      )}
    </section>
  )
}
