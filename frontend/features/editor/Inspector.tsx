'use client'

import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { TimecodeInput } from '@/components/ui/timecode-input'
import type { CompositionV1 } from '@/lib/api/generated/model'
import { formatTimecode } from '@/lib/time/timecode'

import { MIN_ITEM_MS, MIN_WORD_MS } from './store'

type TrackItem = CompositionV1['tracks'][number]['items'][number]

/** What the inspector is describing: a timeline item, a caption word, an overlay, or nothing. */
export type InspectorTarget =
  | { kind: 'item'; id: string }
  | { kind: 'word'; id: string }
  | { kind: 'overlay'; id: string }
  | null

/** The shortest an overlay may be, so its end always follows its start. */
const MIN_OVERLAY_MS = 100

/**
 * The exact values behind whatever is selected, as fields a keyboard and a screen reader
 * can use.
 *
 * Times are typed as timecodes rather than dragged, because a member who knows the exact
 * frame they want should not have to find it with a mouse.
 */
export function Inspector({
  composition,
  target,
  playheadMs,
  onTrim,
  onCrop,
  onSplit,
  onDelete,
  onRetimeWord,
  onWordText,
  onMoveOverlay,
}: {
  composition: CompositionV1
  target: InspectorTarget
  playheadMs: number
  onTrim: (sourceInMs: number, sourceOutMs: number) => void
  onCrop: (crop: TrackItem['crop']) => void
  onSplit: () => void
  onDelete: () => void
  onRetimeWord: (wordId: string, startMs: number, endMs: number) => void
  onWordText: (wordId: string, text: string) => void
  onMoveOverlay: (overlayId: string, startMs: number, endMs: number) => void
}) {
  return (
    <section aria-label="Inspector" className="space-y-4">
      <h2 className="text-caption font-medium uppercase tracking-wide text-subtle-foreground">
        Inspector
      </h2>
      {target === null ? (
        <CanvasFacts composition={composition} />
      ) : target.kind === 'item' ? (
        <ItemFields
          composition={composition}
          itemId={target.id}
          playheadMs={playheadMs}
          onTrim={onTrim}
          onCrop={onCrop}
          onSplit={onSplit}
          onDelete={onDelete}
        />
      ) : target.kind === 'word' ? (
        <WordFields
          composition={composition}
          wordId={target.id}
          onRetimeWord={onRetimeWord}
          onWordText={onWordText}
        />
      ) : (
        <OverlayFields
          composition={composition}
          overlayId={target.id}
          onMoveOverlay={onMoveOverlay}
        />
      )}
    </section>
  )
}

function CanvasFacts({ composition }: { composition: CompositionV1 }) {
  return (
    <div className="space-y-2 text-small">
      <p className="font-mono text-foreground">
        {composition.canvas.width} × {composition.canvas.height}
      </p>
      <p className="text-muted-foreground">Length {formatTimecode(composition.durationMs)}</p>
      <p className="text-muted-foreground">
        Select an item on the timeline, a caption word, or a text overlay.
      </p>
    </div>
  )
}

function ItemFields({
  composition,
  itemId,
  playheadMs,
  onTrim,
  onCrop,
  onSplit,
  onDelete,
}: {
  composition: CompositionV1
  itemId: string
  playheadMs: number
  onTrim: (sourceInMs: number, sourceOutMs: number) => void
  onCrop: (crop: TrackItem['crop']) => void
  onSplit: () => void
  onDelete: () => void
}) {
  const item = composition.tracks
    .flatMap((track) => track.items)
    .find((candidate) => candidate.id === itemId)
  if (item === undefined) return <CanvasFacts composition={composition} />
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <TimecodeInput
          label="Start"
          valueMs={item.sourceInMs}
          maxMs={item.sourceOutMs - MIN_ITEM_MS}
          onCommit={(ms) => onTrim(ms, item.sourceOutMs)}
        />
        <TimecodeInput
          label="End"
          valueMs={item.sourceOutMs}
          minMs={item.sourceInMs + MIN_ITEM_MS}
          onCommit={(ms) => onTrim(item.sourceInMs, ms)}
        />
      </div>
      <p className="font-mono text-caption text-muted-foreground">
        Duration {formatTimecode(item.sourceOutMs - item.sourceInMs)}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" onClick={onSplit}>
          Split at playhead
        </Button>
        <Button variant="secondary" size="sm" onClick={onDelete}>
          Delete item
        </Button>
      </div>
      <p className="font-mono text-caption text-subtle-foreground">
        Playhead {formatTimecode(playheadMs)}
      </p>
      <div className="flex items-center justify-between gap-2">
        <span className="text-caption text-muted-foreground">
          {item.crop === null
            ? 'Full frame'
            : `Cropped to ${Math.round(item.crop.width * 100)}% × ${Math.round(item.crop.height * 100)}%`}
        </span>
        <Button
          variant="ghost"
          size="sm"
          disabled={item.crop === null}
          onClick={() => onCrop(null)}
        >
          Clear crop
        </Button>
      </div>
    </div>
  )
}

function WordFields({
  composition,
  wordId,
  onRetimeWord,
  onWordText,
}: {
  composition: CompositionV1
  wordId: string
  onRetimeWord: (wordId: string, startMs: number, endMs: number) => void
  onWordText: (wordId: string, text: string) => void
}) {
  const words = composition.captions.words
  const index = words.findIndex((word) => word.id === wordId)
  const word = words[index]
  if (word === undefined) return <CanvasFacts composition={composition} />
  const previousEnd = words[index - 1]?.endMs ?? 0
  const nextStart = words[index + 1]?.startMs ?? composition.durationMs
  return (
    <div className="space-y-3">
      <WordText
        key={word.id + word.text}
        text={word.text}
        onCommit={(text) => onWordText(word.id, text)}
      />
      <div className="grid grid-cols-2 gap-2">
        <TimecodeInput
          label="Start"
          valueMs={word.startMs}
          minMs={previousEnd}
          maxMs={word.endMs - MIN_WORD_MS}
          onCommit={(ms) => onRetimeWord(word.id, ms, word.endMs)}
        />
        <TimecodeInput
          label="End"
          valueMs={word.endMs}
          minMs={word.startMs + MIN_WORD_MS}
          maxMs={nextStart}
          onCommit={(ms) => onRetimeWord(word.id, word.startMs, ms)}
        />
      </div>
    </div>
  )
}

function WordText({ text, onCommit }: { text: string; onCommit: (text: string) => void }) {
  const [draft, setDraft] = useState(text)
  const commit = () => {
    if (draft.trim() !== '' && draft.trim() !== text) onCommit(draft.trim())
  }
  return (
    <label className="block space-y-1">
      <span className="text-caption text-muted-foreground">Word</span>
      <input
        type="text"
        value={draft}
        onChange={(event) => setDraft(event.currentTarget.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') commit()
        }}
        className="h-8 w-full rounded-md border border-input bg-secondary px-2 text-small"
      />
    </label>
  )
}

function OverlayFields({
  composition,
  overlayId,
  onMoveOverlay,
}: {
  composition: CompositionV1
  overlayId: string
  onMoveOverlay: (overlayId: string, startMs: number, endMs: number) => void
}) {
  const overlay = composition.overlays.find((entry) => entry.id === overlayId)
  if (overlay === undefined) return <CanvasFacts composition={composition} />
  return (
    <div className="space-y-3">
      <p className="text-small">
        {overlay.type === 'text' || overlay.type === 'citation' ? overlay.text : 'B-roll'}
      </p>
      <div className="grid grid-cols-2 gap-2">
        <TimecodeInput
          label="Start"
          valueMs={overlay.timelineStartMs}
          maxMs={overlay.timelineEndMs - MIN_OVERLAY_MS}
          onCommit={(ms) => onMoveOverlay(overlay.id, ms, overlay.timelineEndMs)}
        />
        <TimecodeInput
          label="End"
          valueMs={overlay.timelineEndMs}
          minMs={overlay.timelineStartMs + MIN_OVERLAY_MS}
          maxMs={composition.durationMs}
          onCommit={(ms) => onMoveOverlay(overlay.id, overlay.timelineStartMs, ms)}
        />
      </div>
    </div>
  )
}
