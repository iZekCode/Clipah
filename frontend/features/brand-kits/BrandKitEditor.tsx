'use client'

import { useQuery } from '@tanstack/react-query'
import { useCallback, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { mayWriteProjects } from '@/features/workspaces/roles'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import {
  archiveApiV1BrandKitsBrandKitIdDelete,
  createApiV1BrandKitsPost,
  listCollectionApiV1BrandKitsGet,
  updateApiV1BrandKitsBrandKitIdPatch,
} from '@/lib/api/generated/brand-kits/brand-kits'
import type { BrandKitListResponse, BrandKitResponse } from '@/lib/api/generated/model'

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
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Brand kits</h1>
        <p className="text-sm text-muted-foreground">
          The colours, type, safe areas, and claims this Workspace holds its clips to. Editing
          a kit publishes a new version; clips already judged by an older version keep it.
        </p>
      </header>

      {kits.isError ? <ErrorNotice error={kits.error} /> : null}
      {failure === null ? null : <ErrorNotice error={failure} />}
      {published === null ? null : (
        <p className="text-sm text-muted-foreground">Published version {published}.</p>
      )}

      {kits.isPending ? (
        <p className="text-sm text-muted-foreground">Reading this Workspace’s brand…</p>
      ) : found.length === 0 ? (
        <p className="text-sm text-muted-foreground">No brand kit has been published yet.</p>
      ) : (
        <ul className="space-y-2">
          {found.map((kit) => (
            <li key={kit.id} className="rounded-lg border p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium">{kit.name}</span>
                <span className="text-xs text-muted-foreground">Version {kit.version}</span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {kit.definition.colors.length} colours · type {kit.definition.captionRules.minFontSize}
                –{kit.definition.captionRules.maxFontSize} ·{' '}
                {kit.definition.visualExclusions.length} exclusions
              </p>
              {kit.archivedAt === null ? null : (
                <p className="mt-1 text-xs text-muted-foreground">
                  Archived. Clips judged by its versions still render.
                </p>
              )}
              {mayWrite && kit.archivedAt === null ? (
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    className="rounded-md border px-2 py-1 text-xs"
                    onClick={() => load(kit)}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="rounded-md border px-2 py-1 text-xs"
                    onClick={() => void archive(kit)}
                  >
                    Archive
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {mayWrite ? (
        <form
          className="space-y-3 rounded-lg border p-3"
          onSubmit={(event) => {
            event.preventDefault()
            void publish()
          }}
        >
          <h2 className="text-sm font-medium">
            {editing === null ? 'Publish a brand kit' : `Publish a new version of ${editing.name}`}
          </h2>
          <Field label="Brand kit name" value={draft.name} onChange={(value) => change('name', value)} />
          <Field
            label="Colours (#RRGGBB, comma separated)"
            value={draft.colours}
            onChange={(value) => change('colours', value)}
          />
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
            <p role="alert" className="text-sm text-destructive">
              {invalid}
            </p>
          )}
          <button
            type="submit"
            disabled={working}
            className="rounded-md border px-3 py-1 text-sm font-medium"
          >
            {editing === null ? 'Publish brand kit' : 'Publish new version'}
          </button>
        </form>
      ) : null}
    </section>
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
    <label className="block space-y-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <input
        className="w-full rounded-md border px-2 py-1"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
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
