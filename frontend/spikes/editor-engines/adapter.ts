/**
 * The browser editor port the bake-off measures candidates against.
 *
 * This is a time-boxed spike. Nothing outside `frontend/spikes/` may import it, and no
 * candidate engine's types appear in it: an engine that cannot be described through this
 * port is an engine Clipah would have to model its compositions around, which is the cost
 * the bake-off exists to find. Task 22 owns the real `CompositionV1` and its JSON Schema;
 * the shape here is the smallest one that can exercise every operation the adoption gate
 * in `plan.md` names, and it is deliberately disposable.
 */

/** The aspect presets the editor must support. */
export type Aspect = '9:16' | '16:9' | '1:1' | '4:5'

/** The canvas each aspect preset renders into. */
export const ASPECT_CANVAS: Record<Aspect, { width: number; height: number }> = {
  '9:16': { width: 1080, height: 1920 },
  '16:9': { width: 1920, height: 1080 },
  '1:1': { width: 1080, height: 1080 },
  '4:5': { width: 1080, height: 1350 },
}

/** A normalized crop rectangle, in fractions of the source frame. */
export interface Crop {
  x: number
  y: number
  width: number
  height: number
}

/** One caption word, carrying the authoritative timing transcription produced. */
export interface CaptionWord {
  id: string
  text: string
  startFrame: number
  durationFrames: number
}

interface ItemBase {
  id: string
  startFrame: number
  durationFrames: number
}

/** One piece of source media placed on the timeline. */
export interface MediaItem extends ItemBase {
  kind: 'video' | 'audio'
  assetId: string
  sourceStartFrame: number
  crop: Crop | null
  opacity: number
}

/** One caption item, whose words carry karaoke timing of their own. */
export interface CaptionItem extends ItemBase {
  kind: 'caption'
  words: CaptionWord[]
  style: {
    fontFamily: string
    fontSize: number
    color: string
    highlightColor: string
  }
}

export type CompositionItem = MediaItem | CaptionItem

/** One track of the timeline. */
export interface CompositionTrack {
  id: string
  kind: 'video' | 'audio' | 'caption'
  items: CompositionItem[]
}

/** The whole composition a candidate engine has to hold without changing it. */
export interface SpikeComposition {
  schemaVersion: 1
  frameRate: number
  canvas: { width: number; height: number; aspect: Aspect }
  tracks: CompositionTrack[]
}

/**
 * What an engine needs from the outside world to reach real media.
 *
 * Clipah's compositions name assets, never URLs, because a signed URL lives five minutes
 * and a composition lives forever. An engine that wants to decode is handed a resolver
 * at construction, and a run without one can only be measured on arithmetic.
 */
export interface EngineOptions {
  /** Turn one asset identifier into a URL this browser may fetch right now. */
  resolveAsset?: (assetId: string) => string
  /** Where a preview canvas may be mounted. Defaults to a detached element. */
  container?: HTMLElement
}

/** One preview frame, described without naming any engine's frame type. */
export interface PreviewFrame {
  frame: number
  width: number
  height: number
  /** Whether real media was decoded, or only the timeline was resolved. */
  decoded: boolean
}

/** One asset's audio envelope, as the timeline draws it. */
export interface Waveform {
  assetId: string
  peaks: number[]
}

/** Undo and redo state a candidate must be able to hand over and take back. */
export interface HistorySnapshot {
  past: string[]
  future: string[]
}

/**
 * What every candidate engine must do before it can be adopted.
 *
 * The seven members named in `plan.md` are here, plus the editing operations and the
 * composition read-back the adoption gate's byte-for-byte comparison needs: a candidate
 * that cannot return the composition it was given is a candidate Clipah cannot verify.
 */
export interface BrowserEditorEngine {
  load(composition: SpikeComposition): Promise<void>
  composition(): SpikeComposition
  seek(frame: number): void
  currentFrame(): number
  play(): void
  pause(): void
  isPlaying(): boolean
  renderPreviewFrame(frame: number): Promise<PreviewFrame>
  getWaveform(assetId: string): Promise<Waveform>
  trimItem(itemId: string, startFrame: number, durationFrames: number): void
  splitItem(itemId: string, atFrame: number): [string, string]
  editCaptionWords(itemId: string, words: CaptionWord[]): void
  applyAspect(aspect: Aspect): void
  undo(): boolean
  redo(): boolean
  serializeHistory(): HistorySnapshot
  restoreHistory(snapshot: HistorySnapshot): void
  dispose(): Promise<void>
}

/** Raised when a candidate cannot do something the adoption gate requires. */
export class EngineCapabilityError extends Error {
  readonly capability: string

  constructor(capability: string, message: string) {
    super(message)
    this.name = 'EngineCapabilityError'
    this.capability = capability
  }
}

/**
 * Serialize one composition the same way whoever produced it ordered its keys.
 *
 * Two engines agree only if their output is identical as text, so the comparison has to
 * be blind to key order and intolerant of values that cannot round-trip. A non-finite
 * number is rejected rather than written as `null`, because `null` would compare equal
 * across two engines that both lost the value.
 */
export function canonicalize(value: unknown): string {
  return JSON.stringify(canonicalValue(value))
}

function canonicalValue(value: unknown): unknown {
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      throw new EngineCapabilityError(
        'finite-numbers',
        'A composition may not carry a value that is not a finite number.',
      )
    }
    return value
  }
  if (Array.isArray(value)) {
    return value.map(canonicalValue)
  }
  if (typeof value === 'object' && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, entry]) => entry !== undefined)
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    return Object.fromEntries(entries.map(([key, entry]) => [key, canonicalValue(entry)]))
  }
  return value
}

/** Take a copy nothing else holds a reference into. */
export function cloneComposition(composition: SpikeComposition): SpikeComposition {
  return JSON.parse(JSON.stringify(composition)) as SpikeComposition
}

/** Find one item and the track holding it, or say plainly that it is not there. */
export function locateItem(
  composition: SpikeComposition,
  itemId: string,
): { track: CompositionTrack; item: CompositionItem; index: number } {
  for (const track of composition.tracks) {
    const index = track.items.findIndex((item) => item.id === itemId)
    if (index !== -1) {
      const item = track.items[index]
      if (item !== undefined) {
        return { track, item, index }
      }
    }
  }
  throw new EngineCapabilityError('item-lookup', `No item ${itemId} exists in this composition.`)
}

/**
 * The centre crop that fills one aspect from a source of another.
 *
 * The crop is normalized, so it stays correct whatever resolution the proxy happens to
 * be, and it is centred because a face-aware crop is a later task's decision, not this
 * spike's.
 */
export function centreCrop(sourceAspect: number, targetAspect: number): Crop {
  if (targetAspect < sourceAspect) {
    const width = targetAspect / sourceAspect
    return { x: (1 - width) / 2, y: 0, width, height: 1 }
  }
  const height = sourceAspect / targetAspect
  return { x: 0, y: (1 - height) / 2, width: 1, height }
}
