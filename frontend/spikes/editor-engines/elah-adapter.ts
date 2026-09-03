/**
 * The preferred candidate: Elah's `TimelineEngine` under Apache-2.0.
 *
 * Elah owns tracks, clips, trimming, splitting, and a history stack, so this adapter
 * drives those rather than reimplementing them. What Elah has no model for — caption word
 * timings, karaoke highlighting, and a crop rectangle — stays on Clipah's side of the
 * boundary in `extras`, and is carried through every operation by this file.
 *
 * That split is the finding, not an accident of the spike: an engine that cannot hold
 * part of Clipah's composition means Clipah keeps a parallel model and keeps the two in
 * step. Every Elah type stays inside this module; the port is all anything else sees.
 */

import {
  GpuRenderer,
  TimelineEngine,
  createDefaultDemuxerFactory,
  resolveTimeline,
  type Clip,
  type Project,
} from '@elah/core'

import {
  EngineCapabilityError,
  canonicalize,
  centreCrop,
  locateItem,
  ASPECT_CANVAS,
  type Aspect,
  type BrowserEditorEngine,
  type CaptionItem,
  type CaptionWord,
  type Crop,
  type EngineOptions,
  type HistorySnapshot,
  type MediaItem,
  type PreviewFrame,
  type SpikeComposition,
  type Waveform,
} from './adapter'

/** What Clipah keeps about an item because Elah has nowhere to put it. */
interface ItemExtras {
  trackId: string
  kind: 'video' | 'audio' | 'caption'
  crop: Crop | null
  words: CaptionWord[]
  style: CaptionItem['style'] | null
}

/**
 * How long one frame is given to decode before the measurement calls it a miss.
 *
 * Generous on purpose: Elah's default demuxer downloads the whole source before it can
 * decode anything, so the first frame of a long proxy carries that download. A tight
 * timeout would report "did not decode" where the honest answer is "took this long".
 */
const FRAME_TIMEOUT_MS = 120_000

/** Build one engine backed by Elah's timeline. */
export function createElahEngine(options: EngineOptions = {}): BrowserEditorEngine {
  return new ElahEngine(options)
}

class ElahEngine implements BrowserEditorEngine {
  private readonly options: EngineOptions
  private renderer: GpuRenderer | null = null
  private mountPoint: HTMLElement | null = null
  private timeline: TimelineEngine | null = null
  /** Clipah item id to Elah clip id. Elah mints its own; nothing outside sees them. */
  private clipIds = new Map<string, string>()
  private extras = new Map<string, ItemExtras>()
  private trackOrder: { id: string; kind: 'video' | 'audio' | 'caption' }[] = []
  private elahTrackIds = new Map<string, string>()
  private aspect: Aspect = '16:9'
  private past: string[] = []
  private future: string[] = []
  private frame = 0
  private playing = false
  private disposed = false

  constructor(options: EngineOptions) {
    this.options = options
  }

  async load(composition: SpikeComposition): Promise<void> {
    this.requireLive()
    const canonical = canonicalize(composition)
    this.mount(composition)
    this.past = [canonical]
    this.future = []
    this.frame = 0
    this.playing = false
    if (canonicalize(this.readComposition()) !== canonical) {
      throw new EngineCapabilityError(
        'round-trip',
        'Elah did not give back the composition it was given.',
      )
    }
  }

  /** Build Elah's timeline, and Clipah's side state, from one composition. */
  private mount(composition: SpikeComposition): void {
    const timeline = new TimelineEngine({
      fps: composition.frameRate,
      stage: { width: composition.canvas.width, height: composition.canvas.height },
      initialTracks: [],
    })
    this.timeline = timeline
    this.clipIds = new Map()
    this.extras = new Map()
    this.trackOrder = []
    this.elahTrackIds = new Map()
    this.aspect = composition.canvas.aspect

    for (const track of composition.tracks) {
      // Elah knows video, audio, and an `elements` track; a caption track is elements.
      const elahTrack = timeline.addTrack(track.kind === 'caption' ? 'elements' : track.kind, {
        name: track.id,
      })
      this.elahTrackIds.set(track.id, elahTrack.id)
      this.trackOrder.push({ id: track.id, kind: track.kind })
      for (const item of track.items) {
        this.addItem(track.id, item)
      }
    }
  }

  composition(): SpikeComposition {
    this.requireLive()
    return JSON.parse(this.present()) as SpikeComposition
  }

  seek(frame: number): void {
    this.requireLive()
    this.frame = frame
  }

  currentFrame(): number {
    return this.frame
  }

  play(): void {
    this.requireLive()
    this.playing = true
  }

  pause(): void {
    this.requireLive()
    this.playing = false
  }

  isPlaying(): boolean {
    return this.playing
  }

  /**
   * Draw one frame, and do not return until the decoded picture is actually on screen.
   *
   * Elah's renderer is synchronous but its frames are not: `VideoLayer` asks a provider
   * for the source frame and draws whatever it already has. Returning after the first
   * `render` would time how long it takes to ask, which is not what a reviewer waits for,
   * so this re-renders until the provider actually holds the frame that was requested.
   */
  async renderPreviewFrame(frame: number): Promise<PreviewFrame> {
    this.requireLive()
    // Elah previews through `GpuRenderer`, which needs a WebGL2 context. Reporting the
    // missing capability is the honest answer; a made-up frame would make the parity
    // comparison in the adoption gate meaningless.
    if (!hasWebGl2()) {
      throw new EngineCapabilityError(
        'webgl2',
        'Elah renders previews through WebGL2, which this environment does not provide.',
      )
    }
    const { canvas } = this.composition()
    const renderer = this.requireRenderer(canvas.width, canvas.height)
    const scene = resolveTimeline(frame, this.requireTimeline().getProject())
    renderer.render(scene)

    const decoded = await this.awaitDecodedFrame(renderer, scene.videos)
    return { frame, width: canvas.width, height: canvas.height, decoded }
  }

  async getWaveform(assetId: string): Promise<Waveform> {
    this.requireLive()
    void assetId
    // `@elah/core` publishes no waveform API at all: its audio surface is playback and a
    // resolver, and nothing that reads an envelope. The adoption gate lists waveform as a
    // required capability, so this is a real gap rather than a missing environment.
    throw new EngineCapabilityError(
      'waveform',
      'Elah exposes no waveform API; Clipah would have to read envelopes itself.',
    )
  }

  trimItem(itemId: string, startFrame: number, durationFrames: number): void {
    this.edit(() => {
      const { timeline, clipId, trackId } = this.clipFor(itemId)
      timeline.trimClip(clipId, trackId, startFrame, durationFrames)
    })
  }

  splitItem(itemId: string, atFrame: number): [string, string] {
    const rightId = `${itemId}-b`
    this.edit(() => {
      const { timeline, clipId, trackId, clip } = this.clipFor(itemId)
      if (atFrame <= clip.startFrame || atFrame >= clip.startFrame + clip.durationFrames) {
        throw new EngineCapabilityError(
          'split-bounds',
          `Frame ${atFrame} is not inside the item being split.`,
        )
      }
      const halves = timeline.splitClip(clipId, trackId, atFrame)
      if (halves === null) {
        throw new EngineCapabilityError('split', `Elah refused to split ${itemId}.`)
      }
      const [left, right] = halves
      this.clipIds.set(itemId, left)
      this.clipIds.set(rightId, right)
      const extras = this.extrasFor(itemId)
      const offset = atFrame - clip.startFrame
      this.extras.set(rightId, {
        ...extras,
        crop: extras.crop === null ? null : { ...extras.crop },
        words: extras.words
          .filter((word) => word.startFrame >= offset)
          .map((word) => ({ ...word, startFrame: word.startFrame - offset })),
        style: extras.style === null ? null : { ...extras.style },
      })
      this.extras.set(itemId, {
        ...extras,
        words: extras.words.filter((word) => word.startFrame < offset),
      })
    })
    return [itemId, rightId]
  }

  editCaptionWords(itemId: string, words: CaptionWord[]): void {
    this.edit(() => {
      const extras = this.extrasFor(itemId)
      if (extras.kind !== 'caption') {
        throw new EngineCapabilityError('captions', `Item ${itemId} carries no caption words.`)
      }
      // Elah's text clip holds one string, so the rendered line is written back to it
      // while the word timings stay on Clipah's side, untouched by the engine.
      const { timeline, clipId, trackId } = this.clipFor(itemId)
      timeline.updateClip(clipId, trackId, {
        content: words.map((word) => word.text).join(' '),
      })
      this.extras.set(itemId, { ...extras, words: words.map((word) => ({ ...word })) })
    })
  }

  applyAspect(aspect: Aspect): void {
    this.edit(() => {
      const timeline = this.requireTimeline()
      const stage = timeline.getProject().stage
      const canvas = ASPECT_CANVAS[aspect]
      const crop = centreCrop(stage.width / stage.height, canvas.width / canvas.height)
      timeline.setStage(canvas.width, canvas.height)
      this.aspect = aspect
      for (const [itemId, extras] of this.extras) {
        if (extras.kind === 'video') {
          this.extras.set(itemId, { ...extras, crop: { ...crop } })
        }
      }
    })
  }

  undo(): boolean {
    this.requireLive()
    if (this.past.length <= 1) {
      return false
    }
    // Elah's own history covers Elah's timeline only. It cannot restore a crop or a
    // caption's word timings, because Elah never held them, so the composition is
    // restored from Clipah's snapshot and the timeline is rebuilt from that. Elah's
    // `undo`/`redo` are unusable for Clipah's model; the ADR records the cost.
    const undone = this.past.pop()
    if (undone === undefined) {
      return false
    }
    this.future.unshift(undone)
    this.rebuildFromPresent()
    return true
  }

  redo(): boolean {
    this.requireLive()
    const next = this.future.shift()
    if (next === undefined) {
      return false
    }
    this.past.push(next)
    this.rebuildFromPresent()
    return true
  }

  serializeHistory(): HistorySnapshot {
    this.requireLive()
    return { past: [...this.past], future: [...this.future] }
  }

  restoreHistory(snapshot: HistorySnapshot): void {
    this.requireLive()
    if (snapshot.past.length === 0) {
      throw new EngineCapabilityError('history', 'A history with no present cannot be restored.')
    }
    this.past = [...snapshot.past]
    this.future = [...snapshot.future]
    this.rebuildFromPresent()
  }

  async dispose(): Promise<void> {
    this.disposed = true
    this.renderer?.dispose()
    this.renderer = null
    this.mountPoint?.remove()
    this.mountPoint = null
    this.timeline = null
    this.clipIds.clear()
    this.extras.clear()
    this.past = []
    this.future = []
  }

  /** Run one change through Elah, then record the composition it produced. */
  private edit(change: () => void): void {
    this.requireLive()
    change()
    this.past.push(canonicalize(this.readComposition()))
    this.future = []
  }

  /** Put Elah back where a restored snapshot says the composition is. */
  private rebuildFromPresent(): void {
    this.mount(JSON.parse(this.present()) as SpikeComposition)
  }

  /** Read Elah's timeline back out as Clipah's composition. */
  private readComposition(): SpikeComposition {
    const timeline = this.requireTimeline()
    const project = timeline.getProject()
    return {
      schemaVersion: 1,
      frameRate: project.fps,
      canvas: { width: project.stage.width, height: project.stage.height, aspect: this.aspect },
      tracks: this.trackOrder.map((track) => ({
        id: track.id,
        kind: track.kind,
        items: this.itemsOf(project, track.id),
      })),
    }
  }

  /** The items of one Clipah track, rebuilt from Elah's clips and Clipah's extras. */
  private itemsOf(project: Project, trackId: string): (MediaItem | CaptionItem)[] {
    const elahTrackId = this.elahTrackIds.get(trackId)
    if (elahTrackId === undefined) {
      return []
    }
    const clips = project.clips[elahTrackId] ?? []
    const items: (MediaItem | CaptionItem)[] = []
    for (const clip of [...clips].sort((left, right) => left.startFrame - right.startFrame)) {
      const itemId = this.itemIdOf(clip.id)
      if (itemId === null) {
        continue
      }
      const extras = this.extrasFor(itemId)
      if (extras.kind === 'caption') {
        items.push({
          id: itemId,
          kind: 'caption',
          startFrame: clip.startFrame,
          durationFrames: clip.durationFrames,
          words: extras.words.map((word) => ({ ...word })),
          style: { ...requireStyle(extras) },
        })
        continue
      }
      items.push({
        id: itemId,
        kind: extras.kind,
        assetId: clip.assetId ?? '',
        startFrame: clip.startFrame,
        durationFrames: clip.durationFrames,
        sourceStartFrame: clip.sourceStartFrame,
        crop: extras.crop === null ? null : { ...extras.crop },
        opacity: clip.opacity ?? 1,
      })
    }
    return items
  }

  /** Put one Clipah item into Elah, keeping what Elah cannot hold on this side. */
  private addItem(trackId: string, item: MediaItem | CaptionItem): void {
    const timeline = this.requireTimeline()
    const elahTrackId = this.elahTrackIds.get(trackId)
    if (elahTrackId === undefined) {
      throw new EngineCapabilityError('tracks', `Track ${trackId} was never created.`)
    }
    if (item.kind === 'caption') {
      const clip = timeline.addClip({
        type: 'text',
        trackId: elahTrackId,
        name: item.id,
        startFrame: item.startFrame,
        durationFrames: item.durationFrames,
        text: {
          content: item.words.map((word) => word.text).join(' '),
          fontFamily: item.style.fontFamily,
          fontSize: item.style.fontSize,
          color: item.style.color,
        },
      })
      this.clipIds.set(item.id, clip.id)
      this.extras.set(item.id, {
        trackId,
        kind: 'caption',
        crop: null,
        words: item.words.map((word) => ({ ...word })),
        style: { ...item.style },
      })
      return
    }
    const clip = timeline.addClip({
      type: item.kind,
      trackId: elahTrackId,
      name: item.id,
      startFrame: item.startFrame,
      durationFrames: item.durationFrames,
      src: this.options.resolveAsset?.(item.assetId) ?? `clipah://asset/${item.assetId}`,
      assetId: item.assetId,
      opacity: item.opacity,
    })
    // Elah computes a source offset when a clip is created, so the one the composition
    // actually names is written back before anything reads it.
    timeline.updateClip(clip.id, elahTrackId, { sourceStartFrame: item.sourceStartFrame })
    this.clipIds.set(item.id, clip.id)
    this.extras.set(item.id, {
      trackId,
      kind: item.kind,
      crop: item.crop === null ? null : { ...item.crop },
      words: [],
      style: null,
    })
  }

  private clipFor(itemId: string): {
    timeline: TimelineEngine
    clipId: string
    trackId: string
    clip: Clip
  } {
    const timeline = this.requireTimeline()
    const clipId = this.clipIds.get(itemId)
    if (clipId === undefined) {
      throw new EngineCapabilityError('item-lookup', `No item ${itemId} exists in this timeline.`)
    }
    const found = timeline.findClip(clipId)
    if (found === null) {
      throw new EngineCapabilityError('item-lookup', `Elah lost the clip behind ${itemId}.`)
    }
    return { timeline, clipId, trackId: found.trackId, clip: found.clip }
  }

  private itemIdOf(clipId: string): string | null {
    for (const [itemId, known] of this.clipIds) {
      if (known === clipId) {
        return itemId
      }
    }
    return null
  }

  private extrasFor(itemId: string): ItemExtras {
    const extras = this.extras.get(itemId)
    if (extras === undefined) {
      throw new EngineCapabilityError('item-lookup', `Nothing is recorded for item ${itemId}.`)
    }
    return extras
  }

  private present(): string {
    const present = this.past[this.past.length - 1]
    if (present === undefined) {
      throw new EngineCapabilityError('load', 'No composition has been loaded.')
    }
    return present
  }

  /** Build the renderer once, wired to Mediabunny for demuxing, and keep it. */
  private requireRenderer(width: number, height: number): GpuRenderer {
    if (this.renderer === null) {
      const container = this.options.container ?? document.createElement('div')
      if (this.options.container === undefined) {
        document.body.appendChild(container)
        this.mountPoint = container
      }
      // Elah's default demuxer downloads the whole source as a Blob before it can decode
      // anything. That is recorded in the ADR: it is the difference between a range
      // request and a full download on a one-hour proxy.
      const renderer = new GpuRenderer({ demuxerFactory: createDefaultDemuxerFactory() })
      renderer.mount(container)
      renderer.resize(width, height, 1)
      this.renderer = renderer
    }
    return this.renderer
  }

  /** Keep drawing until the provider holds the frame that was asked for, or give up. */
  private async awaitDecodedFrame(
    renderer: GpuRenderer,
    videos: { id: string; sourceFrame: number }[],
  ): Promise<boolean> {
    if (videos.length === 0) {
      return false
    }
    const deadline = performance.now() + FRAME_TIMEOUT_MS
    while (performance.now() < deadline) {
      const layer = renderer.videoLayer
      const ready = videos.every((video) => {
        const provider = layer?.getProviderForItemId(video.id)
        provider?.setPlayhead(video.sourceFrame)
        return provider?.getCurrent(video.sourceFrame) != null
      })
      if (ready) {
        return true
      }
      await nextFrame()
    }
    return false
  }

  private requireTimeline(): TimelineEngine {
    this.requireLive()
    if (this.timeline === null) {
      throw new EngineCapabilityError('load', 'No composition has been loaded.')
    }
    return this.timeline
  }

  private requireLive(): void {
    if (this.disposed) {
      throw new EngineCapabilityError('disposed', 'This engine has been disposed.')
    }
  }
}

/** A caption item always has a style; say so plainly rather than inventing one. */
function requireStyle(extras: ItemExtras): CaptionItem['style'] {
  if (extras.style === null) {
    throw new EngineCapabilityError('captions', 'A caption item lost its style.')
  }
  return extras.style
}

/** Wait one paint, so a decode in flight gets a chance to land. */
function nextFrame(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(() => resolve())
      return
    }
    setTimeout(resolve, 16)
  })
}

/** Whether this environment can give Elah the context its renderer needs. */
function hasWebGl2(): boolean {
  if (typeof document === 'undefined') {
    return false
  }
  try {
    return document.createElement('canvas').getContext('webgl2') !== null
  } catch {
    return false
  }
}

// `locateItem` is the port's own lookup; it is used by callers of this adapter rather
// than by the adapter itself, and is re-exported so a spike script has one way to read a
// composition whichever candidate produced it.
export { locateItem }
