'use client'

import { useQuery } from '@tanstack/react-query'
import { Palette } from 'lucide-react'
import { useCallback, useState } from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  CAPTION_FONT_FAMILIES,
  captionFontStack,
  type CaptionFontFamily,
} from '@/features/editor/caption-fonts'
import { formatInstant } from '@/features/exports/export-list'
import { mayWriteProjects } from '@/features/workspaces/roles'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import {
  archiveApiV1BrandKitsBrandKitIdDelete,
  createApiV1BrandKitsPost,
  listCollectionApiV1BrandKitsGet,
  updateApiV1BrandKitsBrandKitIdPatch,
} from '@/lib/api/generated/brand-kits/brand-kits'
import { previewAssetApiV1AssetsAssetIdPreviewUrlGet } from '@/lib/api/generated/studio/studio'
import type {
  BrandKitListResponse,
  BrandKitResponse,
  MediaPreviewResponse,
} from '@/lib/api/generated/model'
import { cn } from '@/lib/utils'

/** A colour is written the one way the backend accepts, and the browser can say so first. */
const HEX = /^#[0-9A-Fa-f]{6}$/

/** The form a member fills in, before it becomes a version of the rules. */
interface Draft {
  name: string
  colours: string
  fontFamily: string
  minFontSize: string
  maxFontSize: string
  alignment: string
  reservedPlacement: string
  exclusions: string
  attribution: string
  forbidden: string
}

const EMPTY: Draft = {
  name: '',
  colours: '#FFFFFF, #000000',
  fontFamily: 'Inter',
  minFontSize: '32',
  maxFontSize: '72',
  alignment: 'center',
  reservedPlacement: 'lowerThird',
  exclusions: '',
  attribution: '',
  forbidden: '',
}

/**
 * The rules a Workspace's clips are held to, and the versions they were published at.
 *
 * Editing a kit publishes a new version rather than rewriting the current one, because a
 * clip records the exact version it was judged against: a Revision approved under version
 * 1 has to keep being judged by version 1 however often the brand changes afterwards. The
 * surface says which version it just published for the same reason.
 *
 * Every name here was typed by a member, so all of it is rendered as React children.
 */
export function BrandKitEditor() {
  const { active } = useWorkspaceScope()
  const workspaceId = active.id
  const mayWrite = mayWriteProjects(active.role)
  const [draft, setDraft] = useState<Draft>(EMPTY)
  const [editing, setEditing] = useState<BrandKitResponse | null>(null)
  const [published, setPublished] = useState<number | null>(null)
  const [invalid, setInvalid] = useState<string | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [working, setWorking] = useState(false)

  const kits = useQuery<BrandKitListResponse, ApiError>({
    queryKey: ['/api/v1/brand-kits', workspaceId],
    queryFn: ({ signal }) =>
      listCollectionApiV1BrandKitsGet({ workspace_id: workspaceId }, { signal }),
    retry: false,
  })

  const change = useCallback((field: keyof Draft, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }))
  }, [])

  const publish = useCallback(async () => {
    const colours = split(draft.colours)
    const offending = colours.find((colour) => !HEX.test(colour))
    if (offending !== undefined || colours.length === 0) {
      setInvalid('Colours are written as #RRGGBB, one per comma.')
      return
    }
    setInvalid(null)
    setFailure(null)
    setWorking(true)
    try {
      const definition = definitionOf(draft, colours)
      const answer =
        editing === null
          ? await createApiV1BrandKitsPost(
              { name: draft.name, definition },
              { workspace_id: workspaceId },
            )
          : await updateApiV1BrandKitsBrandKitIdPatch(
              editing.id,
              { name: draft.name, definition },
              { workspace_id: workspaceId },
            )
      setPublished(answer.version)
      setDraft(EMPTY)
      setEditing(null)
      await kits.refetch()
    } catch (error) {
      setFailure(error as ApiError)
    } finally {
      setWorking(false)
    }
  }, [draft, editing, kits, workspaceId])

  const archive = useCallback(
    async (kit: BrandKitResponse) => {
      setFailure(null)
      try {
        await archiveApiV1BrandKitsBrandKitIdDelete(kit.id, { workspace_id: workspaceId })
        await kits.refetch()
      } catch (error) {
        setFailure(error as ApiError)
      }
    },
    [kits, workspaceId],
  )

  const load = useCallback((kit: BrandKitResponse) => {
    setEditing(kit)
    setPublished(null)
    setDraft(draftOf(kit))
  }, [])

  const found = kits.data?.brandKits ?? []

  return (
    <section className="space-y-4">
      <PageHeader
        title="Brand kits"
        description="The colours, type, safe areas, and claims this Workspace holds its clips to. Editing a kit publishes a new version; clips already judged by an older version keep it."
      />

      {kits.isError ? <ErrorNotice error={kits.error} /> : null}
      {failure === null ? null : <ErrorNotice error={failure} />}
      {published === null ? null : (
        <p role="status" className="rounded-md bg-success-soft px-3 py-2 text-small font-medium text-success">
          Published version {published}.
        </p>
      )}

      {kits.isPending ? (
        <LoadingState label="Reading this Workspace’s brand…" variant="rows" />
      ) : found.length === 0 ? (
        <EmptyState
          icon={Palette}
          title="No brand kit has been published yet."
          description="A brand kit keeps every clip in your colours and type, and flags claims you never make."
        />
      ) : (
        <ul aria-label="Brand kits" className="grid gap-4 lg:grid-cols-2">
          {found.map((kit) => {
            const family = kit.definition.fonts[0]?.family ?? 'Inter'
            return (
              <li
                key={kit.id}
                className={cn(
                  'space-y-4 rounded-lg border bg-card p-4',
                  editing?.id === kit.id ? 'border-primary' : 'border-border',
                )}
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-title">{kit.name}</span>
                  <span className="font-mono text-caption text-muted-foreground">
                    Version {kit.version}
                  </span>
                </div>
                {kit.updatedAt === null ? null : (
                  <p className="-mt-3 font-mono text-caption text-subtle-foreground">
                    Updated {formatInstant(kit.updatedAt)}
                  </p>
                )}
                <div className="grid gap-4 sm:grid-cols-[auto_minmax(0,1fr)]">
                  <KitLogo logoAssetId={kit.definition.logoAssetId} />
                  <div className="min-w-0 space-y-1">
                    <p
                      className="text-h2 leading-none"
                      style={isCaptionFont(family) ? { fontFamily: captionFontStack(family) } : undefined}
                    >
                      Aa Bb 123
                    </p>
                    <p className="font-mono text-caption text-muted-foreground">{family}</p>
                  </div>
                </div>
                <div role="group" aria-label={`Palette of ${kit.name}`} className="flex flex-wrap gap-3">
                  {kit.definition.colors.map((colour) => (
                    <span key={`${colour.hex}-${colour.name}`} title={colour.name} className="space-y-1">
                      <span
                        aria-hidden="true"
                        className="block size-10 rounded-sm border border-input"
                        style={{ backgroundColor: colour.hex }}
                      />
                      <span className="block font-mono text-[11px] text-muted-foreground">
                        {colour.hex}
                      </span>
                    </span>
                  ))}
                </div>
                <p className="font-mono text-caption text-muted-foreground">
                  {kit.definition.colors.length} colours · type {kit.definition.captionRules.minFontSize}
                  –{kit.definition.captionRules.maxFontSize} ·{' '}
                  {kit.definition.visualExclusions.length} exclusions
                </p>
                {kit.archivedAt === null ? null : (
                  <p className="text-caption text-muted-foreground">
                    Archived. Clips judged by its versions still render.
                  </p>
                )}
                {mayWrite && kit.archivedAt === null ? (
                  <div className="flex gap-2">
                    <Button type="button" variant="secondary" size="sm" onClick={() => load(kit)}>
                      Edit
                    </Button>
                    <Button type="button" variant="ghost" size="sm" onClick={() => void archive(kit)}>
                      Archive
                    </Button>
                  </div>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}

      {mayWrite ? (
        <form
          className="space-y-4 rounded-lg border p-5"
          onSubmit={(event) => {
            event.preventDefault()
            void publish()
          }}
        >
          <div className="space-y-1">
            <h2 className="text-title">
              {editing === null ? 'Publish a brand kit' : `Publish a new version of ${editing.name}`}
            </h2>
            {editing === null ? null : (
              <p className="text-small text-muted-foreground">
                Currently at version {editing.version}. Saving publishes version {editing.version + 1};
                clips judged by earlier versions keep them.{' '}
                <button
                  type="button"
                  className="font-semibold text-primary hover:underline"
                  onClick={() => {
                    setEditing(null)
                    setDraft(EMPTY)
                  }}
                >
                  Cancel editing
                </button>
              </p>
            )}
          </div>
          <Field label="Brand kit name" value={draft.name} onChange={(value) => change('name', value)} />
          <Field
            label="Colours (#RRGGBB, comma separated)"
            value={draft.colours}
            onChange={(value) => change('colours', value)}
          />
          <div aria-hidden="true" className="flex flex-wrap gap-2">
            {split(draft.colours)
              .filter((colour) => HEX.test(colour))
              .map((colour, index) => (
                <span
                  key={`${colour}-${index}`}
                  className="size-6 rounded-sm border border-input"
                  style={{ backgroundColor: colour }}
                />
              ))}
          </div>
          <Field
            label="Font family"
            value={draft.fontFamily}
            onChange={(value) => change('fontFamily', value)}
          />
          <div className="flex gap-3">
            <Field
              label="Smallest type"
              value={draft.minFontSize}
              onChange={(value) => change('minFontSize', value)}
            />
            <Field
              label="Largest type"
              value={draft.maxFontSize}
              onChange={(value) => change('maxFontSize', value)}
            />
          </div>
          <Field
            label="Alignment"
            value={draft.alignment}
            onChange={(value) => change('alignment', value)}
          />
          <Field
            label="Area kept clear of type"
            value={draft.reservedPlacement}
            onChange={(value) => change('reservedPlacement', value)}
          />
          <Field
            label="Never show (comma separated)"
            value={draft.exclusions}
            onChange={(value) => change('exclusions', value)}
          />
          <Field
            label="Attribution shown on screen"
            value={draft.attribution}
            onChange={(value) => change('attribution', value)}
          />
          <Field
            label="Claims we may not make (comma separated)"
            value={draft.forbidden}
            onChange={(value) => change('forbidden', value)}
          />
          {invalid === null ? null : (
            <p role="alert" className="text-small text-destructive">
              {invalid}
            </p>
          )}
          <Button type="submit" disabled={working}>
            {editing === null ? 'Publish brand kit' : 'Publish new version'}
          </Button>
        </form>
      ) : null}
    </section>
  )
}

function isCaptionFont(family: string): family is CaptionFontFamily {
  return (CAPTION_FONT_FAMILIES as readonly string[]).includes(family)
}

/** The kit's logo on graphite, fetched through a short-lived link, or an empty slot. */
function KitLogo({ logoAssetId }: { logoAssetId: string | null }) {
  const { active } = useWorkspaceScope()
  const logo = useQuery<MediaPreviewResponse, ApiError>({
    queryKey: ['/api/v1/assets/preview-url', active.id, logoAssetId],
    queryFn: ({ signal }) =>
      previewAssetApiV1AssetsAssetIdPreviewUrlGet(logoAssetId ?? '', { workspace_id: active.id }, { signal }),
    enabled: logoAssetId !== null,
    retry: false,
  })
  return (
    <div className="flex h-16 w-28 items-center justify-center rounded-sm bg-stage p-2">
      {logo.data === undefined ? (
        <span className="text-caption text-subtle-foreground">No logo</span>
      ) : (
        // Signed object-store URLs are not known to the Next image optimizer.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={logo.data.url} alt="Logo" className="h-12 w-auto object-contain" />
      )}
    </div>
  )
}

/** One labelled text field, so every rule a member types is reachable by its own name. */
function Field({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="block space-y-1">
      <span className="text-caption text-muted-foreground">{label}</span>
      <Input value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  )
}

/** Turn the form into the definition the backend validates. */
function definitionOf(draft: Draft, colours: string[]): Record<string, unknown> {
  return {
    logoAssetId: null,
    fonts: [{ family: draft.fontFamily, assetId: null }],
    // A member who types only a colour has named it too: the swatch is what they call it.
    colors: colours.map((hex) => ({ name: hex, hex })),
    captionRules: {
      minFontSize: Number(draft.minFontSize),
      maxFontSize: Number(draft.maxFontSize),
      allowedAlignments: split(draft.alignment),
      reservedPlacements: split(draft.reservedPlacement),
    },
    visualExclusions: split(draft.exclusions),
    claimRules: {
      requiredAttribution: draft.attribution.trim() === '' ? null : draft.attribution.trim(),
      forbiddenClaimPhrases: split(draft.forbidden),
    },
  }
}

/** Load one published kit back into the form it was written in. */
function draftOf(kit: BrandKitResponse): Draft {
  const rules = kit.definition.captionRules
  return {
    name: kit.name,
    colours: kit.definition.colors.map((colour) => colour.hex).join(', '),
    fontFamily: kit.definition.fonts[0]?.family ?? 'Inter',
    minFontSize: String(rules.minFontSize),
    maxFontSize: String(rules.maxFontSize),
    alignment: rules.allowedAlignments.join(', '),
    reservedPlacement: rules.reservedPlacements.join(', '),
    exclusions: kit.definition.visualExclusions.join(', '),
    attribution: kit.definition.claimRules.requiredAttribution ?? '',
    forbidden: kit.definition.claimRules.forbiddenClaimPhrases.join(', '),
  }
}

/** Read one comma-separated field as the list a member meant by it. */
function split(value: string): string[] {
  return value
    .split(',')
    .map((entry) => entry.trim())
    .filter((entry) => entry !== '')
}
