/**
 * The editor's state, which is the composition document and nothing else.
 *
 * Every operation here produces a new document from the old one; none of them touch
 * source or proxy media, and none of them keep a second copy of what the composition
 * already says. Undo and redo are Immer patches, so a reversal restores exactly the
 * fields a change altered rather than a snapshot that might have drifted.
 *
 * The invariants the backend enforces are maintained here too — the base video lane is
 * laid out end to end from zero, `durationMs` follows the longest lane, no two items
 * share an instant on one lane, and caption words are re-timed with the timeline —
 * because a document that would be refused on save is not worth editing.
 *
 * A few things a member works with are deliberately *not* in the document: which track
 * is selected, which tracks are locked, and where the source monitor's marks sit. None
 * of them change the clip, so none of them belong in a Revision.
 */
import { applyPatches, current, enablePatches, produceWithPatches, type Patch } from 'immer'

import { motionFits, type TemplateDefinition } from './templates'
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

/** How close to an edge a dragged value has to come, on screen, before it snaps. */
export const SNAP_THRESHOLD_PX = 8

/** How long a new text overlay is on screen before a member changes it. */
export const TEXT_OVERLAY_MS = 3_000

/** The shortest a caption word may be retimed to, so karaoke still has a moment to paint. */
export const MIN_WORD_MS = 100

type Track = CompositionV1['tracks'][number]
type TrackItem = Track['items'][number]
type TrackKind = Track['type']
type Crop = TrackItem['crop']
type CaptionStyle = CompositionV1['captions']['style']
type CaptionWord = CompositionV1['captions']['words'][number]
type Overlay = CompositionV1['overlays'][number]
type Keyframe = TrackItem['keyframes'][number]
type Transform = TrackItem['transform']
type MotionPreset = TrackItem['motion']
type TextOverlay = Extract<Overlay, { type: 'text' }>
type TextStyle = TextOverlay['style']
type AudioMix = CompositionV1['audio']

/** The sound lanes a member can add beside the base video. */
export type SoundTrackKind = Exclude<TrackKind, 'video'>

/** One item as the timeline draws it: where it starts and where it stops. */
export interface PlacedItem {
  trackId: string
  item: TrackItem
  startMs: number
  endMs: number
}

/** One stretch of the clip in which one speaker is talking. */
export interface Scene {
  id: string
  startMs: number
  endMs: number
  speaker: string | null
  label: string | null
  wordCount: number
}

/** What the editor holds while a member is working. */
export interface EditorState {
  composition: CompositionV1
  selectedItemId: string | null
  selectedTrackId: string | null
  lockedTrackIds: string[]
  playheadMs: number
  markInMs: number | null
  markOutMs: number | null
  savedCanonical: string
  past: Array<{ undo: Patch[]; redo: Patch[] }>
  future: Array<{ undo: Patch[]; redo: Patch[] }>
}

/** Everything a member can do to a composition, and to their own view of it. */
export type EditorAction =
  | { type: 'select'; itemId: string | null }
  | { type: 'selectTrack'; trackId: string | null }
  | { type: 'toggleTrackLock'; trackId: string }
  | { type: 'seek'; ms: number }
  | { type: 'markIn'; ms: number }
  | { type: 'markOut'; ms: number }
  | { type: 'clearMarks' }
  | { type: 'trim'; itemId: string; sourceInMs: number; sourceOutMs: number }
  | { type: 'crop'; itemId: string; crop: Crop }
  | { type: 'aspect'; aspect: Aspect; sourceAspect: number }
  | { type: 'captionText'; wordId: string; text: string }
  | { type: 'captionStyle'; patch: Partial<CaptionStyle> }
  | { type: 'captionMode'; mode: CompositionV1['captions']['mode'] }
  | { type: 'split'; itemId: string; atMs: number }
  | { type: 'splitSide'; itemId: string; atMs: number; keep: 'left' | 'right' }
  | { type: 'duplicateItem'; itemId: string }
  | { type: 'resizeItem'; itemId: string; edge: 'start' | 'end'; toMs: number }
  | { type: 'moveItem'; itemId: string; toMs: number }
  | { type: 'deleteItem'; itemId: string; ripple?: boolean }
  | { type: 'addTrack'; trackType: SoundTrackKind }
  | { type: 'addFromSource' }
  | {
      type: 'addSound'
      kind: SoundTrackKind
      assetId: string
      atMs: number
      sourceInMs: number
      sourceOutMs: number
    }
  | { type: 'audio'; patch: Partial<AudioMix> }
  | { type: 'addText'; text: string }
  | { type: 'updateOverlay'; overlayId: string; patch: Partial<Pick<TextOverlay, 'text'>> & { style?: Partial<TextStyle>; placement?: TextOverlay['placement'] } }
  | { type: 'moveOverlay'; overlayId: string; startMs: number; endMs: number }
  | { type: 'deleteOverlay'; overlayId: string }
  | { type: 'addBookmark'; label: string; atMs?: number }
  | { type: 'renameBookmark'; bookmarkId: string; label: string }
  | { type: 'removeBookmark'; bookmarkId: string }
  | { type: 'retimeWord'; wordId: string; startMs: number; endMs: number }
  | { type: 'applyTemplate'; template: TemplateDefinition }
  | { type: 'setMotion'; targetId: string; preset: MotionPreset }
  | { type: 'addKeyframe'; targetId: string; atMs: number; transform?: Transform; opacity?: number }
  | { type: 'moveKeyframe'; targetId: string; atMs: number; toMs: number }
  | { type: 'removeKeyframe'; targetId: string; atMs: number }
  | { type: 'undo' }
  | { type: 'redo' }
  | { type: 'markSaved'; composition: CompositionV1 }
  | { type: 'replace'; composition: CompositionV1 }

/** Start editing one Revision, with no history behind it and nothing to save. */
export function initialEditorState(composition: CompositionV1): EditorState {
  return {
    composition,
    selectedItemId: firstItemId(composition),
    selectedTrackId: composition.tracks[0]?.id ?? null,
    lockedTrackIds: [],
    playheadMs: 0,
    markInMs: null,
    markOutMs: null,
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

/** Whether a member has protected one lane from being edited by accident. */
export function isTrackLocked(state: EditorState, trackId: string): boolean {
  return state.lockedTrackIds.includes(trackId)
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

/** The items of one lane, in the order that lane plays them. */
export function trackItems(composition: CompositionV1, trackId: string): TrackItem[] {
  const track = composition.tracks.find((candidate) => candidate.id === trackId)
  return [...(track?.items ?? [])].sort((left, right) => left.timelineStartMs - right.timelineStartMs)
}

/**
 * The instants a dragged edge is allowed to land on exactly.
 *
 * They are the edges a member is trying to meet — the start and end of the clip, every
 * item boundary, and every marker — so snapping never invents a moment of its own.
 */
export function snapTargets(composition: CompositionV1): number[] {
  const targets = new Set<number>([0, composition.durationMs])
  for (const placed of timelineItems(composition)) {
    targets.add(placed.startMs)
    targets.add(placed.endMs)
  }
  for (const bookmark of composition.bookmarks) {
    targets.add(bookmark.timelineMs)
  }
  return [...targets].sort((left, right) => left - right)
}

/** Land one value on the nearest target it is already close to, or leave it alone. */
export function snap(ms: number, targets: number[], thresholdMs: number): number {
  let best: number | null = null
  for (const target of targets) {
    const distance = Math.abs(target - ms)
    if (distance <= thresholdMs && (best === null || distance < Math.abs(best - ms))) {
      best = target
    }
  }
  return best ?? ms
}

/** How much time a fixed distance on screen covers at one zoom level. */
export function snapThresholdMs(pixelsPerSecond: number): number {
  return Math.round((SNAP_THRESHOLD_PX / pixelsPerSecond) * 1_000)
}

/**
 * The clip as a list of scenes, one per stretch of a single speaker.
 *
 * Scenes are derived rather than stored: the transcript already knows who was talking
 * and when, so a second copy in the document could only ever disagree with it. A member
 * names a scene by leaving a marker at its start, and that label is read back here.
 */
export function scenes(composition: CompositionV1): Scene[] {
  const list: Scene[] = []
  for (const word of composition.captions.words) {
    const open = list.at(-1)
    if (open !== undefined && open.speaker === word.speaker) {
      open.endMs = Math.max(open.endMs, word.endMs)
      open.wordCount += 1
      continue
    }
    list.push({
      id: `scene-at-${word.startMs}`,
      startMs: word.startMs,
      endMs: word.endMs,
      speaker: word.speaker,
      label: null,
      wordCount: 1,
    })
  }
  return list.map((scene) => ({
    ...scene,
    label:
      composition.bookmarks.find((bookmark) => bookmark.timelineMs === scene.startMs)?.label ?? null,
  }))
}


/** The caption word being said at one instant, if a word is being said at all. */
export function activeWordAt(words: CaptionWord[], atMs: number): CaptionWord | null {
  return words.find((word) => atMs >= word.startMs && atMs < word.endMs) ?? null
}

/**
 * The animated values one element holds at one instant.
 *
 * Between two keyframes a value travels by the easing the earlier one names; outside them
 * it is held, so an element never jumps to a value nobody asked for.
 */
export function interpolatedAt(
  keyframes: Keyframe[],
  atMs: number,
): { transform: Transform | null; opacity: number | null } {
  const ordered = [...keyframes].sort((left, right) => left.atMs - right.atMs)
  return {
    transform: interpolatedTransform(ordered, atMs),
    opacity: interpolatedOpacity(ordered, atMs),
  }
}

/** Where an element's framing sits at one instant. */
function interpolatedTransform(ordered: Keyframe[], atMs: number): Transform | null {
  const framing = ordered.filter((frame) => frame.transform !== null)
  const first = framing[0]
  const last = framing.at(-1)
  if (first === undefined || last === undefined) {
    return null
  }
  if (atMs <= first.atMs) {
    return first.transform
  }
  if (atMs >= last.atMs) {
    return last.transform
  }
  for (const [earlier, later] of pairs(framing)) {
    if (atMs >= earlier.atMs && atMs <= later.atMs) {
      const ratio = eased(earlier.easing, (atMs - earlier.atMs) / (later.atMs - earlier.atMs))
      const from = earlier.transform as NonNullable<Transform>
      const to = later.transform as NonNullable<Transform>
      return {
        x: from.x + (to.x - from.x) * ratio,
        y: from.y + (to.y - from.y) * ratio,
        scale: from.scale + (to.scale - from.scale) * ratio,
        rotation: from.rotation + (to.rotation - from.rotation) * ratio,
      }
    }
  }
  return last.transform
}

/** How opaque an element is at one instant. */
function interpolatedOpacity(ordered: Keyframe[], atMs: number): number | null {
  const fading = ordered.filter((frame) => frame.opacity !== null)
  const first = fading[0]
  const last = fading.at(-1)
  if (first === undefined || last === undefined) {
    return null
  }
  if (atMs <= first.atMs) {
    return first.opacity
  }
  if (atMs >= last.atMs) {
    return last.opacity
  }
  for (const [earlier, later] of pairs(fading)) {
    if (atMs >= earlier.atMs && atMs <= later.atMs) {
      const ratio = eased(earlier.easing, (atMs - earlier.atMs) / (later.atMs - earlier.atMs))
      const from = earlier.opacity ?? 0
      const to = later.opacity ?? 0
      return from + (to - from) * ratio
    }
  }
  return last.opacity
}

/** Each consecutive pair of a list, so an interval can be searched for. */
function pairs(frames: Keyframe[]): Array<[Keyframe, Keyframe]> {
  return frames.slice(0, -1).map((frame, index) => [frame, frames[index + 1] as Keyframe])
}

/** How far along a movement is, once the easing it names has been applied. */
function eased(easing: Keyframe['easing'], ratio: number): number {
  const progress = Math.min(Math.max(ratio, 0), 1)
  if (easing === 'easeIn') {
    return progress * progress
  }
  if (easing === 'easeOut') {
    return 1 - (1 - progress) * (1 - progress)
  }
  if (easing === 'easeInOut') {
    return progress < 0.5
      ? 2 * progress * progress
      : 1 - Math.pow(-2 * progress + 2, 2) / 2
  }
  return progress
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
    case 'selectTrack':
      return { ...state, selectedTrackId: action.trackId }
    case 'toggleTrackLock':
      return {
        ...state,
        lockedTrackIds: state.lockedTrackIds.includes(action.trackId)
          ? state.lockedTrackIds.filter((trackId) => trackId !== action.trackId)
          : [...state.lockedTrackIds, action.trackId],
      }
    case 'seek':
      return { ...state, playheadMs: clamp(action.ms, 0, state.composition.durationMs) }
    case 'markIn':
      return { ...state, markInMs: clamp(action.ms, 0, sourceLengthMs(state.composition)) }
    case 'markOut':
      return { ...state, markOutMs: clamp(action.ms, 0, sourceLengthMs(state.composition)) }
    case 'clearMarks':
      return { ...state, markInMs: null, markOutMs: null }
    case 'undo':
      return step(state, 'past', 'future')
    case 'redo':
      return step(state, 'future', 'past')
    case 'markSaved':
      return { ...state, savedCanonical: canonicalJson(action.composition) }
    case 'replace':
      return initialEditorState(action.composition)
    default:
      if (refusedByLock(state, action)) {
        return state
      }
      return record(state, (draft) => edit(draft, action, state))
  }
}

/** Whether this action aims at a lane a member has protected from editing. */
function refusedByLock(state: EditorState, action: EditorAction): boolean {
  const trackId = targetTrackId(state.composition, action)
  return trackId !== null && isTrackLocked(state, trackId)
}

/** The lane one action would change, when it changes exactly one. */
function targetTrackId(composition: CompositionV1, action: EditorAction): string | null {
  if ('itemId' in action && typeof action.itemId === 'string') {
    return locate(composition, action.itemId)?.track.id ?? null
  }
  return null
}

/** Apply one document change, keeping the patches that reverse and repeat it. */
function record(state: EditorState, change: (draft: CompositionV1) => void): EditorState {
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
function step(state: EditorState, from: 'past' | 'future', to: 'past' | 'future'): EditorState {
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
function edit(draft: CompositionV1, action: EditorAction, state: EditorState): void {
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
    case 'captionMode':
      draft.captions.mode = action.mode
      return
    case 'split':
      return split(draft, action)
    case 'splitSide':
      return splitSide(draft, action)
    case 'duplicateItem':
      return duplicateItem(draft, action.itemId)
    case 'resizeItem':
      return resizeItem(draft, action)
    case 'moveItem':
      return moveItem(draft, action)
    case 'deleteItem':
      return remove(draft, action.itemId, action.ripple === true)
    case 'addTrack':
      return addTrack(draft, action.trackType)
    case 'addFromSource':
      return addFromSource(draft, state)
    case 'addSound':
      return addSound(draft, action)
    case 'audio':
      Object.assign(draft.audio, action.patch)
      return
    case 'addText':
      return addText(draft, action.text, state.playheadMs)
    case 'updateOverlay':
      return updateOverlay(draft, action)
    case 'moveOverlay':
      return moveOverlay(draft, action)
    case 'deleteOverlay':
      draft.overlays = draft.overlays.filter((overlay) => overlay.id !== action.overlayId)
      return
    case 'addBookmark':
      return addBookmark(draft, action.label, action.atMs ?? state.playheadMs)
    case 'renameBookmark':
      return renameBookmark(draft, action)
    case 'removeBookmark':
      draft.bookmarks = draft.bookmarks.filter((bookmark) => bookmark.id !== action.bookmarkId)
      return
    case 'retimeWord':
      return retimeWord(draft, action)
    case 'applyTemplate':
      return applyTemplate(draft, action.template)
    case 'setMotion':
      return setMotion(draft, action)
    case 'addKeyframe':
      return addKeyframe(draft, action)
    case 'moveKeyframe':
      return moveKeyframe(draft, action)
    case 'removeKeyframe':
      return removeKeyframe(draft, action)
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
  const sourceInMs = Math.round(Math.min(Math.max(action.sourceInMs, lower), upper - MIN_ITEM_MS))
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

/**
 * Split at the playhead and keep only one side of the cut.
 *
 * This is the same decision as a trim — the other side is removed from the timeline and
 * its captions go with it — which is why it is expressed as one.
 */
function splitSide(
  draft: CompositionV1,
  action: { itemId: string; atMs: number; keep: 'left' | 'right' },
): void {
  const found = locate(draft, action.itemId)
  if (found === null) {
    return
  }
  const { item } = found
  const offset = action.atMs - item.timelineStartMs
  const length = item.sourceOutMs - item.sourceInMs
  if (offset < MIN_ITEM_MS || length - offset < MIN_ITEM_MS) {
    return
  }
  const boundary = item.sourceInMs + offset
  if (action.keep === 'left') {
    trim(draft, { itemId: item.id, sourceInMs: item.sourceInMs, sourceOutMs: boundary })
    return
  }
  trim(draft, { itemId: item.id, sourceInMs: boundary, sourceOutMs: item.sourceOutMs })
}

/**
 * Play one item's media a second time, immediately after itself.
 *
 * The copy carries no captions of its own: the words belong to the moment the transcript
 * recorded, and a second set with the same identifiers would be refused on save. What
 * follows the copy moves later by exactly the length that was inserted.
 */
function duplicateItem(draft: CompositionV1, itemId: string): void {
  const found = locate(draft, itemId)
  if (found === null) {
    return
  }
  const { track, item, index } = found
  const length = item.sourceOutMs - item.sourceInMs
  const copy: TrackItem = {
    ...structuredClone(current(item)),
    id: nextItemId(draft, item.id),
    timelineStartMs: item.timelineStartMs + length,
  }
  if (track.type === 'video') {
    shiftCaptions(draft, item.timelineStartMs + length, length)
    track.items.splice(index + 1, 0, copy)
    relayout(draft)
    return
  }
  copy.timelineStartMs = freeStart(track, copy.timelineStartMs, length, copy.id)
  track.items.splice(index + 1, 0, copy)
  sortTrack(track)
  relayout(draft)
}

/**
 * Drag one edge of an item to a moment on the timeline.
 *
 * On the base video lane a resize is a trim, so the rest of the clip follows it. On a
 * sound lane the item keeps its place and only its own bounds change, because a bed is
 * timed against the picture rather than against its neighbours.
 */
function resizeItem(
  draft: CompositionV1,
  action: { itemId: string; edge: 'start' | 'end'; toMs: number },
): void {
  const found = locate(draft, action.itemId)
  if (found === null) {
    return
  }
  const { track, item } = found
  const offset = action.toMs - item.timelineStartMs
  if (track.type === 'video') {
    if (action.edge === 'end') {
      trim(draft, {
        itemId: item.id,
        sourceInMs: item.sourceInMs,
        sourceOutMs: item.sourceInMs + offset,
      })
      return
    }
    trim(draft, {
      itemId: item.id,
      sourceInMs: item.sourceInMs + offset,
      sourceOutMs: item.sourceOutMs,
    })
    return
  }
  const neighbours = boundsOf(track, item)
  if (action.edge === 'end') {
    const end = clamp(action.toMs, item.timelineStartMs + MIN_ITEM_MS, neighbours.latestEnd)
    item.sourceOutMs = item.sourceInMs + (end - item.timelineStartMs)
    relayout(draft)
    return
  }
  const start = clamp(
    action.toMs,
    neighbours.earliestStart,
    item.timelineStartMs + (item.sourceOutMs - item.sourceInMs) - MIN_ITEM_MS,
  )
  item.sourceInMs += start - item.timelineStartMs
  item.timelineStartMs = start
  relayout(draft)
}

/**
 * Drag one item to another moment on the timeline.
 *
 * The base video lane plays its items one after another with nothing between them, so
 * dragging there reorders rather than repositions, and the captions of every item that
 * moved travel with it. On a sound lane the item lands where it was dropped, clamped so
 * that it never overlaps a neighbour — an overlap would be refused on save.
 */
function moveItem(draft: CompositionV1, action: { itemId: string; toMs: number }): void {
  const found = locate(draft, action.itemId)
  if (found === null) {
    return
  }
  const { track, item, index } = found
  if (track.type !== 'video') {
    item.timelineStartMs = freeStart(
      track,
      Math.max(0, Math.round(action.toMs)),
      item.sourceOutMs - item.sourceInMs,
      item.id,
    )
    sortTrack(track)
    relayout(draft)
    return
  }
  const before = track.items.map((entry) => ({
    id: entry.id,
    startMs: entry.timelineStartMs,
    lengthMs: entry.sourceOutMs - entry.sourceInMs,
  }))
  const remaining = before.filter((entry) => entry.id !== item.id)
  const target = insertionIndex(remaining, action.toMs)
  if (target === index) {
    return
  }
  const moved = track.items.splice(index, 1)[0]
  if (moved === undefined) {
    return
  }
  track.items.splice(target, 0, moved)
  let cursor = 0
  const moves: Array<{ oldStart: number; newStart: number; lengthMs: number }> = []
  for (const entry of track.items) {
    const length = entry.sourceOutMs - entry.sourceInMs
    const previous = before.find((candidate) => candidate.id === entry.id)
    if (previous !== undefined) {
      moves.push({ oldStart: previous.startMs, newStart: cursor, lengthMs: length })
    }
    cursor += length
  }
  remapCaptions(draft, moves)
  relayout(draft)
}

/** Where an item dropped at one instant belongs among the items that stayed put. */
function insertionIndex(
  entries: Array<{ startMs: number; lengthMs: number }>,
  toMs: number,
): number {
  let cursor = 0
  let index = 0
  for (const entry of entries) {
    if (toMs < cursor + entry.lengthMs / 2) {
      return index
    }
    cursor += entry.lengthMs
    index += 1
  }
  return index
}

/** Remove one item, its captions, and — on the base lane — the gap it leaves behind. */
function remove(draft: CompositionV1, itemId: string, ripple: boolean): void {
  const found = locate(draft, itemId)
  if (found === null) {
    return
  }
  const { track, item, index } = found
  const length = item.sourceOutMs - item.sourceInMs
  if (track.type === 'video') {
    if (videoItems(draft).length < 2) {
      return
    }
    cutCaptions(draft, item.timelineStartMs, length)
    track.items.splice(index, 1)
    relayout(draft)
    return
  }
  const removedAt = item.timelineStartMs
  track.items.splice(index, 1)
  if (ripple) {
    for (const later of track.items) {
      if (later.timelineStartMs >= removedAt) {
        later.timelineStartMs = Math.max(0, later.timelineStartMs - length)
      }
    }
  }
  relayout(draft)
}

/** Add one empty sound lane, named after the kind of sound it carries. */
function addTrack(draft: CompositionV1, trackType: SoundTrackKind): void {
  draft.tracks.push({ id: nextTrackId(draft, trackType), type: trackType, items: [] })
}

/**
 * Add the marked span of the source to the end of the base timeline.
 *
 * The marks are read against the clip's own source range, which is what the source
 * monitor plays, and the new item carries no captions: the words for that span were not
 * part of the moment the analysis chose.
 */
function addFromSource(draft: CompositionV1, state: EditorState): void {
  const track = draft.tracks.find((candidate) => candidate.type === 'video')
  const template = track?.items[0]
  if (track === undefined || template === undefined) {
    return
  }
  const { markInMs, markOutMs } = state
  if (markInMs === null || markOutMs === null || markOutMs - markInMs < MIN_ITEM_MS) {
    return
  }
  const sourceInMs = draft.sourceRange.inMs + Math.round(markInMs)
  const sourceOutMs = draft.sourceRange.inMs + Math.round(markOutMs)
  track.items.push({
    ...structuredClone(current(template)),
    id: nextItemId(draft, 'source'),
    timelineStartMs: draft.durationMs,
    sourceInMs: Math.min(sourceInMs, draft.sourceRange.outMs - MIN_ITEM_MS),
    sourceOutMs: Math.min(sourceOutMs, draft.sourceRange.outMs),
  })
  relayout(draft)
}

/**
 * Put one asset on a lane of the kind it belongs to, making that lane if it is missing.
 *
 * Extracted audio is speech rather than a bed, so it lives on its own kind of lane and
 * is levelled with the dialogue by the renderer. One lane of each kind is enough for any
 * number of placements, so a second placement reuses the lane the first one made.
 */
function addSound(
  draft: CompositionV1,
  action: {
    kind: SoundTrackKind
    assetId: string
    atMs: number
    sourceInMs: number
    sourceOutMs: number
  },
): void {
  if (!draft.tracks.some((track) => track.type === action.kind)) {
    addTrack(draft, action.kind)
  }
  const track = draft.tracks.find((candidate) => candidate.type === action.kind)
  if (track === undefined) {
    return
  }
  placeSound(draft, track, action)
}

/** Put one asset on one sound lane, in the first place it fits at or after the drop. */
function placeSound(
  draft: CompositionV1,
  track: Track,
  action: { assetId: string; atMs: number; sourceInMs: number; sourceOutMs: number },
): void {
  const lengthMs = Math.round(action.sourceOutMs - action.sourceInMs)
  if (lengthMs < MIN_ITEM_MS) {
    return
  }
  const id = nextItemId(draft, `${track.id}-item`)
  track.items.push({
    id,
    sourceAssetId: action.assetId,
    timelineStartMs: freeStart(track, Math.max(0, Math.round(action.atMs)), lengthMs, id),
    sourceInMs: Math.round(action.sourceInMs),
    sourceOutMs: Math.round(action.sourceOutMs),
    transform: { x: 0.5, y: 0.5, scale: 1, rotation: 0 },
    crop: null,
    opacity: 1,
    blendMode: 'normal',
    motion: 'none',
    origin: { type: 'userAsset', suggestionId: null, provenanceId: null },
    keyframes: [],
  })
  sortTrack(track)
  relayout(draft)
}

/** Write one text overlay at the playhead, inside the clip it belongs to. */
function addText(draft: CompositionV1, text: string, playheadMs: number): void {
  const written = text.trim()
  if (written === '') {
    return
  }
  const endMs = Math.min(playheadMs + TEXT_OVERLAY_MS, draft.durationMs)
  const startMs = Math.max(0, Math.min(playheadMs, endMs - MIN_ITEM_MS))
  if (endMs - startMs < MIN_ITEM_MS) {
    return
  }
  draft.overlays.push({
    id: nextOverlayId(draft),
    type: 'text',
    timelineStartMs: Math.round(startMs),
    timelineEndMs: Math.round(endMs),
    placement: 'center',
    opacity: 1,
    keyframes: [],
    motion: 'none',
    text: written,
    style: defaultTextStyle(draft),
  })
}

/** Change what one text overlay says, or how it is drawn. */
function updateOverlay(
  draft: CompositionV1,
  action: {
    overlayId: string
    patch: { text?: string; style?: Partial<TextStyle>; placement?: TextOverlay['placement'] }
  },
): void {
  const overlay = draft.overlays.find((candidate) => candidate.id === action.overlayId)
  if (overlay === undefined || overlay.type !== 'text') {
    return
  }
  const text = action.patch.text?.trim()
  if (text !== undefined && text !== '') {
    overlay.text = text
  }
  if (action.patch.placement !== undefined) {
    overlay.placement = action.patch.placement
  }
  if (action.patch.style !== undefined) {
    Object.assign(overlay.style, action.patch.style)
  }
}

/** Move one overlay's window, keeping it inside the clip and long enough to read. */
function moveOverlay(
  draft: CompositionV1,
  action: { overlayId: string; startMs: number; endMs: number },
): void {
  const overlay = draft.overlays.find((candidate) => candidate.id === action.overlayId)
  if (overlay === undefined) {
    return
  }
  const endMs = clamp(Math.round(action.endMs), MIN_ITEM_MS, draft.durationMs)
  const startMs = clamp(Math.round(action.startMs), 0, endMs - MIN_ITEM_MS)
  overlay.timelineStartMs = startMs
  overlay.timelineEndMs = endMs
}

/** Leave one named marker at the playhead, in the order the timeline reaches them. */
function addBookmark(draft: CompositionV1, label: string, playheadMs: number): void {
  const named = label.trim()
  if (named === '') {
    return
  }
  const timelineMs = clamp(Math.round(playheadMs), 0, draft.durationMs)
  draft.bookmarks.push({ id: nextBookmarkId(draft), timelineMs, label: named })
  draft.bookmarks.sort((left, right) => left.timelineMs - right.timelineMs)
}

/** Rename one marker without moving it. */
function renameBookmark(draft: CompositionV1, action: { bookmarkId: string; label: string }): void {
  const named = action.label.trim()
  const bookmark = draft.bookmarks.find((candidate) => candidate.id === action.bookmarkId)
  if (bookmark !== undefined && named !== '') {
    bookmark.label = named
  }
}


/**
 * Move one caption word's own timing, inside the gap its neighbours leave it.
 *
 * Transcription produced these timestamps, so retiming is deliberate work rather than a
 * side effect — and it may never produce two words that overlap, because karaoke would
 * then have two active words at once.
 */
function retimeWord(
  draft: CompositionV1,
  action: { wordId: string; startMs: number; endMs: number },
): void {
  const index = draft.captions.words.findIndex((word) => word.id === action.wordId)
  const word = draft.captions.words[index]
  if (word === undefined) {
    return
  }
  const earliest = draft.captions.words[index - 1]?.endMs ?? 0
  const latest = draft.captions.words[index + 1]?.startMs ?? draft.durationMs
  const startMs = clamp(Math.round(action.startMs), earliest, latest - MIN_WORD_MS)
  const endMs = clamp(Math.round(action.endMs), startMs + MIN_WORD_MS, latest)
  word.startMs = startMs
  word.endMs = endMs
}

/**
 * Write one published look into the document, and record the version it came from.
 *
 * Only type and colour change: a look is never allowed to touch the clip, its items, or
 * the words the transcript produced.
 */
function applyTemplate(draft: CompositionV1, template: TemplateDefinition): void {
  draft.template = { id: template.id, version: template.version }
  draft.captions.mode = template.captionMode
  draft.captions.style = { ...template.captionStyle }
  for (const overlay of draft.overlays) {
    if (overlay.type === 'text') {
      overlay.style = { ...template.textStyle }
    }
  }
}

/** Give one element a movement, but only if it is on screen long enough to show it. */
function setMotion(draft: CompositionV1, action: { targetId: string; preset: MotionPreset }): void {
  const target = animatable(draft, action.targetId)
  if (target === null) {
    return
  }
  if (!motionFits(action.preset, target.durationMs)) {
    return
  }
  target.setMotion(action.preset)
}

/** Add one keyframe at one instant, replacing any keyframe already standing there. */
function addKeyframe(
  draft: CompositionV1,
  action: { targetId: string; atMs: number; transform?: Transform; opacity?: number },
): void {
  const target = animatable(draft, action.targetId)
  if (target === null) {
    return
  }
  if (action.transform === undefined && action.opacity === undefined) {
    return
  }
  const atMs = clamp(Math.round(action.atMs), 0, target.durationMs)
  const kept = target.keyframes().filter((frame) => frame.atMs !== atMs)
  const added: Keyframe = {
    atMs,
    easing: 'easeInOut',
    transform: action.transform ?? null,
    opacity: action.opacity ?? null,
    style: null,
  }
  target.setKeyframes([...kept, added].sort((left, right) => left.atMs - right.atMs))
}

/** Move one keyframe to another instant, unless another keyframe already holds it. */
function moveKeyframe(
  draft: CompositionV1,
  action: { targetId: string; atMs: number; toMs: number },
): void {
  const target = animatable(draft, action.targetId)
  if (target === null) {
    return
  }
  const toMs = clamp(Math.round(action.toMs), 0, target.durationMs)
  const frames = target.keyframes()
  if (frames.some((frame) => frame.atMs === toMs) || !frames.some((f) => f.atMs === action.atMs)) {
    return
  }
  target.setKeyframes(
    frames
      .map((frame) => (frame.atMs === action.atMs ? { ...frame, atMs: toMs } : frame))
      .sort((left, right) => left.atMs - right.atMs),
  )
}

/** Remove the keyframe standing at one instant. */
function removeKeyframe(
  draft: CompositionV1,
  action: { targetId: string; atMs: number },
): void {
  const target = animatable(draft, action.targetId)
  if (target === null) {
    return
  }
  target.setKeyframes(target.keyframes().filter((frame) => frame.atMs !== action.atMs))
}

/**
 * One element that can be animated, whichever kind of element it happens to be.
 *
 * An item and an overlay carry their keyframes in the same shape but describe their own
 * length differently, so this is the one place that difference is spelled out.
 */
function animatable(
  draft: CompositionV1,
  targetId: string,
): {
  durationMs: number
  keyframes: () => Keyframe[]
  setKeyframes: (frames: Keyframe[]) => void
  setMotion: (preset: MotionPreset) => void
} | null {
  const found = locate(draft, targetId)
  if (found !== null) {
    const { item } = found
    return {
      durationMs: item.sourceOutMs - item.sourceInMs,
      keyframes: () => item.keyframes,
      setKeyframes: (frames) => {
        item.keyframes = frames
      },
      setMotion: (preset) => {
        item.motion = preset
      },
    }
  }
  const overlay = draft.overlays.find((candidate) => candidate.id === targetId)
  if (overlay === undefined || overlay.type === 'citation') {
    return null
  }
  return {
    durationMs: overlay.timelineEndMs - overlay.timelineStartMs,
    keyframes: () => overlay.keyframes,
    setKeyframes: (frames) => {
      overlay.keyframes = frames
    },
    setMotion: (preset) => {
      overlay.motion = preset
    },
  }
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

/** Carry each item's caption words with the item when the running order changes. */
function remapCaptions(
  draft: CompositionV1,
  moves: Array<{ oldStart: number; newStart: number; lengthMs: number }>,
): void {
  draft.captions.words = draft.captions.words
    .map((word) => {
      const move = moves.find(
        (candidate) =>
          word.startMs >= candidate.oldStart && word.startMs < candidate.oldStart + candidate.lengthMs,
      )
      if (move === undefined) {
        return word
      }
      const shift = move.newStart - move.oldStart
      return { ...word, startMs: word.startMs + shift, endMs: word.endMs + shift }
    })
    .sort((left, right) => left.startMs - right.startMs)
}

/**
 * Lay the base lane out end to end, and let the duration follow the longest lane.
 *
 * Sound lanes keep their own offsets: a bed dropped at eight seconds belongs at eight
 * seconds, and packing it back to zero would be an edit nobody asked for.
 */
function relayout(draft: CompositionV1): void {
  for (const track of draft.tracks) {
    if (track.type !== 'video') {
      continue
    }
    let cursor = 0
    for (const item of track.items) {
      item.timelineStartMs = cursor
      cursor += item.sourceOutMs - item.sourceInMs
    }
  }
  const ends = draft.tracks.flatMap((track) =>
    track.items.map((item) => item.timelineStartMs + (item.sourceOutMs - item.sourceInMs)),
  )
  const duration = Math.max(...ends, MIN_ITEM_MS)
  draft.durationMs = duration
  draft.captions.words = draft.captions.words.filter((word) => word.endMs <= duration)
  draft.bookmarks = draft.bookmarks.filter((bookmark) => bookmark.timelineMs <= duration)
  draft.overlays = draft.overlays
    .filter((overlay) => overlay.timelineStartMs + MIN_ITEM_MS <= duration)
    .map((overlay) =>
      overlay.timelineEndMs <= duration ? overlay : { ...overlay, timelineEndMs: duration },
    )
}

/** The first start at or after one instant where an item of this length fits. */
function freeStart(track: Track, desiredMs: number, lengthMs: number, itemId: string): number {
  const others = track.items
    .filter((item) => item.id !== itemId)
    .map((item) => ({
      startMs: item.timelineStartMs,
      endMs: item.timelineStartMs + (item.sourceOutMs - item.sourceInMs),
    }))
    .sort((left, right) => left.startMs - right.startMs)
  let start = Math.max(0, desiredMs)
  for (const other of others) {
    if (start < other.endMs && start + lengthMs > other.startMs) {
      const before = other.startMs - lengthMs
      start = before >= 0 && fits(others, before, lengthMs) ? before : other.endMs
    }
  }
  return start
}

/** Whether one span is free of every other item on its lane. */
function fits(
  others: Array<{ startMs: number; endMs: number }>,
  startMs: number,
  lengthMs: number,
): boolean {
  return others.every((other) => startMs >= other.endMs || startMs + lengthMs <= other.startMs)
}

/** How far one sound item may be dragged or stretched before it meets a neighbour. */
function boundsOf(track: Track, item: TrackItem): { earliestStart: number; latestEnd: number } {
  const others = track.items
    .filter((candidate) => candidate.id !== item.id)
    .map((candidate) => ({
      startMs: candidate.timelineStartMs,
      endMs: candidate.timelineStartMs + (candidate.sourceOutMs - candidate.sourceInMs),
    }))
  const earlier = others.filter((other) => other.endMs <= item.timelineStartMs)
  const later = others.filter((other) => other.startMs >= item.timelineStartMs)
  return {
    earliestStart: Math.max(0, ...earlier.map((other) => other.endMs)),
    latestEnd: Math.min(
      Number.MAX_SAFE_INTEGER,
      ...later.map((other) => other.startMs),
    ),
  }
}

/** Keep one lane's items in the order it plays them. */
function sortTrack(track: Track): void {
  track.items.sort((left, right) => left.timelineStartMs - right.timelineStartMs)
}

/** Every item of the base video lane. */
function videoItems(composition: CompositionV1): TrackItem[] {
  return composition.tracks.filter((track) => track.type === 'video').flatMap((track) => track.items)
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

/** Name a new item after the one it came from, without colliding with any other. */
function nextItemId(composition: CompositionV1, itemId: string): string {
  const taken = new Set(timelineItems(composition).map((placed) => placed.item.id))
  let suffix = 2
  while (taken.has(`${itemId}-${suffix}`)) {
    suffix += 1
  }
  return `${itemId}-${suffix}`
}

/** Name a new lane after the kind of sound it carries. */
function nextTrackId(composition: CompositionV1, trackType: SoundTrackKind): string {
  const taken = new Set(composition.tracks.map((track) => track.id))
  let suffix = 1
  while (taken.has(`${trackType}-${suffix}`)) {
    suffix += 1
  }
  return `${trackType}-${suffix}`
}

/** Name a new text overlay so no two overlays share an identifier. */
function nextOverlayId(composition: CompositionV1): string {
  const taken = new Set(composition.overlays.map((overlay) => overlay.id))
  let suffix = 1
  while (taken.has(`text-${suffix}`)) {
    suffix += 1
  }
  return `text-${suffix}`
}

/** Name a new marker so no two markers share an identifier. */
function nextBookmarkId(composition: CompositionV1): string {
  const taken = new Set(composition.bookmarks.map((bookmark) => bookmark.id))
  let suffix = 1
  while (taken.has(`bookmark-${suffix}`)) {
    suffix += 1
  }
  return `bookmark-${suffix}`
}

/** The type a new overlay is written in: the caption type, without its karaoke colour. */
function defaultTextStyle(composition: CompositionV1): TextStyle {
  const style: Partial<CaptionStyle> & TextStyle = { ...composition.captions.style }
  delete style.highlightColor
  return { ...style, fontSize: 48 }
}

/** How long the source this clip was cut from runs. */
function sourceLengthMs(composition: CompositionV1): number {
  return composition.sourceRange.outMs - composition.sourceRange.inMs
}

/** The item the editor selects when it opens. */
function firstItemId(composition: CompositionV1): string | null {
  return timelineItems(composition)[0]?.item.id ?? null
}

/** Keep one number inside the range it is allowed to take. */
function clamp(value: number, lowest: number, highest: number): number {
  return Math.max(lowest, Math.min(value, highest))
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
