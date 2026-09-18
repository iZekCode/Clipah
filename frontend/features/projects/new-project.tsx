'use client'

import { useQueryClient } from '@tanstack/react-query'
import { Link2, Plus, Upload } from 'lucide-react'
import { useRouter } from 'next/navigation'
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { Field, inputClassName } from '@/components/field'
import { Button, type ButtonProps } from '@/components/ui/button'
import { IconButton } from '@/components/ui/icon-button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { UploadRejectedError, uploadSource } from '@/features/uploads/uploader'
import { unusableUrl } from '@/features/uploads/YouTubeImportForm'
import { mayWriteProjects } from '@/features/workspaces/roles'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import { ApiError } from '@/lib/api/client'
import type { SourceKind } from '@/lib/api/generated/model'
import { createApiV1ProjectsPost } from '@/lib/api/generated/projects/projects'
import { createApiV1ProjectsProjectIdYoutubeImportsPost } from '@/lib/api/generated/source-imports/source-imports'

import { projectsQueryKey } from './query-keys'

type Source = 'upload' | 'youtube'

/** Open the one New project dialog from anywhere inside the Workspace frame. */
interface NewProjectControl {
  /** Open New project, with the file already chosen when one was dropped or picked. */
  open: (file?: File) => void
}

const NewProjectContext = createContext<NewProjectControl | null>(null)

/**
 * Hold the New project dialog once for the whole Workspace frame, so the shell, Home,
 * and Projects all open the same dialog instead of each owning a copy.
 */
export function NewProjectProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [initialFile, setInitialFile] = useState<File | null>(null)
  const control = useMemo<NewProjectControl>(
    () => ({
      open: (file?: File) => {
        setInitialFile(file ?? null)
        setOpen(true)
      },
    }),
    [],
  )
  return (
    <NewProjectContext.Provider value={control}>
      {children}
      <NewProjectDialog
        open={open}
        initialFile={initialFile}
        onOpenChange={(next) => {
          setOpen(next)
          if (!next) setInitialFile(null)
        }}
      />
    </NewProjectContext.Provider>
  )
}

/** Open New project from anywhere inside the Workspace frame, optionally with a file. */
export function useNewProject(): NewProjectControl | null {
  return useContext(NewProjectContext)
}

/**
 * The prominent way to start. It is shown only to members who may create Projects; the
 * backend refuses the same write for everyone else.
 */
export function NewProjectButton({
  size = 'default',
  variant,
  label = 'New project',
  className,
  appearance = 'button',
}: {
  size?: 'default' | 'sm' | 'lg'
  variant?: ButtonProps['variant']
  label?: string
  className?: string
  /** `rail` is the square lime-plus control at the top of the navigation rail. */
  appearance?: 'button' | 'rail'
}) {
  const { active } = useWorkspaceScope()
  const shared = useContext(NewProjectContext)
  const [open, setOpen] = useState(false)

  if (!mayWriteProjects(active.role)) {
    return null
  }
  if (appearance === 'rail') {
    return (
      <>
        <IconButton
          label="New project"
          variant="secondary"
          icon={<Plus strokeWidth={2} className="text-primary" />}
          onClick={() => (shared === null ? setOpen(true) : shared.open())}
          tooltipSide="right"
        />
        {shared === null ? <NewProjectDialog open={open} onOpenChange={setOpen} /> : null}
      </>
    )
  }
  return (
    <>
      <Button
        type="button"
        size={size}
        variant={variant}
        className={className}
        onClick={() => (shared === null ? setOpen(true) : shared.open())}
      >
        <Plus aria-hidden="true" />
        {label}
      </Button>
      {shared === null ? <NewProjectDialog open={open} onOpenChange={setOpen} /> : null}
    </>
  )
}

/** Where the dialog has got to with one submission. */
type Phase =
  | { step: 'editing' }
  | { step: 'creating' }
  | { step: 'uploading'; uploadedBytes: number; totalBytes: number }
  | { step: 'importing' }
  | { step: 'failed'; error: unknown }

/**
 * Start a Project the way a creator thinks about it: pick the video, name it, go.
 *
 * The Project is created on submit and the existing resumable upload or public import
 * then carries the media into it. A Project that was created is never created twice: a
 * retry reuses it, and when the upload cannot be finished the Project stays available so
 * the member can pick the file again from its own page, where the upload resumes.
 */
export function NewProjectDialog({
  open,
  onOpenChange,
  initialFile = null,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** A file chosen before the dialog opened, such as one dropped on the window. */
  initialFile?: File | null
}) {
  const { active } = useWorkspaceScope()
  const queryClient = useQueryClient()
  const router = useRouter()
  const [source, setSource] = useState<Source>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [nameEdited, setNameEdited] = useState(false)
  const [url, setUrl] = useState('')
  const [refusal, setRefusal] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>({ step: 'editing' })
  const created = useRef<{ id: string; source: Source } | null>(null)
  const submission = useRef<string | null>(null)

  const busy = phase.step === 'creating' || phase.step === 'uploading' || phase.step === 'importing'

  function reset() {
    setSource('upload')
    setFile(null)
    setName('')
    setNameEdited(false)
    setUrl('')
    setRefusal(null)
    setPhase({ step: 'editing' })
    created.current = null
    submission.current = null
  }

  function changeOpen(next: boolean) {
    // An upload in flight belongs to this dialog; closing it would orphan the progress.
    if (!next && busy) {
      return
    }
    onOpenChange(next)
    if (!next) {
      reset()
    }
  }

  function pickFile(picked: File | null) {
    setFile(picked)
    setRefusal(null)
    if (picked !== null && !nameEdited) {
      setName(suggestedName(picked.name))
    }
  }

  useEffect(() => {
    if (open && initialFile !== null) {
      setSource('upload')
      pickFile(initialFile)
    }
    // Only a newly offered file is picked; edits after that belong to the member.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialFile])

  /** Create the Project once per submission, however many times the media step is retried. */
  async function ensureProject(projectName: string, kind: SourceKind): Promise<string> {
    if (created.current !== null) {
      return created.current.id
    }
    submission.current ??= crypto.randomUUID()
    setPhase({ step: 'creating' })
    const project = await createApiV1ProjectsPost(
      { name: projectName, sourceKind: kind },
      { workspace_id: active.id },
      { headers: { 'Idempotency-Key': `projects:create:${submission.current}` } },
    )
    created.current = { id: project.id, source }
    void queryClient.invalidateQueries({ queryKey: projectsQueryKey(active.id) })
    return project.id
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const projectName = name.trim()
    if (projectName === '') {
      setRefusal('Give this project a name.')
      return
    }
    if (source === 'upload' && file === null) {
      setRefusal('Choose a video file to upload.')
      return
    }
    if (source === 'youtube') {
      const problem = unusableUrl(url)
      if (problem !== null) {
        setRefusal(problem)
        return
      }
    }
    setRefusal(null)

    try {
      if (source === 'upload' && file !== null) {
        const projectId = await ensureProject(projectName, 'upload')
        setPhase({ step: 'uploading', uploadedBytes: 0, totalBytes: file.size })
        await uploadSource({
          file,
          projectId,
          workspaceId: active.id,
          onProgress: (uploadedBytes, totalBytes) =>
            setPhase({ step: 'uploading', uploadedBytes, totalBytes }),
        })
        finish(projectId)
        return
      }
      const projectId = await ensureProject(projectName, 'public_url')
      setPhase({ step: 'importing' })
      await createApiV1ProjectsProjectIdYoutubeImportsPost(
        projectId,
        { url: url.trim() },
        { workspace_id: active.id },
        { headers: { 'Idempotency-Key': `youtube-import:${projectId}:${url.trim()}` } },
      )
      finish(projectId)
    } catch (error) {
      setPhase({ step: 'failed', error })
    }
  }

  function finish(projectId: string) {
    onOpenChange(false)
    reset()
    router.push(`/dashboard/projects/${projectId}`)
  }

  const projectId = created.current?.id ?? null

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>New project</DialogTitle>
          <DialogDescription>
            Add one long video. Clipah transcribes it and suggests the moments worth clipping.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={(event) => void submit(event)} className="space-y-5" noValidate>
          <div role="tablist" aria-label="Where the video comes from" className="grid grid-cols-2 gap-1 rounded-lg bg-secondary p-1">
            <SourceTab
              selected={source === 'upload'}
              disabled={busy || projectId !== null}
              onSelect={() => {
                setSource('upload')
                setRefusal(null)
              }}
              icon={<Upload aria-hidden="true" className="size-4" />}
            >
              Upload a file
            </SourceTab>
            <SourceTab
              selected={source === 'youtube'}
              disabled={busy || projectId !== null}
              onSelect={() => {
                setSource('youtube')
                setRefusal(null)
              }}
              icon={<Link2 aria-hidden="true" className="size-4" />}
            >
              YouTube link
            </SourceTab>
          </div>

          <div role="tabpanel" className="space-y-4">
            {source === 'upload' ? (
              <FilePicker file={file} disabled={busy} onPick={pickFile} />
            ) : (
              <Field
                label="YouTube video link"
                help="One public, non-live video you have the right to use. Private, members-only, and age-restricted videos cannot be imported."
              >
                <input
                  type="url"
                  value={url}
                  disabled={busy || projectId !== null}
                  onChange={(event) => {
                    setUrl(event.target.value)
                    setRefusal(null)
                  }}
                  placeholder="https://www.youtube.com/watch?v=…"
                  className={inputClassName}
                />
              </Field>
            )}

            <Field label="Project name" help={source === 'upload' ? 'We suggest the file name. You can change it any time.' : undefined}>
              <input
                type="text"
                value={name}
                maxLength={200}
                disabled={busy || projectId !== null}
                onChange={(event) => {
                  setName(event.target.value)
                  setNameEdited(true)
                }}
                placeholder="Episode 42 — interview"
                className={inputClassName}
              />
            </Field>
          </div>

          {refusal === null ? null : (
            <p role="alert" className="text-sm text-destructive">
              {refusal}
            </p>
          )}

          <SubmissionProgress phase={phase} />

          {phase.step === 'failed' ? (
            <div className="space-y-3">
              <FailureMessage error={phase.error} />
              {projectId === null ? null : (
                <p className="text-sm text-muted-foreground">
                  Your project was created and is waiting for its video. Try again here, or
                  open the project and add the video there — an interrupted upload picks up
                  where it stopped.
                </p>
              )}
            </div>
          ) : null}

          <DialogFooter className="gap-2 sm:gap-2">
            {projectId !== null && phase.step === 'failed' ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  const id = projectId
                  onOpenChange(false)
                  reset()
                  router.push(`/dashboard/projects/${id}`)
                }}
              >
                Open project
              </Button>
            ) : (
              <Button type="button" variant="ghost" disabled={busy} onClick={() => changeOpen(false)}>
                Cancel
              </Button>
            )}
            <Button type="submit" disabled={busy}>
              {submitLabel(phase, source)}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function SourceTab({
  selected,
  disabled,
  onSelect,
  icon,
  children,
}: {
  selected: boolean
  disabled: boolean
  onSelect: () => void
  icon: ReactNode
  children: ReactNode
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      disabled={disabled && !selected}
      onClick={onSelect}
      className={`flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors disabled:opacity-50 ${
        selected ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
      }`}
    >
      {icon}
      {children}
    </button>
  )
}

/** A large, keyboard-reachable drop target that is still an ordinary file input. */
function FilePicker({
  file,
  disabled,
  onPick,
}: {
  file: File | null
  disabled: boolean
  onPick: (file: File | null) => void
}) {
  const [dragging, setDragging] = useState(false)

  return (
    <label
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        if (!disabled) {
          onPick(event.dataTransfer.files[0] ?? null)
        }
      }}
      className={`flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors focus-within:ring-2 focus-within:ring-ring ${
        dragging ? 'border-primary bg-accent' : 'border-input bg-secondary/40 hover:bg-secondary'
      }`}
    >
      <Upload aria-hidden="true" className="size-6 text-primary" />
      <span className="text-sm font-medium">
        {file === null ? 'Choose a video, or drop it here' : file.name}
      </span>
      <span className="text-xs text-muted-foreground">
        {file === null ? 'MP4, MOV, or WebM up to 2 GB' : formatBytes(file.size)}
      </span>
      <input
        type="file"
        accept="video/*"
        aria-label="Video file"
        disabled={disabled}
        onChange={(event) => onPick(event.target.files?.[0] ?? null)}
        className="sr-only"
      />
    </label>
  )
}

/** Say what is happening right now, with real numbers only where the browser has them. */
function SubmissionProgress({ phase }: { phase: Phase }) {
  if (phase.step === 'creating') {
    return <p role="status" className="text-sm text-muted-foreground">Creating your project…</p>
  }
  if (phase.step === 'importing') {
    return <p role="status" className="text-sm text-muted-foreground">Starting the import…</p>
  }
  if (phase.step !== 'uploading') {
    return null
  }
  const percent =
    phase.totalBytes === 0 ? 0 : Math.round((phase.uploadedBytes / phase.totalBytes) * 100)
  return (
    <div className="space-y-2">
      <p role="status" className="text-sm font-medium">
        Uploading video — {percent}%
      </p>
      <div
        role="progressbar"
        aria-label="Upload progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="h-2 overflow-hidden rounded-full bg-secondary"
      >
        <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
      </div>
      <p className="text-xs text-muted-foreground">
        Keep this window open until the upload finishes.
      </p>
    </div>
  )
}

function FailureMessage({ error }: { error: unknown }) {
  if (error instanceof ApiError) {
    return <ErrorNotice error={error} />
  }
  const message =
    error instanceof UploadRejectedError
      ? error.message
      : 'That upload could not be finished. Check your connection and try again.'
  return (
    <p role="alert" className="text-sm text-destructive">
      {message}
    </p>
  )
}

function submitLabel(phase: Phase, source: Source): string {
  if (phase.step === 'creating') return 'Creating…'
  if (phase.step === 'uploading') return 'Uploading…'
  if (phase.step === 'importing') return 'Importing…'
  if (phase.step === 'failed') return 'Try again'
  return source === 'upload' ? 'Create and upload' : 'Create and import'
}

/** A readable project name from a file name: no extension, no separators. */
export function suggestedName(filename: string): string {
  const withoutExtension = filename.replace(/\.[^./\\]+$/, '')
  const spaced = withoutExtension.replace(/[_]+/g, ' ').replace(/\s+/g, ' ').trim()
  return (spaced === '' ? filename : spaced).slice(0, 200)
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}
