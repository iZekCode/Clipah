'use client'

import { useQuery } from '@tanstack/react-query'
import { useCallback, useState } from 'react'

import { LayoutTemplate } from 'lucide-react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { PageHeader } from '@/components/page-header'
import { Checkbox } from '@/components/ui/checkbox'
import { Select } from '@/components/ui/select'
import { mayWriteProjects } from '@/features/workspaces/roles'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { TemplateListResponse, TemplateResponse } from '@/lib/api/generated/model'
import {
  archiveApiV1TemplatesTemplateIdDelete,
  createApiV1TemplatesPost,
  listCollectionApiV1TemplatesGet,
} from '@/lib/api/generated/templates/templates'

/** The caption treatments a Workspace-owned look may publish. */
const CAPTION_MODES = ['karaoke', 'block', 'off'] as const

/**
 * The looks a Workspace reuses, and the version each one is published at.
 *
 * A look is applied by value: opening a clip with one writes its type into the composition
 * and records the version it came from. That is why archiving is offered rather than
 * deletion — the clips that already carry a version keep rendering exactly as they did.
 */
export function TemplateLibrary() {
  const { active } = useWorkspaceScope()
  const workspaceId = active.id
  const mayWrite = mayWriteProjects(active.role)
  const [includeArchived, setIncludeArchived] = useState(false)
  const [name, setName] = useState('')
  const [captionMode, setCaptionMode] = useState<(typeof CAPTION_MODES)[number]>('karaoke')
  const [archived, setArchived] = useState<string | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const templates = useQuery<TemplateListResponse, ApiError>({
    queryKey: ['/api/v1/templates', workspaceId, includeArchived],
    queryFn: ({ signal }) =>
      listCollectionApiV1TemplatesGet(
        { workspace_id: workspaceId, include_archived: includeArchived },
        { signal },
      ),
    retry: false,
  })

  const publish = useCallback(async () => {
    setFailure(null)
    try {
      await createApiV1TemplatesPost(
        { name, brandKitId: null, definition: definitionOf(captionMode) },
        { workspace_id: workspaceId },
      )
      setName('')
      await templates.refetch()
    } catch (error) {
      setFailure(error as ApiError)
    }
  }, [captionMode, name, templates, workspaceId])

  const archive = useCallback(
    async (template: TemplateResponse) => {
      setFailure(null)
      try {
        await archiveApiV1TemplatesTemplateIdDelete(template.id, { workspace_id: workspaceId })
        setArchived(template.name)
        await templates.refetch()
      } catch (error) {
        setFailure(error as ApiError)
      }
    },
    [templates, workspaceId],
  )

  const found = templates.data?.templates ?? []

  return (
    <section className="space-y-4">
      <PageHeader
        title="Templates"
        description="Reusable looks for captions and drawn text. Applying one writes its type into a clip and records the version it came from."
      />

      {templates.isError ? <ErrorNotice error={templates.error} /> : null}
      {failure === null ? null : <ErrorNotice error={failure} />}
      {archived === null ? null : (
        <p className="text-sm text-muted-foreground">
          {archived} is archived. It still renders for the clips that use it.
        </p>
      )}

      <label className="flex items-center gap-2 text-sm">
        <Checkbox
          checked={includeArchived}
          onChange={(event) => setIncludeArchived(event.target.checked)}
        />
        <span>Show archived looks</span>
      </label>

      {templates.isPending ? (
        <LoadingState label="Reading this Workspace’s looks…" variant="cards" />
      ) : found.length === 0 ? (
        <EmptyState
          icon={LayoutTemplate}
          title="No look has been published yet."
          description="Publish a look below to reuse the same caption style across clips."
        />
      ) : (
        <ul aria-label="Looks" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {found.map((template) => (
            <li key={template.id} className="surface overflow-hidden p-0">
              <LookPreview template={template} />
              <div className="p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium">{template.name}</span>
                <span className="rounded-full bg-secondary px-2 py-0.5 text-xs font-medium text-muted-foreground">Version {template.version}</span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {template.definition.captionMode} captions ·{' '}
                {template.definition.captionStyle.fontFamily}{' '}
                {template.definition.captionStyle.fontSize}
              </p>
              {template.archivedAt === null ? null : (
                <p className="mt-1 text-xs text-muted-foreground">
                  Archived. It still renders for the clips that use it.
                </p>
              )}
              {mayWrite && template.archivedAt === null ? (
                <button
                  type="button"
                  className="mt-2 rounded-lg border bg-card px-2.5 py-1 text-xs hover:bg-secondary"
                  onClick={() => void archive(template)}
                >
                  Archive
                </button>
              ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}

      {mayWrite ? (
        <form
          className="surface max-w-xl space-y-4 p-5"
          onSubmit={(event) => {
            event.preventDefault()
            void publish()
          }}
        >
          <h2 className="text-base font-semibold">Publish a look</h2>
          <label className="block space-y-1 text-sm">
            <span className="text-muted-foreground">Look name</span>
            <input
              className="h-10 w-full rounded-lg border border-input bg-card px-3"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="text-muted-foreground">Captions</span>
            <Select
              wrapperClassName="w-full"
              value={captionMode}
              onChange={(event) =>
                setCaptionMode(event.target.value as (typeof CAPTION_MODES)[number])
              }
            >
              {CAPTION_MODES.map((mode) => (
                <option key={mode} value={mode}>
                  {mode}
                </option>
              ))}
            </Select>
          </label>
          <button type="submit" className="h-10 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90">
            Publish look
          </button>
        </form>
      ) : null}
    </section>
  )
}

/**
 * A small vertical frame showing how the look's captions read over footage.
 *
 * It uses the look's own font, colour, weight, background, and highlight, so the preview
 * is the definition rather than an illustration of it.
 */
function LookPreview({ template }: { template: TemplateResponse }) {
  const style = template.definition.captionStyle
  const karaoke = template.definition.captionMode === 'karaoke'
  const hidden = template.definition.captionMode === 'off'
  return (
    <div
      aria-hidden="true"
      className="flex aspect-[16/10] items-end justify-center bg-gradient-to-br from-slate-700 via-slate-800 to-violet-900 p-4"
    >
      {hidden ? (
        <span className="mb-6 rounded bg-black/30 px-2 py-1 text-xs text-white/70">No captions</span>
      ) : (
        <span
          className="mb-4 max-w-full rounded px-2 py-1 text-center leading-tight"
          style={{
            fontFamily: `${style.fontFamily}, ui-sans-serif, system-ui`,
            fontWeight: style.weight,
            fontStyle: style.italic ? 'italic' : 'normal',
            color: style.color,
            fontSize: `${Math.max(14, Math.min(24, style.fontSize / 3))}px`,
            backgroundColor: style.backgroundEnabled ? style.backgroundColor : 'transparent',
            textShadow: style.backgroundEnabled ? 'none' : '0 1px 3px rgba(0,0,0,0.6)',
          }}
        >
          This is how{' '}
          <span style={{ color: karaoke ? (style.highlightColor ?? style.color) : style.color }}>captions</span>{' '}
          look
        </span>
      )}
    </div>
  )
}

/** The one kind of look version 1 publishes, written out from the member's choice. */
function definitionOf(captionMode: string): Record<string, unknown> {
  const style = {
    fontFamily: 'Inter',
    fontSize: 56,
    color: '#FFFFFF',
    align: 'center',
    weight: 600,
    italic: false,
    decoration: 'none',
    letterSpacing: 0,
    lineHeight: 1.2,
    backgroundEnabled: false,
    backgroundColor: '#000000',
  }
  return {
    kind: 'clip_look',
    captionMode,
    captionStyle: { ...style, highlightColor: '#FFD166' },
    textStyle: { ...style, fontSize: 42, weight: 500 },
  }
}
