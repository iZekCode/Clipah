'use client'

import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  AudioLines,
  Captions,
  ClipboardCheck,
  Crop,
  Film,
  Keyboard,
  Palette,
  Redo2,
  RotateCcw,
  SlidersHorizontal,
  Type,
  Undo2,
  Upload,
} from 'lucide-react'
import Link from 'next/link'
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import { BrollPanel, type DecisionRequest } from '@/features/broll/BrollPanel'
import { ErrorNotice } from '@/components/error-notice'
import { Button } from '@/components/ui/button'
import { IconButton } from '@/components/ui/icon-button'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { RequireSession } from '@/features/auth/require-session'
import { useSession } from '@/features/auth/session'
import { useStoryboard } from '@/features/media/use-storyboard'
import { useWaveform } from '@/features/media/use-waveform'
import { ReviewPanel } from '@/features/reviews/ReviewPanel'
import { useWorkspaceScope, WorkspaceProvider } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { showAccessibilityQualityApiV1EditsEditIdAccessibilityGet } from '@/lib/api/generated/edit-reviews/edit-reviews'
import {
  decideApiV1EditsEditIdBrollDecisionsPost,
  historyApiV1EditsEditIdRevisionsGet,
  saveApiV1EditsEditIdPut,
  showApiV1EditsEditIdGet,
} from '@/lib/api/generated/edits/edits'
import { showApiV1ProjectsProjectIdProxyGet } from '@/lib/api/generated/playback/playback'
import { showApiV1ProjectsProjectIdGet } from '@/lib/api/generated/projects/projects'
import type {
  AccessibilityResponse,
  CompositionV1,
  EditResponse,
  ProjectResponse,
  ProxyPlaybackResponse,
  RevisionHistoryResponse,
} from '@/lib/api/generated/model'
import { cn } from '@/lib/utils'

import { AssetsPanel } from './AssetsPanel'
import { ExportDialog, presetForCanvas } from './ExportDialog'
import { AccessibilityPanel } from './AccessibilityPanel'
import { AudioPanel } from './AudioPanel'
import { CAPTION_FONT_VARIABLES } from './caption-fonts'
import { CaptionsPanel } from './CaptionsPanel'
import { KaraokePanel } from './KaraokePanel'
import { CropOverlay } from './CropOverlay'
import { KeyframeEditor } from './KeyframeEditor'
import { LayoutPanel } from './LayoutPanel'
import { MotionPanel } from './MotionPanel'
import { Inspector, type InspectorTarget } from './Inspector'
import { Player } from './Player'
import { ResetEditsDialog } from './ResetEditsDialog'
import { SceneList } from './SceneList'
import { ShortcutSheet } from './ShortcutSheet'
import { SourceMonitor } from './SourceMonitor'
import { StylePanel } from './StylePanel'
import { TextPanel } from './TextPanel'
import { Timeline, ZOOM_LEVELS, fitZoom } from './Timeline'
import { TimelineToolbar } from './TimelineToolbar'
import { TransportBar } from './TransportBar'
import { Autosave, type SaveStatus } from './autosave'
import type { PreviewEngine } from './engine'
import {
  ASPECT_CANVAS,
  MIN_ITEM_MS,
  canRedo,
  canUndo,
  currentAspect,
  editorReducer,
  initialEditorState,
  isDirty,
  timelineItems,
  type Aspect,
  type EditorAction,
} from './store'
import { useEditorKeys } from './use-editor-keys'

/** The editing tools, in the order a creator usually reaches for them. */
const TOOLS = [
  { id: 'captions', label: 'Captions', icon: Captions },
  { id: 'style', label: 'Style', icon: Palette },
  { id: 'layout', label: 'Layout', icon: Crop },
  { id: 'media', label: 'Media', icon: Film },
  { id: 'audio', label: 'Audio', icon: AudioLines },
  { id: 'text', label: 'Text', icon: Type },
  { id: 'review', label: 'Review', icon: ClipboardCheck },
] as const

type ToolId = (typeof TOOLS)[number]['id']
type ItemCrop = NonNullable<CompositionV1['tracks'][number]['items'][number]['crop']>

/** What the editor says about work that has not reached the backend yet. */
const SAVE_LABELS: Record<SaveStatus, string> = {
  idle: 'Not saved yet',
  saving: 'Saving…',
  saved: 'Saved',
  offline: 'Offline — your changes are kept here',
  conflict: 'Conflict',
}

/** One editor screen, inside a confirmed Session and the Workspace that owns the Edit. */
export function EditorScreen({ editId, engine }: { editId: string; engine?: PreviewEngine }) {
  return (
    <RequireSession>
      <WorkspaceProvider>
        <EditorBody editId={editId} engine={engine} />
      </WorkspaceProvider>
    </RequireSession>
  )
}

/**
 * The editor itself: one Revision loaded, edited, and saved back as the next one.
 *
 * The composition is the only edit state on this screen. The preview, the timeline, the
 * captions panel, and the inspector all read it and all change it the same way, so what
 * a member sees is always the document that would be saved.
 */
function EditorBody({ editId, engine }: { editId: string; engine?: PreviewEngine }) {
  const { active } = useWorkspaceScope()
  const session = useSession()
  const [status, setStatus] = useState<SaveStatus>('saved')
  const [conflict, setConflict] = useState<{ revision: number | null } | null>(null)
  const [zoom, setZoom] = useState<number>(ZOOM_LEVELS[2])
  const [playing, setPlaying] = useState(false)

  const loaded = useQuery<EditResponse, ApiError>({
    queryKey: ['/api/v1/edits', active.id, editId],
    queryFn: ({ signal }) => showApiV1EditsEditIdGet(editId, { workspace_id: active.id }, { signal }),
    retry: false,
  })
  const projectId = loaded.data?.projectId ?? null
  const history = useQuery<RevisionHistoryResponse, ApiError>({
    queryKey: ['/api/v1/edit-revisions', active.id, editId],
    queryFn: ({ signal }) =>
      historyApiV1EditsEditIdRevisionsGet(editId, { workspace_id: active.id }, { signal }),
    retry: false,
    enabled: loaded.data !== undefined,
  })
  const { refetch: refetchHistory } = history
  const refreshHistory = useCallback(() => {
    void refetchHistory()
  }, [refetchHistory])
  const proxy = useQuery<ProxyPlaybackResponse, ApiError>({
    queryKey: ['/api/v1/projects/proxy', active.id, projectId],
    enabled: projectId !== null,
    queryFn: ({ signal }) =>
      showApiV1ProjectsProjectIdProxyGet(
        projectId ?? '',
        { workspace_id: active.id },
        { signal },
      ),
    retry: false,
  })

  if (loaded.isPending) {
    return (
      <main className="p-6">
        <p role="status" className="text-sm text-muted-foreground">Opening this clip…</p>
      </main>
    )
  }
  if (loaded.isError) {
    return (
      <main className="p-6">
        <ErrorNotice error={loaded.error} />
      </main>
    )
  }

  return (
    <LoadedEditor
      key={loaded.data.id}
      edit={loaded.data}
      revisionId={history.data?.revisions[0]?.id ?? null}
      workspaceId={active.id}
      canReview={active.role !== 'viewer'}
      canExport={active.role === 'owner' || active.role === 'admin' || active.role === 'editor'}
      collaborationEnabled={session.data?.capabilities.collaboration === true}
      proxy={proxy.data ?? null}
      proxyError={proxy.isError ? proxy.error : null}
      engine={engine}
      status={status}
      onStatus={setStatus}
      conflict={conflict}
      onConflict={setConflict}
      zoom={zoom}
      onZoom={setZoom}
      playing={playing}
      onPlaying={setPlaying}
      onReload={async () => {
        const refreshed = await loaded.refetch()
        await history.refetch()
        return refreshed.data ?? null
      }}
      onRevisionSaved={refreshHistory}
    />
  )
}

function LoadedEditor({
  edit,
  revisionId,
  workspaceId,
  canReview,
  canExport,
  collaborationEnabled,
  proxy,
  proxyError,
  engine,
  status,
  onStatus,
  conflict,
  onConflict,
  zoom,
  onZoom,
  playing,
  onPlaying,
  onReload,
  onRevisionSaved,
}: {
  edit: EditResponse
  revisionId: string | null
  workspaceId: string
  canReview: boolean
  canExport: boolean
  collaborationEnabled: boolean
  proxy: ProxyPlaybackResponse | null
  proxyError: ApiError | null
  engine?: PreviewEngine
  status: SaveStatus
  onStatus: (status: SaveStatus) => void
  conflict: { revision: number | null } | null
  onConflict: (conflict: { revision: number | null } | null) => void
  zoom: number
  onZoom: (zoom: number) => void
  playing: boolean
  onPlaying: (playing: boolean) => void
  onReload: () => Promise<EditResponse | null>
  onRevisionSaved: () => void
}) {
  const [state, dispatch] = useReducer(editorReducer, edit.composition, initialEditorState)
  const [snapping, setSnapping] = useState(true)
  const [ripple, setRipple] = useState(false)
  const composition = state.composition
  const dirty = isDirty(state)

  const autosave = useMemo(
    () =>
      new Autosave({
        editId: edit.id,
        revision: edit.currentRevision,
        save: async (document, expectedRevision) => {
          const saved = await saveApiV1EditsEditIdPut(
            edit.id,
            { expectedRevision, composition: document as unknown as Record<string, unknown> },
            { workspace_id: workspaceId },
          )
          return { currentRevision: saved.currentRevision, composition: saved.composition }
        },
        onStatus,
        onSaved: (result) => {
          dispatch({ type: 'markSaved', composition: result.composition })
          onRevisionSaved()
        },
        onConflict: (revision) => {
          onConflict({ revision })
        },
      }),
    [edit.id, edit.currentRevision, workspaceId, onStatus, onConflict, onRevisionSaved],
  )

  useEffect(() => () => autosave.dispose(), [autosave])

  // Every change to the document is offered to the backend; nothing else is.
  useEffect(() => {
    if (dirty) {
      autosave.queue(composition)
    }
  }, [autosave, composition, dirty])

  // An unsent change waits for the connection rather than being lost with it.
  useEffect(() => {
    const retry = () => {
      void autosave.retryPending()
    }
    window.addEventListener('online', retry)
    return () => {
      window.removeEventListener('online', retry)
    }
  }, [autosave])

  const save = useCallback(() => {
    void autosave.flush()
  }, [autosave])

  const [deciding, setDeciding] = useState(false)

  /**
   * Answer one B-roll proposal, and save the document that answer produced.
   *
   * The decision and its Revision are one event, so they travel in one request: the
   * next composition is computed here rather than read back from React state, because
   * the backend must be told exactly the document the member's click produced.
   */
  const decide = useCallback(
    async (request: DecisionRequest) => {
      const change = compositionChangeFor(request)
      const next = change === null ? state : editorReducer(state, change)
      if (change !== null) {
        dispatch(change)
      }
      setDeciding(true)
      try {
        const saved = await decideApiV1EditsEditIdBrollDecisionsPost(
          edit.id,
          {
            expectedRevision: autosave.expectedRevision,
            composition: next.composition as unknown as Record<string, unknown>,
            decision: {
              suggestionId: request.suggestion.id,
              action: request.action,
              ...(request.assetId === undefined ? {} : { assetId: request.assetId }),
            },
          },
          { workspace_id: workspaceId },
        )
        autosave.accept(saved.currentRevision)
        dispatch({ type: 'markSaved', composition: saved.composition })
        onRevisionSaved()
      } catch (error) {
        const refused = error as ApiError
        if (refused.code === 'EDIT_REVISION_CONFLICT') {
          onConflict({ revision: refused.currentRevision })
          return
        }
        throw error
      } finally {
        setDeciding(false)
      }
    },
    [autosave, edit.id, onConflict, onRevisionSaved, state, workspaceId],
  )

  const selected = useMemo(
    () =>
      timelineItems(composition).find((placed) => placed.item.id === state.selectedItemId)?.item ??
      null,
    [composition, state.selectedItemId],
  )
  // The Layout tool frames the selected video item, or the clip's first one.
  const framed = useMemo(() => {
    const video = composition.tracks.filter((track) => track.type === 'video')
    const items = video.flatMap((track) => track.items)
    return items.find((item) => item.id === state.selectedItemId) ?? items[0] ?? null
  }, [composition.tracks, state.selectedItemId])
  const sourceAspect = useMemo(() => {
    if (proxy?.width == null || proxy.height == null || proxy.height === 0) {
      return composition.canvas.width / composition.canvas.height
    }
    return proxy.width / proxy.height
  }, [proxy, composition.canvas.height, composition.canvas.width])

  /** Move the playhead to the nearest marker in one direction, if there is one. */
  const toMarker = useCallback(
    (direction: 1 | -1) => {
      const ordered = [...composition.bookmarks].sort(
        (left, right) => left.timelineMs - right.timelineMs,
      )
      const next =
        direction === 1
          ? ordered.find((bookmark) => bookmark.timelineMs > state.playheadMs)
          : [...ordered].reverse().find((bookmark) => bookmark.timelineMs < state.playheadMs)
      if (next !== undefined) {
        dispatch({ type: 'seek', ms: next.timelineMs })
      }
    },
    [composition.bookmarks, state.playheadMs],
  )

  const [loop, setLoop] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [laneHeight, setLaneHeight] = useState(240)
  const [propertiesOpen, setPropertiesOpen] = useState(false)
  const lanes = useRef<HTMLElement>(null)
  const [focus, setFocus] = useState<InspectorTarget>(null)
  const [previewCrop, setPreviewCrop] = useState<ItemCrop | null>(null)
  // A selected timeline item wins; otherwise the word or overlay chosen last.
  const target: InspectorTarget =
    state.selectedItemId !== null ? { kind: 'item', id: state.selectedItemId } : focus
  const storyboard = useStoryboard(edit.projectId, { enabled: true })
  const waveform = useWaveform(edit.projectId, { enabled: true })

  useEditorKeys({
    onPlayPause: () => onPlaying(!playing),
    onUndo: () => dispatch({ type: 'undo' }),
    onRedo: () => dispatch({ type: 'redo' }),
    onSplit: () =>
      state.selectedItemId === null
        ? undefined
        : dispatch({ type: 'split', itemId: state.selectedItemId, atMs: state.playheadMs }),
    onDelete: () =>
      state.selectedItemId === null
        ? undefined
        : dispatch({ type: 'deleteItem', itemId: state.selectedItemId }),
    onZoomIn: () => onZoom(ZOOM_LEVELS[Math.min(ZOOM_LEVELS.indexOf(zoom as never) + 1, 4)] ?? zoom),
    onZoomOut: () =>
      onZoom(ZOOM_LEVELS[Math.max(ZOOM_LEVELS.indexOf(zoom as never) - 1, 0)] ?? zoom),
    onSave: save,
    onStep: (deltaMs) =>
      dispatch({
        type: 'seek',
        ms: Math.min(
          composition.durationMs,
          Math.max(0, Math.round(state.playheadMs + deltaMs)),
        ),
      }),
    onAddMarker: () =>
      dispatch({ type: 'addBookmark', label: `Marker ${composition.bookmarks.length + 1}` }),
    onHelp: () => setHelpOpen(true),
  })

  const [tool, setTool] = useState<ToolId>('captions')
  const [exporting, setExporting] = useState(false)
  const project = useQuery<ProjectResponse, ApiError>({
    queryKey: ['/api/v1/projects', workspaceId, edit.projectId],
    queryFn: ({ signal }) =>
      showApiV1ProjectsProjectIdGet(edit.projectId, { workspace_id: workspaceId }, { signal }),
    retry: false,
  })
  const saveLabel = dirty && status === 'saved' ? SAVE_LABELS.idle : SAVE_LABELS[status]

  const inspector = (
    <Inspector
      composition={composition}
      target={target}
      playheadMs={state.playheadMs}
      onTrim={(sourceInMs, sourceOutMs) =>
        selected === null
          ? undefined
          : dispatch({ type: 'trim', itemId: selected.id, sourceInMs, sourceOutMs })
      }
      onCrop={(crop) =>
        selected === null ? undefined : dispatch({ type: 'crop', itemId: selected.id, crop })
      }
      onSplit={() =>
        selected === null
          ? undefined
          : dispatch({ type: 'split', itemId: selected.id, atMs: state.playheadMs })
      }
      onDelete={() =>
        selected === null ? undefined : dispatch({ type: 'deleteItem', itemId: selected.id })
      }
      onRetimeWord={(wordId, startMs, endMs) =>
        dispatch({ type: 'retimeWord', wordId, startMs, endMs })
      }
      onWordText={(wordId, text) => dispatch({ type: 'captionText', wordId, text })}
      onMoveOverlay={(overlayId, startMs, endMs) =>
        dispatch({ type: 'moveOverlay', overlayId, startMs, endMs })
      }
    />
  )

  return (
    <main
      className={cn('flex min-h-screen flex-col bg-background md:h-screen', CAPTION_FONT_VARIABLES)}
    >
      <header className="flex h-12 shrink-0 items-center gap-3 border-b bg-card px-3">
        <Link
          href={`/dashboard/projects/${edit.projectId}`}
          className="inline-flex min-w-0 items-center gap-1.5 rounded-md px-2 py-1.5 text-small font-medium text-muted-foreground hover:bg-secondary hover:text-foreground"
        >
          <ArrowLeft aria-hidden="true" strokeWidth={1.75} className="size-4 shrink-0" />
          <span className="max-w-48 truncate">{project.data?.name ?? 'Back to project'}</span>
        </Link>
        <div className="min-w-0">
          <h1 className="sr-only truncate text-small font-semibold sm:not-sr-only">Editing clip</h1>
          <p className="hidden font-mono text-caption text-subtle-foreground sm:block">
            Revision {autosave.expectedRevision}
          </p>
        </div>
        <p
          role="status"
          className={cn(
            'hidden text-caption font-medium sm:block',
            status === 'conflict' || status === 'offline'
              ? 'text-warning'
              : 'text-muted-foreground',
          )}
        >
          {saveLabel}
        </p>
        <div className="ml-auto flex items-center gap-1">
          <IconButton
            label="Undo"
            shortcut="⌘Z"
            icon={<Undo2 strokeWidth={1.75} />}
            className="hidden md:inline-flex"
            disabled={!canUndo(state)}
            onClick={() => dispatch({ type: 'undo' })}
          />
          <IconButton
            label="Redo"
            shortcut="⇧⌘Z"
            icon={<Redo2 strokeWidth={1.75} />}
            className="hidden md:inline-flex"
            disabled={!canRedo(state)}
            onClick={() => dispatch({ type: 'redo' })}
          />
          <IconButton
            label="Reset edits"
            icon={<RotateCcw strokeWidth={1.75} />}
            className="hidden md:inline-flex"
            // Revision 1 with nothing changed is already the clip as it was first opened.
            disabled={autosave.expectedRevision === 1 && !dirty}
            onClick={() => setResetOpen(true)}
          />
          <IconButton
            label="Editor shortcuts"
            shortcut="?"
            icon={<Keyboard strokeWidth={1.75} />}
            className="hidden md:inline-flex"
            onClick={() => setHelpOpen(true)}
          />
          <IconButton
            label="Properties"
            icon={<SlidersHorizontal strokeWidth={1.75} />}
            className="lg:hidden"
            aria-expanded={propertiesOpen}
            onClick={() => setPropertiesOpen(true)}
          />
          <Button variant="ghost" size="sm" onClick={save}>
            Save
          </Button>
          <Button size="sm" onClick={() => setExporting(true)}>
            <Upload aria-hidden="true" strokeWidth={1.75} /> Export
          </Button>
        </div>
      </header>

      {conflict === null ? null : (
        <div role="alert" className="border-b border-warning/40 bg-warning-soft px-4 py-3">
          <p className="text-small font-medium text-warning">
            This clip changed since you opened it. Choose which version to keep.
          </p>
          <div className="mt-2 flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                if (conflict.revision !== null) {
                  autosave.resume(conflict.revision)
                }
                onConflict(null)
                void autosave.flush()
              }}
            >
              Keep my version
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                void onReload().then((refreshed) => {
                  if (refreshed !== null) {
                    dispatch({ type: 'replace', composition: refreshed.composition })
                    autosave.accept(refreshed.currentRevision)
                  }
                  onConflict(null)
                })
              }}
            >
              Take the newer version
            </Button>
          </div>
        </div>
      )}

      {proxyError !== null ? (
        <div className="px-4 pt-3">
          <ErrorNotice error={proxyError} />
        </div>
      ) : null}

      <p className="mx-4 mt-3 rounded-md border border-line-strong bg-card p-3 text-small text-muted-foreground md:hidden">
        This is a preview. Captions, timing, and layout are edited on a larger screen — open
        this clip on a tablet or computer to continue. Export and download work here.
      </p>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <nav
          aria-label="Editing tools"
          className="hidden w-14 shrink-0 border-r bg-card md:flex md:flex-col md:items-center md:gap-1 md:py-2"
        >
          <div
            role="tablist"
            aria-orientation="vertical"
            aria-label="Editing tools"
            className="flex flex-col gap-1"
          >
            {TOOLS.map((entry) => {
              const Icon = entry.icon
              return (
                <button
                  key={entry.id}
                  type="button"
                  role="tab"
                  id={`editor-tool-${entry.id}`}
                  aria-selected={tool === entry.id}
                  aria-controls={`editor-panel-${entry.id}`}
                  onClick={() => setTool(entry.id)}
                  className={cn(
                    'flex w-12 flex-col items-center gap-0.5 rounded-md py-2 text-[11px] font-medium transition-colors duration-fast ease-signal',
                    tool === entry.id
                      ? 'bg-secondary text-primary'
                      : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
                  )}
                >
                  <Icon aria-hidden="true" strokeWidth={1.75} className="size-5" />
                  {entry.label}
                </button>
              )
            })}
          </div>
        </nav>

        {/*
          Every tool panel stays mounted and is only hidden when another tool is chosen, so
          switching tools never throws away a half-typed caption, a draft overlay, or an
          open B-roll decision.
        */}
        <aside
          aria-label="Tool panel"
          className="hidden w-80 shrink-0 overflow-y-auto border-r bg-card md:block"
        >
          <ToolPanel id="captions" active={tool}>
            <CaptionsPanel
              captions={composition.captions}
              playheadMs={state.playheadMs}
              selectedWordId={focus?.kind === 'word' ? focus.id : null}
              onText={(wordId, text) => dispatch({ type: 'captionText', wordId, text })}
              onSeek={(ms) => dispatch({ type: 'seek', ms })}
              onSelectWord={(wordId) => {
                dispatch({ type: 'select', itemId: null })
                setFocus({ kind: 'word', id: wordId })
              }}
              timing={
                <KaraokePanel
                  captions={composition.captions}
                  playheadMs={state.playheadMs}
                  onRetime={(wordId, startMs, endMs) =>
                    dispatch({ type: 'retimeWord', wordId, startMs, endMs })
                  }
                  onMode={(mode) => dispatch({ type: 'captionMode', mode })}
                />
              }
            />
          </ToolPanel>
          <ToolPanel id="style" active={tool}>
            <StylePanel
              composition={composition}
              onStyle={(patch) => dispatch({ type: 'captionStyle', patch })}
              onApplyTemplate={(template) => dispatch({ type: 'applyTemplate', template })}
              motion={
                <>
                  <KeyframeEditor
                    item={selected}
                    playheadMs={state.playheadMs}
                    onAdd={(atMs, transform) =>
                      selected === null
                        ? undefined
                        : dispatch({ type: 'addKeyframe', targetId: selected.id, atMs, transform })
                    }
                    onMove={(atMs, toMs) =>
                      selected === null
                        ? undefined
                        : dispatch({ type: 'moveKeyframe', targetId: selected.id, atMs, toMs })
                    }
                    onRemove={(atMs) =>
                      selected === null
                        ? undefined
                        : dispatch({ type: 'removeKeyframe', targetId: selected.id, atMs })
                    }
                  />
                  <MotionPanel
                    composition={composition}
                    onMotion={(targetId, preset) =>
                      dispatch({ type: 'setMotion', targetId, preset })
                    }
                  />
                </>
              }
            />
          </ToolPanel>
          <ToolPanel id="layout" active={tool}>
            <LayoutPanel
              item={framed}
              sourceAspect={sourceAspect}
              canvasAspect={composition.canvas.width / composition.canvas.height}
              onCrop={(crop) =>
                framed === null ? undefined : dispatch({ type: 'crop', itemId: framed.id, crop })
              }
            />
          </ToolPanel>
          <ToolPanel id="media" active={tool}>
            <BrollPanel
              projectId={edit.projectId}
              candidateId={edit.candidateId}
              workspaceId={workspaceId}
              clipStartMs={composition.sourceRange.inMs}
              deciding={deciding}
              onDecide={(request) => {
                void decide(request)
              }}
            />
            <AssetsPanel
              projectId={edit.projectId}
              workspaceId={workspaceId}
              onAdd={(asset) =>
                dispatch({
                  type: 'addSound',
                  kind: 'music',
                  assetId: asset.id,
                  atMs: state.playheadMs,
                  sourceInMs: 0,
                  sourceOutMs: asset.durationMs ?? MIN_ITEM_MS * 10,
                })
              }
              onExtract={(asset) =>
                dispatch({
                  type: 'addSound',
                  kind: 'extractedAudio',
                  assetId: asset.id,
                  atMs: state.playheadMs,
                  sourceInMs: 0,
                  sourceOutMs: asset.durationMs ?? MIN_ITEM_MS * 10,
                })
              }
            />
            <SourceMonitor
              composition={composition}
              source={proxy}
              markInMs={state.markInMs}
              markOutMs={state.markOutMs}
              onMarkIn={(ms) => dispatch({ type: 'markIn', ms })}
              onMarkOut={(ms) => dispatch({ type: 'markOut', ms })}
              onClear={() => dispatch({ type: 'clearMarks' })}
              onAdd={() => dispatch({ type: 'addFromSource' })}
            />
          </ToolPanel>
          <ToolPanel id="audio" active={tool}>
            <AudioPanel
              composition={composition}
              lockedTrackIds={state.lockedTrackIds}
              onAudio={(patch) => dispatch({ type: 'audio', patch })}
              onAddTrack={(trackType) => dispatch({ type: 'addTrack', trackType })}
              onToggleLock={(trackId) => dispatch({ type: 'toggleTrackLock', trackId })}
            />
          </ToolPanel>
          <ToolPanel id="text" active={tool}>
            <TextPanel
              overlays={composition.overlays}
              onAdd={(text) => dispatch({ type: 'addText', text })}
              onUpdate={(overlayId, patch) => dispatch({ type: 'updateOverlay', overlayId, patch })}
              onMove={(overlayId, startMs, endMs) =>
                dispatch({ type: 'moveOverlay', overlayId, startMs, endMs })
              }
              onRemove={(overlayId) => dispatch({ type: 'deleteOverlay', overlayId })}
            />
          </ToolPanel>
          <ToolPanel id="review" active={tool}>
            <SceneList
              composition={composition}
              onSeek={(ms) => dispatch({ type: 'seek', ms })}
              onLabel={(atMs, label) => {
                // Naming a scene twice renames its marker rather than leaving two.
                const existing = composition.bookmarks.find(
                  (bookmark) => bookmark.timelineMs === atMs,
                )
                dispatch(
                  existing === undefined
                    ? { type: 'addBookmark', label, atMs }
                    : { type: 'renameBookmark', bookmarkId: existing.id, label },
                )
              }}
            />
            {revisionId === null ? null : (
              <RevisionQualityPanels
                editId={edit.id}
                revisionId={revisionId}
                workspaceId={workspaceId}
                canReview={canReview}
                collaborationEnabled={collaborationEnabled}
                playheadMs={state.playheadMs}
                onSelect={(itemId, timeMs) => {
                  if (timeMs !== null) dispatch({ type: 'seek', ms: timeMs })
                  if (itemId !== null) dispatch({ type: 'select', itemId })
                }}
              />
            )}
          </ToolPanel>
        </aside>

        <section
          aria-label="Stage"
          className="flex min-w-0 flex-1 flex-col gap-3 bg-stage p-3 lg:p-4"
        >
          <div className="flex items-center justify-center">
            <SegmentedControl
              label="Canvas shape"
              size="sm"
              value={currentAspect(composition)}
              options={(Object.keys(ASPECT_CANVAS) as Aspect[]).map((aspect) => ({
                value: aspect,
                label: aspect,
              }))}
              onChange={(aspect) => dispatch({ type: 'aspect', aspect, sourceAspect })}
            />
          </div>
          <div className="flex min-h-0 flex-1 items-center justify-center">
            {proxy === null ? (
              <p role="status" className="text-caption text-muted-foreground">
                Loading the preview…
              </p>
            ) : (
              <Player
                composition={composition}
                source={proxy}
                playheadMs={state.playheadMs}
                playing={playing}
                loop={loop}
                engine={engine}
                showFullFrame={tool === 'layout' && framed?.crop != null}
                overlay={
                  tool === 'layout' && framed !== null && framed.crop !== null ? (
                    <CropOverlay
                      crop={previewCrop ?? framed.crop}
                      onPreview={setPreviewCrop}
                      onCommit={(crop) => {
                        setPreviewCrop(null)
                        dispatch({ type: 'crop', itemId: framed.id, crop })
                      }}
                    />
                  ) : null
                }
                onSeek={(ms) => dispatch({ type: 'seek', ms })}
                onPlayingChange={onPlaying}
              />
            )}
          </div>
          <TransportBar
            playing={playing}
            playheadMs={state.playheadMs}
            durationMs={composition.durationMs}
            loop={loop}
            onPlayingChange={onPlaying}
            onSeek={(ms) => dispatch({ type: 'seek', ms })}
            onLoop={setLoop}
          />
        </section>

        <aside
          aria-label="Properties"
          className="hidden w-72 shrink-0 overflow-y-auto border-l bg-card p-3 lg:block"
        >
          {inspector}
        </aside>
      </div>

      <section
        ref={lanes}
        aria-label="Editing lanes"
        className="hidden shrink-0 flex-col border-t bg-card md:flex"
        style={{ height: laneHeight }}
      >
        <div
          role="separator"
          aria-label="Resize the timeline"
          aria-orientation="horizontal"
          aria-valuemin={160}
          aria-valuemax={520}
          aria-valuenow={laneHeight}
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === 'ArrowUp') {
              event.preventDefault()
              setLaneHeight((height) => Math.min(520, height + 24))
            }
            if (event.key === 'ArrowDown') {
              event.preventDefault()
              setLaneHeight((height) => Math.max(160, height - 24))
            }
          }}
          onPointerDown={(event) => {
            const startY = event.clientY
            const startHeight = laneHeight
            const move = (moveEvent: PointerEvent) =>
              setLaneHeight(Math.min(520, Math.max(160, startHeight + startY - moveEvent.clientY)))
            const up = () => {
              window.removeEventListener('pointermove', move)
              window.removeEventListener('pointerup', up)
            }
            window.addEventListener('pointermove', move)
            window.addEventListener('pointerup', up)
          }}
          className="h-1.5 shrink-0 cursor-row-resize bg-border hover:bg-line-strong focus-visible:bg-primary"
        />
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3">
          <TimelineToolbar
            snapping={snapping}
            ripple={ripple}
            hasSelection={state.selectedItemId !== null}
            markerCount={composition.bookmarks.length}
            zoom={zoom}
            onZoom={onZoom}
            onFit={() =>
              onZoom(fitZoom((lanes.current?.clientWidth ?? 1_200) - 112, composition.durationMs))
            }
            onSnapping={setSnapping}
            onRipple={setRipple}
            onSplit={() =>
              state.selectedItemId === null
                ? undefined
                : dispatch({ type: 'split', itemId: state.selectedItemId, atMs: state.playheadMs })
            }
            onSplitAwayLeft={() =>
              state.selectedItemId === null
                ? undefined
                : dispatch({
                    type: 'splitSide',
                    itemId: state.selectedItemId,
                    atMs: state.playheadMs,
                    keep: 'right',
                  })
            }
            onSplitAwayRight={() =>
              state.selectedItemId === null
                ? undefined
                : dispatch({
                    type: 'splitSide',
                    itemId: state.selectedItemId,
                    atMs: state.playheadMs,
                    keep: 'left',
                  })
            }
            onDuplicate={() =>
              state.selectedItemId === null
                ? undefined
                : dispatch({ type: 'duplicateItem', itemId: state.selectedItemId })
            }
            onDelete={() =>
              state.selectedItemId === null
                ? undefined
                : dispatch({ type: 'deleteItem', itemId: state.selectedItemId, ripple })
            }
            onAddMarker={(label) => dispatch({ type: 'addBookmark', label })}
            onPreviousMarker={() => toMarker(-1)}
            onNextMarker={() => toMarker(1)}
            onAddTrack={(trackType) => dispatch({ type: 'addTrack', trackType })}
          />
          <Timeline
            composition={composition}
            selectedItemId={state.selectedItemId}
            selectedTrackId={state.selectedTrackId}
            playheadMs={state.playheadMs}
            zoom={zoom}
            snapping={snapping}
            lockedTrackIds={state.lockedTrackIds}
            storyboard={storyboard.data ?? null}
            peaks={waveform.peaks}
            peaksPerSecond={waveform.peaksPerSecond}
            sourceAssetId={composition.sourceAssetId}
            onSelect={(itemId) => {
              setFocus(null)
              dispatch({ type: 'select', itemId })
            }}
            onSelectOverlay={(overlayId) => {
              setFocus({ kind: 'overlay', id: overlayId })
              dispatch({ type: 'select', itemId: null })
            }}
            onSelectTrack={(trackId) => dispatch({ type: 'selectTrack', trackId })}
            onSeek={(ms) => dispatch({ type: 'seek', ms })}
            onMove={(itemId, toMs) => dispatch({ type: 'moveItem', itemId, toMs })}
            onResize={(itemId, edge, toMs) => dispatch({ type: 'resizeItem', itemId, edge, toMs })}
            onRemoveMarker={(bookmarkId) => dispatch({ type: 'removeBookmark', bookmarkId })}
          />
        </div>
      </section>

      <Sheet open={propertiesOpen} onOpenChange={setPropertiesOpen}>
        <SheetContent
          side="right"
          className="w-full overflow-y-auto sm:max-w-sm lg:hidden"
          aria-describedby={undefined}
        >
          <SheetHeader>
            <SheetTitle>Properties</SheetTitle>
          </SheetHeader>
          <div className="mt-4">{propertiesOpen ? inspector : null}</div>
        </SheetContent>
      </Sheet>
      <ShortcutSheet open={helpOpen} onOpenChange={setHelpOpen} />
      <ResetEditsDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        editId={edit.id}
        workspaceId={workspaceId}
        onReset={(original) => dispatch({ type: 'reset', composition: original })}
      />

      <ExportDialog
        open={exporting}
        onOpenChange={setExporting}
        editId={edit.id}
        projectId={edit.projectId}
        sourceRange={composition.sourceRange}
        workspaceId={workspaceId}
        defaultPreset={presetForCanvas(composition.canvas.width, composition.canvas.height)}
        mayExport={canExport}
        saveNow={() => autosave.saveNow()}
        currentRevision={() => autosave.expectedRevision}
      />
    </main>
  )
}

/** One editing tool's panel, kept mounted while another tool is showing. */
function ToolPanel({ id, active, children }: { id: ToolId; active: ToolId; children: ReactNode }) {
  return (
    <div
      role="tabpanel"
      id={`editor-panel-${id}`}
      aria-labelledby={`editor-tool-${id}`}
      data-state={id === active ? 'active' : 'inactive'}
      className={id === active ? 'flex flex-col gap-4 overflow-x-auto p-4' : 'hidden'}
    >
      {children}
    </div>
  )
}

/** Immutable review and quality reads kept separate from draft composition state. */
function RevisionQualityPanels({
  editId,
  revisionId,
  workspaceId,
  canReview,
  collaborationEnabled,
  playheadMs,
  onSelect,
}: {
  editId: string
  revisionId: string
  workspaceId: string
  canReview: boolean
  collaborationEnabled: boolean
  playheadMs: number
  onSelect: (itemId: string | null, timeMs: number | null) => void
}) {
  const quality = useQuery<AccessibilityResponse, ApiError>({
    queryKey: ['/api/v1/edit-accessibility', workspaceId, editId, revisionId],
    queryFn: ({ signal }) =>
      showAccessibilityQualityApiV1EditsEditIdAccessibilityGet(
        editId,
        { workspace_id: workspaceId, revision_id: revisionId },
        { signal },
      ),
    retry: false,
  })

  return (
    <>
      {quality.isPending ? <p role="status">Checking accessibility…</p> : null}
      {quality.isError ? <ErrorNotice error={quality.error} /> : null}
      {quality.data === undefined ? null : (
        <AccessibilityPanel
          warnings={quality.data.warnings}
          onSelect={({ itemId, timeMs }) => onSelect(itemId, timeMs)}
        />
      )}
      {collaborationEnabled ? (
        <ReviewPanel
          editId={editId}
          revisionId={revisionId}
          workspaceId={workspaceId}
          canReview={canReview}
          playheadMs={playheadMs}
        />
      ) : null}
    </>
  )
}

/**
 * The document change one B-roll decision makes, or nothing when it makes none.
 *
 * Rejecting a proposal is an answer about the proposal rather than an edit to the clip,
 * so it changes no document and produces no Revision.
 */
function compositionChangeFor(request: DecisionRequest): EditorAction | null {
  if (request.action === 'accept' && request.placement !== undefined) {
    return { type: 'acceptSuggestion', placement: request.placement }
  }
  if (request.action === 'replace' && request.assetId !== undefined) {
    return {
      type: 'replaceSuggestionMedia',
      suggestionId: request.suggestion.id,
      assetId: request.assetId,
    }
  }
  if (request.action === 'remove') {
    return { type: 'removeSuggestion', suggestionId: request.suggestion.id }
  }
  return null
}
