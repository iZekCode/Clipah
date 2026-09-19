'use client'

import { useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { showRevisionApiV1EditsEditIdRevisionsRevisionGet } from '@/lib/api/generated/edits/edits'
import type { CompositionV1 } from '@/lib/api/generated/model'

/** The Revision every Edit starts at: the clip exactly as it was first opened. */
const FIRST_REVISION = 1

/**
 * Ask before throwing every edit away, then fetch the clip as it was first opened.
 *
 * Nothing is lost by resetting: the reset is one more change, so undo brings the work
 * back, and every earlier Revision stays in the history.
 */
export function ResetEditsDialog({
  open,
  onOpenChange,
  editId,
  workspaceId,
  onReset,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  editId: string
  workspaceId: string
  onReset: (original: CompositionV1) => void
}) {
  const [resetting, setResetting] = useState(false)
  const [failure, setFailure] = useState<unknown>(null)

  async function reset(): Promise<void> {
    setResetting(true)
    setFailure(null)
    try {
      const first = await showRevisionApiV1EditsEditIdRevisionsRevisionGet(
        editId,
        FIRST_REVISION,
        { workspace_id: workspaceId },
      )
      onReset(first.composition)
      onOpenChange(false)
    } catch (error) {
      setFailure(error)
    } finally {
      setResetting(false)
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!resetting) {
          setFailure(null)
          onOpenChange(next)
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Reset all edits?</AlertDialogTitle>
          <AlertDialogDescription>
            The clip goes back to how it was when it was first opened. You can undo this.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {failure === null ? null : <ErrorNotice error={failure} />}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={resetting}>Cancel</AlertDialogCancel>
          <Button variant="destructive" disabled={resetting} onClick={() => void reset()}>
            {resetting ? 'Resetting…' : 'Reset'}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
