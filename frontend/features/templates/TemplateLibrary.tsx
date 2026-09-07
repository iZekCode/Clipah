'use client'

import { useQuery } from '@tanstack/react-query'
import { useCallback, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
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
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Templates</h1>
        <p className="text-sm text-muted-foreground">
          Reusable looks for captions and drawn text. Applying one writes its type into a clip
          and records the version it came from.
        </p>
      </header>

      {templates.isError ? <ErrorNotice error={templates.error} /> : null}
      {failure === null ? null : <ErrorNotice error={failure} />}
      {archived === null ? null : (
        <p className="text-sm text-muted-foreground">
          {archived} is archived. It still renders for the clips that use it.
        </p>
      )}

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={includeArchived}
          onChange={(event) => setIncludeArchived(event.target.checked)}
        />
        <span>Show archived looks</span>
      </label>

      {templates.isPending ? (
        <p className="text-sm text-muted-foreground">Reading this Workspace’s looks…</p>
      ) : found.length === 0 ? (
        <p className="text-sm text-muted-foreground">No look has been published yet.</p>
      ) : (
        <ul className="space-y-2">
          {found.map((template) => (
            <li key={template.id} className="rounded-lg border p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium">{template.name}</span>
                <span className="text-xs text-muted-foreground">Version {template.version}</span>
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
                  className="mt-2 rounded-md border px-2 py-1 text-xs"
                  onClick={() => void archive(template)}
                >
                  Archive
                </button>
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
          <h2 className="text-sm font-medium">Publish a look</h2>
          <label className="block space-y-1 text-sm">
            <span className="text-muted-foreground">Look name</span>
            <input
              className="w-full rounded-md border px-2 py-1"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="text-muted-foreground">Captions</span>
            <select
              className="w-full rounded-md border px-2 py-1"
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
            </select>
          </label>
          <button type="submit" className="rounded-md border px-3 py-1 text-sm font-medium">
            Publish look
          </button>
        </form>
      ) : null}
    </section>
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
