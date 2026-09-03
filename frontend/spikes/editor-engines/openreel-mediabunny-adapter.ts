/**
 * The comparison candidate: OpenReel's architecture, implemented over Mediabunny.
 *
 * OpenReel publishes no embeddable timeline package, so what is actually being compared
 * is its approach — own the timeline model, and reach for Mediabunny only at the media
 * boundary for demuxing, decoding, and muxing. That is what this adapter does: every
 * timeline operation is Clipah's own arithmetic over Clipah's own composition, and the
 * only thing delegated is work a browser primitive has to do.
 *
 * The cost of the approach is visible in this file: the timeline, its history, and its
 * frame arithmetic are ours to write and to keep correct. The benefit is that no library
 * ever holds the composition, so there is nothing to migrate away from.
 */

import {
  ALL_FORMATS,
  AudioBufferSink,
  CanvasSink,
  Input,
  UrlSource,
  type InputAudioTrack,
  type InputVideoTrack,
} from 'mediabunny'

import {
  EngineCapabilityError,
  canonicalize,
  centreCrop,
  cloneComposition,
  locateItem,
  ASPECT_CANVAS,
  type Aspect,
  type BrowserEditorEngine,
  type CaptionItem,
  type CaptionWord,
  type EngineOptions,
  type HistorySnapshot,
  type MediaItem,
  type PreviewFrame,
  type SpikeComposition,
  type Waveform,
} from './adapter'

/** How many buckets one envelope is reduced to before the timeline draws it. */
const WAVEFORM_BUCKETS = 1_000

/** One opened source, kept so a seek does not reopen the file every time. */
interface OpenSource {
  input: Input
  video: InputVideoTrack | null
  audio: InputAudioTrack | null
  canvasSink: CanvasSink | null
}

/** Build one engine that owns its own timeline and defers only media work. */
export function createOpenReelMediabunnyEngine(
  options: EngineOptions = {},
): BrowserEditorEngine {
  return new OpenReelMediabunnyEngine(options)
}

class OpenReelMediabunnyEngine implements BrowserEditorEngine {
  private readonly options: EngineOptions
  private readonly sources = new Map<string, Promise<OpenSource>>()
  private canvas: HTMLCanvasElement | null = null
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
    // Canonicalizing first is the validation: a composition carrying something that
    // cannot round-trip is refused whole rather than loaded with a hole in it.
    const canonical = canonicalize(composition)
    this.past = [canonical]
    this.future = []
    this.frame = 0
    this.playing = false
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
   * Decode the frame this timeline shows at `frame`, and draw it.
   *
   * Mediabunny reads the source over range requests rather than downloading it, and
   * `CanvasSink.getCanvas` resolves only once the picture exists, so the time this takes
   * is the time a reviewer waits after moving the playhead.
   */
  async renderPreviewFrame(frame: number): Promise<PreviewFrame> {
    this.requireLive()
    // Mediabunny decodes through WebCodecs. Outside a browser that supports it there is
    // no frame to return, and inventing one would make the parity comparison worthless.
    if (typeof globalThis.VideoDecoder === 'undefined') {
      throw new EngineCapabilityError(
        'webcodecs',
        'Mediabunny decodes through WebCodecs, which this environment does not provide.',
      )
    }
    const composition = this.composition()
    const showing = visibleVideo(composition, frame)
    if (showing === null || this.options.resolveAsset === undefined) {
      return { frame, width: composition.canvas.width, height: composition.canvas.height, decoded: false }
    }

    const source = await this.open(this.options.resolveAsset(showing.assetId))
    if (source.canvasSink === null) {
      throw new EngineCapabilityError('video-track', 'That source carries no video track.')
    }
    const seconds =
      (showing.sourceStartFrame + (frame - showing.startFrame)) / composition.frameRate
    const decoded = await source.canvasSink.getCanvas(seconds)
    if (decoded !== null) {
      this.paint(decoded.canvas, composition.canvas.width, composition.canvas.height)
    }
    return {
      frame,
      width: composition.canvas.width,
      height: composition.canvas.height,
      decoded: decoded !== null,
    }
  }

  /** Read the real envelope, reduced to the buckets a timeline can draw. */
  async getWaveform(assetId: string): Promise<Waveform> {
    this.requireLive()
    if (typeof globalThis.AudioDecoder === 'undefined') {
      throw new EngineCapabilityError(
        'webcodecs-audio',
        'Reading an envelope needs an audio decoder this environment does not provide.',
      )
    }
    if (this.options.resolveAsset === undefined) {
      throw new EngineCapabilityError('asset-resolver', 'No URL was supplied for that asset.')
    }
    const source = await this.open(this.options.resolveAsset(assetId))
    if (source.audio === null) {
      throw new EngineCapabilityError('audio-track', 'That source carries no audio track.')
    }
    const sink = new AudioBufferSink(source.audio)
    const peaks = new Array<number>(WAVEFORM_BUCKETS).fill(0)
    const duration = await source.audio.computeDuration()
    for await (const wrapped of sink.buffers()) {
      const bucket = Math.min(
        WAVEFORM_BUCKETS - 1,
        Math.floor((wrapped.timestamp / Math.max(duration, 1e-6)) * WAVEFORM_BUCKETS),
      )
      peaks[bucket] = Math.max(peaks[bucket] ?? 0, peakOf(wrapped.buffer))
    }
    return { assetId, peaks }
  }

  trimItem(itemId: string, startFrame: number, durationFrames: number): void {
    this.edit((composition) => {
      const { item } = locateItem(composition, itemId)
      const shift = startFrame - item.startFrame
      item.startFrame = startFrame
      item.durationFrames = durationFrames
      if (item.kind !== 'caption') {
        item.sourceStartFrame += shift
      }
    })
  }

  splitItem(itemId: string, atFrame: number): [string, string] {
    const rightId = `${itemId}-b`
    this.edit((composition) => {
      const { track, item, index } = locateItem(composition, itemId)
      requireInside(item.startFrame, item.durationFrames, atFrame)
      const offset = atFrame - item.startFrame
      const right = splitTail(item, atFrame, offset, rightId)
      item.durationFrames = offset
      if (item.kind === 'caption') {
        item.words = item.words.filter((word) => word.startFrame < offset)
      }
      track.items.splice(index + 1, 0, right)
    })
    return [itemId, rightId]
  }

  editCaptionWords(itemId: string, words: CaptionWord[]): void {
    this.edit((composition) => {
      const { item } = locateItem(composition, itemId)
      if (item.kind !== 'caption') {
        throw new EngineCapabilityError('captions', `Item ${itemId} carries no caption words.`)
      }
      item.words = words.map((word) => ({ ...word }))
    })
  }

  applyAspect(aspect: Aspect): void {
    this.edit((composition) => {
      const source = composition.canvas.width / composition.canvas.height
      const canvas = ASPECT_CANVAS[aspect]
      const crop = centreCrop(source, canvas.width / canvas.height)
      composition.canvas = { ...canvas, aspect }
      for (const track of composition.tracks) {
        for (const item of track.items) {
          if (item.kind === 'video') {
            item.crop = { ...crop }
          }
        }
      }
    })
  }

  undo(): boolean {
    this.requireLive()
    if (this.past.length <= 1) {
      return false
    }
    const undone = this.past.pop()
    if (undone === undefined) {
      return false
    }
    this.future.unshift(undone)
    return true
  }

  redo(): boolean {
    this.requireLive()
    const next = this.future.shift()
    if (next === undefined) {
      return false
    }
    this.past.push(next)
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
  }

  async dispose(): Promise<void> {
    this.disposed = true
    for (const opening of this.sources.values()) {
      await opening.then((source) => source.input.dispose()).catch(() => undefined)
    }
    this.sources.clear()
    this.canvas?.remove()
    this.canvas = null
    this.past = []
    this.future = []
  }

  /** Open one source at most once, however many seeks reach for it. */
  private open(url: string): Promise<OpenSource> {
    const existing = this.sources.get(url)
    if (existing !== undefined) {
      return existing
    }
    // A URL source reads what it needs over range requests, so opening a one-hour proxy
    // costs a header read rather than a download.
    const opening = (async (): Promise<OpenSource> => {
      const input = new Input({ formats: ALL_FORMATS, source: new UrlSource(url) })
      const video = await input.getPrimaryVideoTrack()
      const audio = await input.getPrimaryAudioTrack()
      return { input, video, audio, canvasSink: video === null ? null : new CanvasSink(video) }
    })()
    this.sources.set(url, opening)
    return opening
  }

  /** Put the decoded picture somewhere real, so a measured frame is a drawn frame. */
  private paint(decoded: CanvasImageSource, width: number, height: number): void {
    if (this.canvas === null) {
      const canvas = this.options.container?.appendChild(document.createElement('canvas'))
        ?? document.body.appendChild(document.createElement('canvas'))
      this.canvas = canvas
    }
    this.canvas.width = width
    this.canvas.height = height
    this.canvas.getContext('2d')?.drawImage(decoded, 0, 0, width, height)
  }

  /** Apply one change, recording the state it produced so it can be undone. */
  private edit(change: (composition: SpikeComposition) => void): void {
    this.requireLive()
    const next = cloneComposition(this.composition())
    change(next)
    this.past.push(canonicalize(next))
    this.future = []
  }

  private present(): string {
    const present = this.past[this.past.length - 1]
    if (present === undefined) {
      throw new EngineCapabilityError('load', 'No composition has been loaded.')
    }
    return present
  }

  private requireLive(): void {
    if (this.disposed) {
      throw new EngineCapabilityError('disposed', 'This engine has been disposed.')
    }
  }
}

/** The video item covering one timeline frame, or nothing when none does. */
function visibleVideo(composition: SpikeComposition, frame: number): MediaItem | null {
  for (const track of composition.tracks) {
    for (const item of track.items) {
      if (
        item.kind === 'video' &&
        frame >= item.startFrame &&
        frame < item.startFrame + item.durationFrames
      ) {
        return item
      }
    }
  }
  return null
}

/** The loudest sample in one decoded buffer. */
function peakOf(buffer: AudioBuffer): number {
  let peak = 0
  for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
    for (const sample of buffer.getChannelData(channel)) {
      peak = Math.max(peak, Math.abs(sample))
    }
  }
  return peak
}

/** Refuse a split that would produce an empty half. */
function requireInside(startFrame: number, durationFrames: number, atFrame: number): void {
  if (atFrame <= startFrame || atFrame >= startFrame + durationFrames) {
    throw new EngineCapabilityError(
      'split-bounds',
      `Frame ${atFrame} is not inside the item being split.`,
    )
  }
}

/** Build the second half of a split item, carrying its own source offset. */
function splitTail(
  item: MediaItem | CaptionItem,
  atFrame: number,
  offset: number,
  rightId: string,
): MediaItem | CaptionItem {
  const durationFrames = item.durationFrames - offset
  if (item.kind === 'caption') {
    const tail: CaptionItem = {
      ...item,
      id: rightId,
      startFrame: atFrame,
      durationFrames,
      words: item.words
        .filter((word) => word.startFrame >= offset)
        .map((word) => ({ ...word, startFrame: word.startFrame - offset })),
      style: { ...item.style },
    }
    return tail
  }
  const tail: MediaItem = {
    ...item,
    id: rightId,
    startFrame: atFrame,
    durationFrames,
    sourceStartFrame: item.sourceStartFrame + offset,
    crop: item.crop === null ? null : { ...item.crop },
  }
  return tail
}
