/**
 * The editor's state, which is the composition document and nothing else.
 *
 * Every operation here produces a new document from the old one; none of them touch
 * source or proxy media, and none of them keep a second copy of what the composition
 * already says. Undo and redo are Immer patches, so a reversal restores exactly the
 * fields a change altered rather than a snapshot that might have drifted.
 *
 * The invariants the backend enforces are maintained here too — items are laid out end
 * to end from zero, `durationMs` follows them, and caption words are re-timed with the
 * timeline — because a document that would be refused on save is not worth editing.
 */
import { applyPatches, current, enablePatches, produceWithPatches, type Patch } from 'immer'

import type { CompositionV1 } from '@/lib/api/generated/model'

enablePatches()

/** The aspect presets Section 9 requires the editor to offer. */
export type Aspect = '9:16' | '16:9' | '1:1' | '4:5'

/** The canvas each aspect preset renders into. */
export const ASPECT_CANVAS: Record<Aspect, { width: number; height: number }> = {
  '9:16': { width: 1080, height: 1920 },
  '16:9': { width: 1920, height: 1080 },
  '1:1': { width: 1080, height: 1080 },
  '4:5': { width: 1080, height: 1350 },
}

/** The shortest item the editor will leave on the timeline. */
export const MIN_ITEM_MS = 500

type Track = CompositionV1['tracks'][number]
type TrackItem = Track['items'][number]
type Crop = TrackItem['crop']
type CaptionStyle = CompositionV1['captions']['style']
type CaptionWord = CompositionV1['captions']['words'][number]

/** One item as the timeline draws it: where it starts and where it stops. */
export interface PlacedItem {
  trackId: string
  item: TrackItem
  startMs: number
  endMs: number
}

/** What the editor holds while a member is working. */
export interface EditorState {
  composition: CompositionV1
  selectedItemId: string | null
  playheadMs: number
  savedCanonical: string
  past: Array<{ undo: Patch[]; redo: Patch[] }>
  future: Array<{ undo: Patch[]; redo: Patch[] }>
}

/** Everything a member can do to a composition in the basic editor. */
export type EditorAction =
  | { type: 'select'; itemId: string | null }
  | { type: 'seek'; ms: number }
  | { type: 'trim'; itemId: string; sourceInMs: number; sourceOutMs: number }
  | { type: 'crop'; itemId: string; crop: Crop }
  | { type: 'aspect'; aspect: Aspect; sourceAspect: number }
  | { type: 'captionText'; wordId: string; text: string }
  | { type: 'captionStyle'; patch: Partial<CaptionStyle> }
  | { type: 'split'; itemId: string; atMs: number }
  | { type: 'deleteItem'; itemId: string }
  | { type: 'undo' }
  | { type: 'redo' }
  | { type: 'markSaved'; composition: CompositionV1 }
  | { type: 'replace'; composition: CompositionV1 }

/** Start editing one Revision, with no history behind it and nothing to save. */
export function initialEditorState(composition: CompositionV1): EditorState {
  return {
    composition,
    selectedItemId: firstItemId(composition),
    playheadMs: 0,
    savedCanonical: canonicalJson(composition),
    past: [],
    future: [],
  }
}

/** Whether the document differs from the Revision the backend last accepted. */
export function isDirty(state: EditorState): boolean {
  return canonicalJson(state.composition) !== state.savedCanonical
}

/** Whether there is a change to reverse. */
export function canUndo(state: EditorState): boolean {
  return state.past.length > 0
}

/** Whether there is a reversed change to reapply. */
export function canRedo(state: EditorState): boolean {
  return state.future.length > 0
}

/** Lay every item out in the order the timeline plays them. */
export function timelineItems(composition: CompositionV1): PlacedItem[] {
  const placed: PlacedItem[] = []
  for (const track of composition.tracks) {
    for (const item of track.items) {
      placed.push({
        trackId: track.id,
        item,
        startMs: item.timelineStartMs,
        endMs: item.timelineStartMs + (item.sourceOutMs - item.sourceInMs),
      })
    }
  }
  return placed.sort((left, right) => left.startMs - right.startMs)
}

/**
 * The centred crop that fills one aspect from a source of another.
 *
 * It is normalized, so it stays correct whatever resolution the proxy happens to be, and
 * it is centred because a face-aware crop is Task 26's decision rather than this one's.
 */
export function centreCrop(sourceAspect: number, targetAspect: number): Crop {
  if (Math.abs(sourceAspect - targetAspect) < 0.001) {
    return null
  }
  if (targetAspect < sourceAspect) {
    const width = targetAspect / sourceAspect
    return { x: (1 - width) / 2, y: 0, width, height: 1 }
  }
  const height = sourceAspect / targetAspect
  return { x: 0, y: (1 - height) / 2, width: 1, height }
}

/** Serialize a document the way the backend hashes it, so equality means equality. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalValue(value))
}

/** Apply one member action to the editor's state. */
export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case 'select':
      return { ...state, selectedItemId: action.itemId }
    case 'seek':
      return { ...state, playheadMs: Math.max(0, Math.min(action.ms, state.composition.durationMs)) }
    case 'undo':
      return step(state, 'past', 'future')
    case 'redo':
      return step(state, 'future', 'past')
    case 'markSaved':
      return { ...state, savedCanonical: canonicalJson(action.composition) }
    case 'replace':
      return initialEditorState(action.composition)
    default:
      return record(state, (draft) => edit(draft, action))
  }
}

/** Apply one document change, keeping the patches that reverse and repeat it. */
function record(
  state: EditorState,
  change: (draft: CompositionV1) => void,
): EditorState {
  const [composition, redo, undo] = produceWithPatches(state.composition, change)
  if (redo.length === 0) {
    return state
  }
  return {
    ...state,
    composition,
    past: [...state.past, { undo, redo }],
    future: [],
  }
}

/** Move one change between the two halves of the history. */
function step(
  state: EditorState,
  from: 'past' | 'future',
  to: 'past' | 'future',
): EditorState {
  const source = state[from]
  const entry = source.at(-1)
  if (entry === undefined) {
    return state
  }
  const patches = from === 'past' ? entry.undo : entry.redo
  return {
    ...state,
    composition: applyPatches(state.composition, patches),
    [from]: source.slice(0, -1),
    [to]: [...state[to], entry],
  }
}

/** Perform one document change on a draft the caller is recording patches for. */
function edit(draft: CompositionV1, action: EditorAction): void {
  switch (action.type) {
    case 'trim':
      return trim(draft, action)
    case 'crop':
      return crop(draft, action)
    case 'aspect':
      return reframe(draft, action)
    case 'captionText':
      return retext(draft, action)
    case 'captionStyle':
      Object.assign(draft.captions.style, action.patch)
      return
    case 'split':
      return split(draft, action)
    case 'deleteItem':
      return remove(draft, action.itemId)
    default:
      return
  }
}

/**
 * Trim one item inside the source the clip was cut from.
 *
 * A trim is a removal from the timeline, so the captions covering what was removed go
 * with it and everything after it moves earlier by the same amount. Bounds are clamped
 * rather than refused, because a member dragging a handle past the end of the source is
 * asking for the end of the source.
 */
function trim(
  draft: CompositionV1,
  action: { itemId: string; sourceInMs: number; sourceOutMs: number },
): void {
  const found = locate(draft, action.itemId)
  if (found === null) {
    return
  }
  const { item } = found
  const lower = draft.sourceRange.inMs
  const upper = draft.sourceRange.outMs
  const sourceInMs = Math.round(
    Math.min(Math.max(action.sourceInMs, lower), upper - MIN_ITEM_MS),
  )
  const sourceOutMs = Math.round(
    Math.max(Math.min(action.sourceOutMs, upper), sourceInMs + MIN_ITEM_MS),
  )
  const start = item.timelineStartMs
  const headMs = sourceInMs - item.sourceInMs
  const tailMs = item.sourceOutMs - sourceOutMs
  item.sourceInMs = sourceInMs
  item.sourceOutMs = sourceOutMs
  if (tailMs > 0) {
    cutCaptions(draft, start + (sourceOutMs - item.sourceInMs), tailMs)
  }
  if (headMs > 0) {
    cutCaptions(draft, start, headMs)
  } else if (headMs < 0) {
    shiftCaptions(draft, start, -headMs)
  }
  relayout(draft)
}

/** Frame one item without changing the media inside it. */
function crop(draft: CompositionV1, action: { itemId: string; crop: Crop }): void {
  const found = locate(draft, action.itemId)
  if (found !== null) {
    found.item.crop = action.crop
  }
}

/** Switch the canvas to one preset and centre every visual item inside it. */
function reframe(draft: CompositionV1, action: { aspect: Aspect; sourceAspect: number }): void {
  const canvas = ASPECT_CANVAS[action.aspect]
  draft.canvas.width = canvas.width
  draft.canvas.height = canvas.height
  const target = canvas.width / canvas.height
  for (const track of draft.tracks) {
    if (track.type !== 'video') {
      continue
    }
    for (const item of track.items) {
      item.crop = centreCrop(action.sourceAspect, target)
    }
  }
}

/** Change one caption word's text, never its timing. */
function retext(draft: CompositionV1, action: { wordId: string; text: string }): void {
  const text = action.text.trim()
  if (text === '') {
    return
  }
  const word = draft.captions.words.find((candidate) => candidate.id === action.wordId)
  if (word !== undefined) {
    word.text = text
  }
}

/** Split one item in two at a point on the timeline, keeping every millisecond. */
function split(draft: CompositionV1, action: { itemId: string; atMs: number }): void {
  const found = locate(draft, action.itemId)
  if (found === null) {
    return
  }
  const { track, item, index } = found
  const offset = action.atMs - item.timelineStartMs
  const length = item.sourceOutMs - item.sourceInMs
  if (offset < MIN_ITEM_MS || length - offset < MIN_ITEM_MS) {
    return
  }
  const boundary = item.sourceInMs + offset
  const tail: TrackItem = {
    // `current` reads the draft as plain data; a draft cannot be structurally cloned.
    ...structuredClone(current(item)),
    id: nextItemId(draft, item.id),
    timelineStartMs: action.atMs,
    sourceInMs: boundary,
    sourceOutMs: item.sourceOutMs,
  }
  item.sourceOutMs = boundary
  track.items.splice(index + 1, 0, tail)
  relayout(draft)
}

/** Remove one item, its captions, and the gap it leaves behind. */
function remove(draft: CompositionV1, itemId: string): void {
  const found = locate(draft, itemId)
  if (found === null) {
    return
  }
  const { track, item, index } = found
  if (totalItems(draft) < 2) {
    return
  }
  cutCaptions(draft, item.timelineStartMs, item.sourceOutMs - item.sourceInMs)
  track.items.splice(index, 1)
  relayout(draft)
}

/** Drop the caption words inside one removed span and pull the rest earlier. */
function cutCaptions(draft: CompositionV1, fromMs: number, lengthMs: number): void {
  const toMs = fromMs + lengthMs
  draft.captions.words = draft.captions.words
    .filter((word) => word.endMs <= fromMs || word.startMs >= toMs)
    .map((word) =>
      word.startMs >= toMs
        ? { ...word, startMs: word.startMs - lengthMs, endMs: word.endMs - lengthMs }
        : word,
    )
}

/** Push the caption words at or after one point later by an inserted span. */
function shiftCaptions(draft: CompositionV1, fromMs: number, lengthMs: number): void {
  draft.captions.words = draft.captions.words.map((word: CaptionWord) =>
    word.startMs >= fromMs
      ? { ...word, startMs: word.startMs + lengthMs, endMs: word.endMs + lengthMs }
      : word,
  )
}

/** Lay the items out end to end and let the duration follow them. */
function relayout(draft: CompositionV1): void {
  for (const track of draft.tracks) {
    let cursor = 0
    for (const item of track.items) {
      item.timelineStartMs = cursor
      cursor += item.sourceOutMs - item.sourceInMs
    }
  }
  const duration = Math.max(
    ...draft.tracks.map((track) =>
      track.items.reduce((total, item) => total + (item.sourceOutMs - item.sourceInMs), 0),
    ),
    MIN_ITEM_MS,
  )
  draft.durationMs = duration
  draft.captions.words = draft.captions.words.filter((word) => word.endMs <= duration)
  draft.bookmarks = draft.bookmarks.filter((bookmark) => bookmark.timelineMs <= duration)
}

/** Find one item, the track holding it, and where in that track it sits. */
function locate(
  composition: CompositionV1,
  itemId: string,
): { track: Track; item: TrackItem; index: number } | null {
  for (const track of composition.tracks) {
    const index = track.items.findIndex((item) => item.id === itemId)
    const item = track.items[index]
    if (item !== undefined) {
      return { track, item, index }
    }
  }
  return null
}

/** Name a new item after the one it was split from, without colliding with any other. */
function nextItemId(composition: CompositionV1, itemId: string): string {
  const taken = new Set(timelineItems(composition).map((placed) => placed.item.id))
  let suffix = 2
  while (taken.has(`${itemId}-${suffix}`)) {
    suffix += 1
  }
  return `${itemId}-${suffix}`
}

/** Count every item on every track. */
function totalItems(composition: CompositionV1): number {
  return composition.tracks.reduce((total, track) => total + track.items.length, 0)
}

/** The item the editor selects when it opens. */
function firstItemId(composition: CompositionV1): string | null {
  return timelineItems(composition)[0]?.item.id ?? null
}

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(canonicalValue)
  }
  if (typeof value === 'object' && value !== null) {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, entry]) => entry !== undefined)
        .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
        .map(([key, entry]) => [key, canonicalValue(entry)]),
    )
  }
  return value
}
