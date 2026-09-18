'use client'

import { useQuery } from '@tanstack/react-query'
import { LayoutTemplate, Plus } from 'lucide-react'
import { useCallback, useState } from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { LookSample } from '@/features/editor/LookSample'
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
 * A look is applied by value: opening a clip with one copies its type into the composition
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
  const [composing, setComposing] = useState(false)
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
      setComposing(false)
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
    <section className="space-y-6">
      <PageHeader
        title="Templates"
        description="Caption looks you can reuse on any clip."
        actions={
          mayWrite ? (
            <Button type="button" onClick={() => setComposing(true)}>
              <Plus aria-hidden="true" strokeWidth={1.75} />
              Publish a look
            </Button>
          ) : undefined
        }
      />

      {templates.isError ? <ErrorNotice error={templates.error} /> : null}
      {failure === null ? null : <ErrorNotice error={failure} />}
      {archived === null ? null : (
        <p role="status" className="text-small text-muted-foreground">
          {archived} is archived. It still renders for the clips that use it.
        </p>
      )}

      <div className="flex items-center gap-3">
        <Switch
          id="show-archived-looks"
          checked={includeArchived}
          onCheckedChange={setIncludeArchived}
        />
        <label htmlFor="show-archived-looks" className="text-small text-muted-foreground">
          Show archived looks
        </label>
      </div>

      {templates.isPending ? (
        <LoadingState label="Reading this Workspace’s looks…" variant="cards" />
      ) : found.length === 0 ? (
        <EmptyState
          icon={LayoutTemplate}
          title="No look has been published yet."
          description="Publish a look to reuse the same caption style across clips."
        />
      ) : (
        <ul aria-label="Looks" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {found.map((template) => (
            <li key={template.id} className="flex flex-col gap-2 rounded-lg border bg-card p-2">
              <LookSample
                captionStyle={template.definition.captionStyle}
                captionMode={template.definition.captionMode}
              />
              <div className="flex items-baseline justify-between gap-2 px-1">
                <span className="truncate text-small font-semibold">{template.name}</span>
                <span className="shrink-0 font-mono text-caption text-muted-foreground">
                  Version {template.version}
                </span>
              </div>
              <p className="px-1 font-mono text-caption text-muted-foreground">
                {template.definition.captionMode} · {template.definition.captionStyle.fontFamily}
              </p>
              {template.archivedAt === null ? null : (
                <p className="px-1 text-caption text-muted-foreground">
                  Archived. It still renders for the clips that use it.
                </p>
              )}
              {mayWrite && template.archivedAt === null ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="self-start"
                  onClick={() => void archive(template)}
                >
                  Archive
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {mayWrite ? (
        <Dialog open={composing} onOpenChange={setComposing}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Publish a look</DialogTitle>
              <DialogDescription>Name the look and choose how its captions read.</DialogDescription>
            </DialogHeader>
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault()
                void publish()
              }}
            >
              <label className="block space-y-1">
                <span className="text-caption text-muted-foreground">Look name</span>
                <Input value={name} onChange={(event) => setName(event.target.value)} />
              </label>
              <label className="block space-y-1">
                <span className="text-caption text-muted-foreground">Captions</span>
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
              <div className="flex justify-end">
                <Button type="submit">Publish look</Button>
              </div>
            </form>
          </DialogContent>
        </Dialog>
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
