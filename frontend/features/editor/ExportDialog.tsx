'use client'

import { useQueryClient } from '@tanstack/react-query'
import { Upload } from 'lucide-react'
import { useId, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Radio } from '@/components/ui/radio'
import { ExportList, PRESET_LABELS, exportsQueryKey } from '@/features/exports/export-list'
import { ApiError } from '@/lib/api/client'
import type { RenderPreset } from '@/lib/api/generated/model'
import { createApiV1EditsEditIdRendersPost } from '@/lib/api/generated/renders/renders'

const PRESETS: RenderPreset[] = ['1080x1920', '1080x1350', '1080x1080', '1920x1080']

/** Which export preset matches the canvas the member is editing, if any. */
export function presetForCanvas(width: number, height: number): RenderPreset {
  const match = PRESETS.find((preset) => preset === `${width}x${height}`)
  return match ?? '1080x1920'
}

/**
 * Export one clip: choose a shape, save, and ask for a render of exactly what was saved.
 *
 * The request names the Revision that the save produced, so a change that lands between
 * saving and rendering is refused by the backend instead of silently exported. Exports
 * are durable, so the list below survives closing the dialog, leaving the editor, and
 * refreshing the page.
 */
export function ExportDialog({
  open,
  onOpenChange,
  editId,
  workspaceId,
  defaultPreset,
  mayExport,
  saveNow,
  currentRevision,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  editId: string
  workspaceId: string
  defaultPreset: RenderPreset
  mayExport: boolean
  /** Save pending changes; answers whether the document on screen is now saved. */
  saveNow: () => Promise<boolean>
  currentRevision: () => number
}) {
  const queryClient = useQueryClient()
  const [preset, setPreset] = useState<RenderPreset>(defaultPreset)
  const [working, setWorking] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [failure, setFailure] = useState<unknown>(null)
  const groupId = useId()

  async function start() {
    setWorking(true)
    setProblem(null)
    setFailure(null)
    try {
      const saved = await saveNow()
      if (!saved) {
        setProblem(
          'Your latest changes are not saved yet, so they cannot be exported. Resolve the save problem shown in the editor, then export again.',
        )
        return
      }
      const revision = currentRevision()
      await createApiV1EditsEditIdRendersPost(
        editId,
        { preset, expectedRevision: revision },
        { workspace_id: workspaceId },
        // One export per saved Revision and shape: asking twice is the same export.
        { headers: { 'Idempotency-Key': `render:${editId}:r${revision}:${preset}` } },
      )
      await queryClient.invalidateQueries({ queryKey: exportsQueryKey(workspaceId, { editId }) })
    } catch (error) {
      if (error instanceof ApiError && error.code === 'EDIT_REVISION_CONFLICT') {
        setProblem(
          'This clip changed after it was saved, so nothing was exported. Check the newest version, then export again.',
        )
      } else {
        setFailure(error)
      }
    } finally {
      setWorking(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Export clip</DialogTitle>
          <DialogDescription>
            Choose where the clip is going. Your changes are saved first, and the export is
            made from exactly that saved version.
          </DialogDescription>
        </DialogHeader>

        {mayExport ? (
          <div className="space-y-4">
            <fieldset>
              <legend id={groupId} className="mb-2 text-sm font-medium">
                Format
              </legend>
              <div role="radiogroup" aria-labelledby={groupId} className="grid gap-2 sm:grid-cols-2">
                {PRESETS.map((entry) => {
                  const label = PRESET_LABELS[entry]
                  return (
                    <label
                      key={entry}
                      className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors ${
                        preset === entry ? 'border-primary bg-accent' : 'hover:bg-secondary'
                      }`}
                    >
                      <Radio
                        name="export-preset"
                        value={entry}
                        checked={preset === entry}
                        onChange={() => setPreset(entry)}
                        className="mt-1"
                      />
                      <span>
                        <span className="block text-sm font-medium">
                          {label?.name} {label?.ratio}
                        </span>
                        <span className="block text-xs text-muted-foreground">{label?.use}</span>
                      </span>
                    </label>
                  )
                })}
              </div>
            </fieldset>
            {problem === null ? null : (
              <p role="alert" className="rounded-lg bg-warning-soft p-3 text-sm text-warning">
                {problem}
              </p>
            )}
            {failure === null ? null : <ErrorNotice error={failure} />}
            <div className="flex justify-end">
              <Button type="button" onClick={() => void start()} disabled={working}>
                <Upload aria-hidden="true" />
                {working ? 'Saving and exporting…' : 'Export'}
              </Button>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            Your role can download finished exports but cannot start a new one.
          </p>
        )}

        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Exports of this clip</h3>
          <ExportList
            editId={editId}
            emptyDescription="Your exports appear here, with Download and Publish once they are ready."
          />
        </div>
      </DialogContent>
    </Dialog>
  )
}
