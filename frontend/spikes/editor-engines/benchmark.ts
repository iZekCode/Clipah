/**
 * The measurements the adoption gate turns into a decision.
 *
 * Everything here is injected — the clock, the heap reading, the worker count — so a run
 * is reproducible and so a missing capability is reported as missing rather than guessed
 * at. A gate with no observation is not a passed gate, and `verdict` says so.
 */

import type { BrowserEditorEngine, SpikeComposition } from './adapter'

/** The thresholds `plan.md` fixes for the reference development machine. */
export const GATES = {
  maxLoadMs: 5_000,
  maxMedianSeekMs: 150,
  maxHeapBytes: 1.5 * 1024 * 1024 * 1024,
  maxLeakedWorkers: 0,
} as const

/** What one run observed, with `null` wherever the environment could not tell us. */
export interface EngineMeasurement {
  engine: string
  loadMs: number
  /** Time from a cold engine to the first decoded picture on screen. */
  firstFrameMs: number
  /** What a person actually waits for before the editor is usable. */
  timeToUsableMs: number
  medianSeekMs: number
  p95SeekMs: number
  peakHeapBytes: number | null
  leakedWorkers: number | null
  seekSamples: number
  /** Seeks that produced a real decoded picture rather than only a resolved timeline. */
  decodedFrames: number
  /** What went wrong, in the engine's own words, for every seek that did not decode. */
  failures: string[]
}

/** One gate, and whether this run is allowed to claim it. */
export interface GateVerdict {
  gate: keyof typeof GATES
  observed: number | null
  limit: number
  passed: boolean
  unmeasured: boolean
}

/** The environment readings a run needs, injected so a test can pin every one. */
export interface BenchmarkEnvironment {
  now: () => number
  heapBytes: () => number | null
  liveWorkers: () => number | null
}

/** Read what a real browser can tell us, and admit the rest. */
export function browserEnvironment(): BenchmarkEnvironment {
  return {
    now: () => performance.now(),
    heapBytes: () => {
      const memory = (performance as { memory?: { usedJSHeapSize?: number } }).memory
      return typeof memory?.usedJSHeapSize === 'number' ? memory.usedJSHeapSize : null
    },
    // Only Chromium reports live workers, and only through the DevTools protocol, so a
    // page-side count is not available. A run that cannot count them says so.
    liveWorkers: () => null,
  }
}

/**
 * Load one composition, warm the engine, then time a spread of seeks across it.
 *
 * The first seek after a load is always the slowest, so it is discarded: the gate is
 * about how the editor behaves once someone is working in it.
 */
export async function measureEngine(
  engine: string,
  create: () => BrowserEditorEngine,
  composition: SpikeComposition,
  frames: number[],
  environment: BenchmarkEnvironment,
): Promise<EngineMeasurement> {
  const instance = create()
  const startedAt = environment.now()
  await instance.load(composition)
  const loadMs = environment.now() - startedAt

  // The first seek carries opening the media, and it is what a person waits through
  // before the editor is usable. It is excluded from the steady-state seek samples and
  // reported on its own rather than quietly discarded.
  const [warmup, ...measured] = frames
  let firstFrameMs = 0
  if (warmup !== undefined) {
    const before = environment.now()
    instance.seek(warmup)
    await instance.renderPreviewFrame(warmup).catch(() => undefined)
    firstFrameMs = environment.now() - before
  }
  const durations: number[] = []
  const failures: string[] = []
  let decodedFrames = 0
  for (const frame of measured) {
    const before = environment.now()
    instance.seek(frame)
    // A failure is recorded, never swallowed: a seek that decoded nothing is fast for
    // the wrong reason, and counting it as a passing sample is how a benchmark lies.
    try {
      const rendered = await instance.renderPreviewFrame(frame)
      if (rendered.decoded) {
        decodedFrames += 1
      } else {
        failures.push(`frame ${frame} rendered without decoding any media`)
      }
    } catch (error) {
      failures.push(`frame ${frame}: ${error instanceof Error ? error.message : String(error)}`)
    }
    durations.push(environment.now() - before)
  }

  const peakHeapBytes = environment.heapBytes()
  await instance.dispose()
  return {
    engine,
    loadMs,
    firstFrameMs,
    timeToUsableMs: loadMs + firstFrameMs,
    medianSeekMs: percentile(durations, 0.5),
    p95SeekMs: percentile(durations, 0.95),
    peakHeapBytes,
    leakedWorkers: environment.liveWorkers(),
    seekSamples: durations.length,
    decodedFrames,
    failures,
  }
}

/** Say which gates this run actually cleared, and which it could not observe at all. */
export function verdict(measurement: EngineMeasurement): GateVerdict[] {
  // A seek that decoded nothing is not a seek. Unless every sample produced a picture,
  // the timing samples describe something other than the gate, so they are withheld.
  const decodedEverything =
    measurement.seekSamples > 0 && measurement.decodedFrames === measurement.seekSamples
  return [
    gate('maxLoadMs', measurement.timeToUsableMs),
    gate('maxMedianSeekMs', decodedEverything ? measurement.medianSeekMs : null),
    gate('maxHeapBytes', measurement.peakHeapBytes),
    gate('maxLeakedWorkers', measurement.leakedWorkers),
  ]
}

function gate(name: keyof typeof GATES, observed: number | null): GateVerdict {
  const limit = GATES[name]
  return {
    gate: name,
    observed,
    limit,
    passed: observed !== null && observed <= limit,
    unmeasured: observed === null,
  }
}

/** The value at one position of the sorted samples, or zero when there are none. */
function percentile(samples: number[], fraction: number): number {
  if (samples.length === 0) {
    return 0
  }
  const sorted = [...samples].sort((left, right) => left - right)
  const index = Math.min(sorted.length - 1, Math.floor(fraction * sorted.length))
  return sorted[index] ?? 0
}

/** A timeline of the requested length, so a long-proxy run needs no fixture media file. */
export function longProxyComposition(minutes: number, frameRate = 30): SpikeComposition {
  const durationFrames = minutes * 60 * frameRate
  return {
    schemaVersion: 1,
    frameRate,
    canvas: { width: 1920, height: 1080, aspect: '16:9' },
    tracks: [
      {
        id: 'track-video',
        kind: 'video',
        items: [
          {
            id: 'item-source',
            kind: 'video',
            assetId: 'asset-proxy',
            startFrame: 0,
            durationFrames,
            sourceStartFrame: 0,
            crop: null,
            opacity: 1,
          },
        ],
      },
    ],
  }
}

/** The frames a seek measurement visits: spread across the whole timeline, never repeated. */
export function seekPlan(composition: SpikeComposition, samples: number): number[] {
  const total = composition.tracks
    .flatMap((track) => track.items)
    .reduce((longest, item) => Math.max(longest, item.startFrame + item.durationFrames), 0)
  return Array.from({ length: samples }, (_, index) =>
    Math.floor((index / samples) * Math.max(total - 1, 0)),
  )
}
