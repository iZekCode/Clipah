'use client'

import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { RequireSession } from '@/features/auth/require-session'
import { useWorkspaceScope, WorkspaceProvider } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import {
  saveApiV1EditsEditIdPut,
  showApiV1EditsEditIdGet,
} from '@/lib/api/generated/edits/edits'
import { showApiV1ProjectsProjectIdProxyGet } from '@/lib/api/generated/playback/playback'
import type { EditResponse, ProxyPlaybackResponse } from '@/lib/api/generated/model'

import { AssetsPanel } from './AssetsPanel'
import { AudioPanel } from './AudioPanel'
import { CaptionsPanel } from './CaptionsPanel'
import { Inspector } from './Inspector'
import { Player } from './Player'
import { SceneList } from './SceneList'
import { SourceMonitor } from './SourceMonitor'
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
} from './store'

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
      <p role="status" className="p-6 text-sm text-muted-foreground">
        Opening this clip…
      </p>
    )
  }
  if (loaded.isError) {
    return (
      <div className="p-6">
        <ErrorNotice error={loaded.error} />
      </div>
    )
  }

  return (
    <LoadedEditor
      key={loaded.data.id}
      edit={loaded.data}
      workspaceId={active.id}
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
        return refreshed.data ?? null
      }}
    />
  )
}

function LoadedEditor({
  edit,
  workspaceId,
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
}: {
  edit: EditResponse
  workspaceId: string
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
        },
        onConflict: (revision) => {
          onConflict({ revision })
        },
      }),
    [edit.id, edit.currentRevision, workspaceId, onStatus, onConflict],
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

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-4 p-4">
      <header className="flex items-center gap-2">
        <h1 className="text-lg font-semibold">Editing clip</h1>
        <p className="text-xs text-muted-foreground">Revision {autosave.expectedRevision}</p>
      </header>

      {conflict === null ? null : (
        <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-4">
          <p className="text-sm font-medium text-destructive">
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
              className="rounded border px-3 py-1 text-xs"
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
              className="rounded border px-3 py-1 text-xs"
            >
              Take the newer version
            </button>
          </div>
        </div>
      )}

      {proxyError !== null ? <ErrorNotice error={proxyError} /> : null}

      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex flex-col gap-4">
          {proxy === null ? (
            <p role="status" className="text-xs text-muted-foreground">
              Loading the preview…
            </p>
          ) : (
            <Player
              composition={composition}
              source={proxy}
              playheadMs={state.playheadMs}
              playing={playing}
              engine={engine}
              onSeek={(ms) => dispatch({ type: 'seek', ms })}
              onPlayingChange={onPlaying}
            />
          )}
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
        </div>
        <div className="flex flex-col gap-4">
          <Inspector
            composition={composition}
            item={selected}
            status={status}
            dirty={dirty}
            canUndo={canUndo(state)}
            canRedo={canRedo(state)}
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
            onUndo={() => dispatch({ type: 'undo' })}
            onRedo={() => dispatch({ type: 'redo' })}
            onSave={save}
          />
          <CaptionsPanel
            captions={composition.captions}
            onText={(wordId, text) => dispatch({ type: 'captionText', wordId, text })}
            onStyle={(patch) => dispatch({ type: 'captionStyle', patch })}
          />
          <TextPanel
            overlays={composition.overlays}
            onAdd={(text) => dispatch({ type: 'addText', text })}
            onUpdate={(overlayId, patch) => dispatch({ type: 'updateOverlay', overlayId, patch })}
            onMove={(overlayId, startMs, endMs) =>
              dispatch({ type: 'moveOverlay', overlayId, startMs, endMs })
            }
            onRemove={(overlayId) => dispatch({ type: 'deleteOverlay', overlayId })}
          />
          <AudioPanel
            composition={composition}
            lockedTrackIds={state.lockedTrackIds}
            onAudio={(patch) => dispatch({ type: 'audio', patch })}
            onAddTrack={(trackType) => dispatch({ type: 'addTrack', trackType })}
            onToggleLock={(trackId) => dispatch({ type: 'toggleTrackLock', trackId })}
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
        </div>
      </div>
    </main>
  )
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
