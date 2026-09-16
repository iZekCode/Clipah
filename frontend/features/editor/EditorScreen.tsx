'use client'

import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  AudioLines,
  Captions,
  ClipboardCheck,
  Film,
  LayoutGrid,
  Palette,
  Redo2,
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
import { RequireSession } from '@/features/auth/require-session'
import { useSession } from '@/features/auth/session'
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
  EditResponse,
  ProjectResponse,
  ProxyPlaybackResponse,
  RevisionHistoryResponse,
} from '@/lib/api/generated/model'

import { AssetsPanel } from './AssetsPanel'
import { ExportDialog, presetForCanvas } from './ExportDialog'
import { AccessibilityPanel } from './AccessibilityPanel'
import { AudioPanel } from './AudioPanel'
import { CaptionsPanel } from './CaptionsPanel'
import { KaraokePanel } from './KaraokePanel'
import { KeyframeEditor } from './KeyframeEditor'
import { MotionPanel } from './MotionPanel'
import { Inspector } from './Inspector'
import { Player } from './Player'
import { SceneList } from './SceneList'
import { SourceMonitor } from './SourceMonitor'
import { TemplatesPanel } from './TemplatesPanel'
import { TextPanel } from './TextPanel'
import { Timeline, ZOOM_LEVELS } from './Timeline'
import { TimelineToolbar } from './TimelineToolbar'
import { Autosave, type SaveStatus } from './autosave'
import type { PreviewEngine } from './engine'
import {
  MIN_ITEM_MS,
  canRedo,
  canUndo,
  editorReducer,
  initialEditorState,
  isDirty,
  timelineItems,
  type Aspect,
  type EditorAction,
} from './store'

/** The editing tools, in the order a creator usually reaches for them. */
const TOOLS = [
  { id: 'captions', label: 'Captions', icon: Captions },
  { id: 'layout', label: 'Layout', icon: LayoutGrid },
  { id: 'media', label: 'Media', icon: Film },
  { id: 'audio', label: 'Audio', icon: AudioLines },
  { id: 'text', label: 'Text', icon: Type },
  { id: 'style', label: 'Style', icon: Palette },
  { id: 'review', label: 'Review', icon: ClipboardCheck },
] as const

type ToolId = (typeof TOOLS)[number]['id']

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

  useShortcuts({
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

  return (
    <main className="flex min-h-screen flex-col bg-background md:h-screen">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b bg-card px-4 py-2.5">
        <Link
          href={`/dashboard/projects/${edit.projectId}`}
          className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm font-medium text-muted-foreground hover:bg-secondary hover:text-foreground"
        >
          <ArrowLeft aria-hidden="true" className="size-4" />
          <span className="max-w-40 truncate">{project.data?.name ?? 'Back to project'}</span>
        </Link>
        <div className="min-w-0">
          <h1 className="text-sm font-semibold">Editing clip</h1>
          <p className="text-xs text-muted-foreground">Revision {autosave.expectedRevision}</p>
        </div>
        <p
          role="status"
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
            status === 'conflict' || status === 'offline'
              ? 'bg-warning-soft text-warning'
              : status === 'saving' || (dirty && status === 'saved')
                ? 'bg-info-soft text-info'
                : 'bg-success-soft text-success'
          }`}
        >
          {saveLabel}
        </p>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => dispatch({ type: 'undo' })}
            disabled={!canUndo(state)}
            aria-label="Undo"
            title="Undo (⌘Z)"
            className="inline-flex h-9 items-center gap-1.5 rounded-lg px-2.5 text-sm font-medium hover:bg-secondary disabled:opacity-40"
          >
            <Undo2 aria-hidden="true" className="size-4" />
            <span className="hidden sm:inline">Undo</span>
          </button>
          <button
            type="button"
            onClick={() => dispatch({ type: 'redo' })}
            disabled={!canRedo(state)}
            aria-label="Redo"
            title="Redo (⇧⌘Z)"
            className="inline-flex h-9 items-center gap-1.5 rounded-lg px-2.5 text-sm font-medium hover:bg-secondary disabled:opacity-40"
          >
            <Redo2 aria-hidden="true" className="size-4" />
            <span className="hidden sm:inline">Redo</span>
          </button>
          <button
            type="button"
            onClick={save}
            title="Save (⌘S)"
            className="inline-flex h-9 items-center rounded-lg border bg-card px-3 text-sm font-medium hover:bg-secondary"
          >
            Save
          </button>
          <button
            type="button"
            onClick={() => setExporting(true)}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3.5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
          >
            <Upload aria-hidden="true" className="size-4" />
            Export
          </button>
        </div>
      </header>

      {conflict === null ? null : (
        <div role="alert" className="border-b border-warning/30 bg-warning-soft px-4 py-3">
          <p className="text-sm font-medium text-warning">
            This clip changed since you opened it. Choose which version to keep.
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => {
                if (conflict.revision !== null) {
                  autosave.resume(conflict.revision)
                }
                onConflict(null)
                void autosave.flush()
              }}
              className="rounded-lg border bg-card px-3 py-1.5 text-xs font-medium"
            >
              Keep my version
            </button>
            <button
              type="button"
              onClick={() => {
                void onReload().then((refreshed) => {
                  if (refreshed !== null) {
                    dispatch({ type: 'replace', composition: refreshed.composition })
                    autosave.accept(refreshed.currentRevision)
                  }
                  onConflict(null)
                })
              }}
              className="rounded-lg border bg-card px-3 py-1.5 text-xs font-medium"
            >
              Take the newer version
            </button>
          </div>
        </div>
      )}

      {proxyError !== null ? (
        <div className="px-4 pt-3">
          <ErrorNotice error={proxyError} />
        </div>
      ) : null}

      <p className="mx-4 mt-3 rounded-lg bg-info-soft p-3 text-sm text-info md:hidden">
        This is a preview. Captions, timing, and layout are edited on a larger screen — open
        this clip on a tablet or computer to continue. Export and download work here.
      </p>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <nav
          aria-label="Editing tools"
          className="hidden shrink-0 border-r bg-card md:flex md:w-20 md:flex-col md:items-stretch md:gap-1 md:p-2"
        >
          <div role="tablist" aria-orientation="vertical" aria-label="Editing tools" className="flex flex-col gap-1">
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
                  className={`flex flex-col items-center gap-1 rounded-lg px-1 py-2 text-[11px] font-medium transition-colors ${
                    tool === entry.id
                      ? 'bg-accent text-accent-foreground'
                      : 'text-muted-foreground hover:bg-secondary hover:text-foreground'
                  }`}
                >
                  <Icon aria-hidden="true" className="size-5" />
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
          className="hidden shrink-0 overflow-y-auto border-r bg-card md:block md:w-72 xl:w-96"
        >
          <ToolPanel id="captions" active={tool}>
            <CaptionsPanel
              captions={composition.captions}
              onText={(wordId, text) => dispatch({ type: 'captionText', wordId, text })}
              onStyle={(patch) => dispatch({ type: 'captionStyle', patch })}
            />
            <KaraokePanel
              captions={composition.captions}
              playheadMs={state.playheadMs}
              onRetime={(wordId, startMs, endMs) =>
                dispatch({ type: 'retimeWord', wordId, startMs, endMs })
              }
              onMode={(mode) => dispatch({ type: 'captionMode', mode })}
            />
          </ToolPanel>
          <ToolPanel id="layout" active={tool}>
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
              onMotion={(targetId, preset) => dispatch({ type: 'setMotion', targetId, preset })}
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
          <ToolPanel id="style" active={tool}>
            <TemplatesPanel
              composition={composition}
              onApply={(template) => dispatch({ type: 'applyTemplate', template })}
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

        <section aria-label="Stage" className="flex min-w-0 flex-1 flex-col overflow-y-auto bg-secondary/40">
          <div className="flex flex-1 items-center justify-center p-4 lg:p-6">
            {proxy === null ? (
              <p role="status" className="text-xs text-muted-foreground">
                Loading the preview…
              </p>
            ) : (
              <div className="w-full max-w-3xl">
                <Player
                  composition={composition}
                  source={proxy}
                  playheadMs={state.playheadMs}
                  playing={playing}
                  engine={engine}
                  onSeek={(ms) => dispatch({ type: 'seek', ms })}
                  onPlayingChange={onPlaying}
                />
              </div>
            )}
          </div>
        </section>

        <aside
          aria-label="Properties"
          className="hidden shrink-0 overflow-y-auto border-l bg-card p-3 md:block md:w-56 lg:w-72"
        >
          <Inspector
            composition={composition}
            item={selected}
            playheadMs={state.playheadMs}
            onTrim={(sourceInMs, sourceOutMs) =>
              selected === null
                ? undefined
                : dispatch({ type: 'trim', itemId: selected.id, sourceInMs, sourceOutMs })
            }
            onCrop={(crop) =>
              selected === null ? undefined : dispatch({ type: 'crop', itemId: selected.id, crop })
            }
            onAspect={(aspect: Aspect) => dispatch({ type: 'aspect', aspect, sourceAspect })}
            onSplit={() =>
              selected === null
                ? undefined
                : dispatch({ type: 'split', itemId: selected.id, atMs: state.playheadMs })
            }
            onDelete={() =>
              selected === null ? undefined : dispatch({ type: 'deleteItem', itemId: selected.id })
            }
          />
        </aside>
      </div>

      <section
        aria-label="Editing lanes"
        className="hidden max-h-[40vh] shrink-0 flex-col gap-2 overflow-y-auto border-t bg-card p-3 md:flex"
      >
        <TimelineToolbar
          snapping={snapping}
          ripple={ripple}
          hasSelection={state.selectedItemId !== null}
          markerCount={composition.bookmarks.length}
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
          onSelect={(itemId) => dispatch({ type: 'select', itemId })}
          onSelectTrack={(trackId) => dispatch({ type: 'selectTrack', trackId })}
          onSeek={(ms) => dispatch({ type: 'seek', ms })}
          onZoom={onZoom}
          onMove={(itemId, toMs) => dispatch({ type: 'moveItem', itemId, toMs })}
          onResize={(itemId, edge, toMs) => dispatch({ type: 'resizeItem', itemId, edge, toMs })}
          onRemoveMarker={(bookmarkId) => dispatch({ type: 'removeBookmark', bookmarkId })}
        />
      </section>

      <ExportDialog
        open={exporting}
        onOpenChange={setExporting}
        editId={edit.id}
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

/**
 * The editor's keyboard shortcuts.
 *
 * They are deliberately inert while a member is typing: a caption is text, and an editor
 * that treats `z` inside a caption as an undo is an editor that eats words.
 */
function useShortcuts(handlers: {
  onPlayPause: () => void
  onUndo: () => void
  onRedo: () => void
  onSplit: () => void
  onDelete: () => void
  onZoomIn: () => void
  onZoomOut: () => void
  onSave: () => void
}): void {
  const current = useRef(handlers)
  current.current = handlers

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (isTextEntry(event.target)) {
        return
      }
      const modified = event.metaKey || event.ctrlKey
      if (modified && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        if (event.shiftKey) {
          current.current.onRedo()
        } else {
          current.current.onUndo()
        }
        return
      }
      if (modified && event.key.toLowerCase() === 's') {
        event.preventDefault()
        current.current.onSave()
        return
      }
      if (modified) {
        return
      }
      if (event.key === ' ') {
        event.preventDefault()
        current.current.onPlayPause()
        return
      }
      if (event.key.toLowerCase() === 's') {
        current.current.onSplit()
        return
      }
      if (event.key === 'Delete' || event.key === 'Backspace') {
        current.current.onDelete()
        return
      }
      if (event.key === '+' || event.key === '=') {
        current.current.onZoomIn()
        return
      }
      if (event.key === '-') {
        current.current.onZoomOut()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [])
}

/** Whether the event landed somewhere a member is writing. */
function isTextEntry(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false
  }
  return (
    target.isContentEditable ||
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  )
}
