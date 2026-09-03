import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, test } from 'vitest'

import {
  ASPECT_CANVAS,
  EngineCapabilityError,
  canonicalize,
  cloneComposition,
  locateItem,
  type BrowserEditorEngine,
  type CaptionItem,
  type MediaItem,
  type SpikeComposition,
} from '@/spikes/editor-engines/adapter'
import { createElahEngine } from '@/spikes/editor-engines/elah-adapter'
import { createOpenReelMediabunnyEngine } from '@/spikes/editor-engines/openreel-mediabunny-adapter'

/**
 * The adoption gate, written once and run against every candidate.
 *
 * Nothing here names an engine. A candidate that needs Clipah to hold its composition in
 * the engine's own shape fails these tests, and that failure is the finding the ADR is
 * for.
 */

const FIXTURE = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../contracts/fixtures/editor/parity-composition.json',
)

/** The fixture composition, read fresh so no test can leak state into another. */
function fixture(): SpikeComposition {
  return JSON.parse(readFileSync(FIXTURE, 'utf8')) as SpikeComposition
}

/** Every candidate the bake-off is comparing. */
const CANDIDATES: [string, () => BrowserEditorEngine][] = [
  ['elah', createElahEngine],
  ['openreel-mediabunny', createOpenReelMediabunnyEngine],
]

const open: BrowserEditorEngine[] = []

/** Run something that is expected to refuse, and hand back the refusal itself. */
function trap(run: () => unknown): unknown {
  try {
    run()
  } catch (error) {
    return error
  }
  return null
}

/** Build one engine and remember it, so no test can leave a candidate running. */
function engineFrom(create: () => BrowserEditorEngine): BrowserEditorEngine {
  const engine = create()
  open.push(engine)
  return engine
}

/**
 * Run the whole editing sequence the adoption gate names, in one order.
 *
 * Every candidate gets the same calls, so the composition each one ends holding is the
 * only thing that can differ.
 */
async function runGateSequence(engine: BrowserEditorEngine): Promise<void> {
  await engine.load(fixture())
  engine.splitItem('item-source', 300)
  engine.trimItem('item-source', 30, 240)
  engine.editCaptionWords('item-caption', [
    { id: 'w000001', text: 'Begini', startFrame: 0, durationFrames: 12 },
    { id: 'w000002', text: 'cara', startFrame: 12, durationFrames: 15 },
    { id: 'w000003', text: 'kerja', startFrame: 27, durationFrames: 18 },
    { id: 'w000004', text: 'editornya', startFrame: 45, durationFrames: 24 },
  ])
  engine.applyAspect('9:16')
}

afterEach(async () => {
  await Promise.all(open.splice(0).map((engine) => engine.dispose()))
})

describe.each(CANDIDATES)('%s as a browser editor engine', (name, create) => {
  test('gives back exactly the composition it was given', async () => {
    const engine = engineFrom(create)

    await engine.load(fixture())

    expect(canonicalize(engine.composition())).toBe(canonicalize(fixture()))
  })

  test('seeks to the exact frame it was asked for, at both boundaries', async () => {
    const engine = engineFrom(create)
    await engine.load(fixture())

    for (const frame of [0, 1, 299, 300, 899]) {
      engine.seek(frame)
      expect(engine.currentFrame()).toBe(frame)
    }
  })

  test('plays and pauses without losing where it was', async () => {
    const engine = engineFrom(create)
    await engine.load(fixture())
    engine.seek(120)

    engine.play()
    expect(engine.isPlaying()).toBe(true)
    engine.pause()

    expect(engine.isPlaying()).toBe(false)
    expect(engine.currentFrame()).toBe(120)
  })

  test('trims one item on the timeline and inside its source alike', async () => {
    const engine = engineFrom(create)
    await engine.load(fixture())

    engine.trimItem('item-source', 30, 600)

    const trimmed = locateItem(engine.composition(), 'item-source').item as MediaItem
    expect(trimmed.startFrame).toBe(30)
    expect(trimmed.durationFrames).toBe(600)
    expect(trimmed.sourceStartFrame).toBe(3630)
  })

  test('splits one item into two that together cover what the one covered', async () => {
    const engine = engineFrom(create)
    await engine.load(fixture())

    const [leftId, rightId] = engine.splitItem('item-source', 300)

    const left = locateItem(engine.composition(), leftId).item as MediaItem
    const right = locateItem(engine.composition(), rightId).item as MediaItem
    expect(left.startFrame).toBe(0)
    expect(left.durationFrames).toBe(300)
    expect(right.startFrame).toBe(300)
    expect(right.durationFrames).toBe(600)
    expect(right.sourceStartFrame).toBe(3900)
  })

  test('refuses to split anywhere the item does not actually cover', async () => {
    const engine = engineFrom(create)
    await engine.load(fixture())

    expect(() => engine.splitItem('item-source', 0)).toThrow(EngineCapabilityError)
    expect(() => engine.splitItem('item-source', 900)).toThrow(EngineCapabilityError)
  })

  test('splits a caption so each half keeps only the words it still covers', async () => {
    const engine = engineFrom(create)
    await engine.load(fixture())

    const [leftId, rightId] = engine.splitItem('item-caption', 30)

    const left = locateItem(engine.composition(), leftId).item as CaptionItem
    const right = locateItem(engine.composition(), rightId).item as CaptionItem
    expect(left.words.map((word) => word.id)).toEqual(['w000001', 'w000002', 'w000003'])
    expect(right.words.map((word) => word.id)).toEqual(['w000004'])
    expect(right.words.map((word) => word.startFrame)).toEqual([15])
  })

  test('edits caption words without moving the timings transcription produced', async () => {
    const engine = engineFrom(create)
    await engine.load(fixture())
    const before = (locateItem(fixture(), 'item-caption').item as CaptionItem).words

    engine.editCaptionWords(
      'item-caption',
      before.map((word) => (word.id === 'w000001' ? { ...word, text: 'Begini' } : word)),
    )

    const after = (locateItem(engine.composition(), 'item-caption').item as CaptionItem).words
    expect(after.map((word) => word.text)).toEqual(['Begini', 'cara', 'kerja', 'editornya'])
    expect(after.map((word) => [word.startFrame, word.durationFrames])).toEqual(
      before.map((word) => [word.startFrame, word.durationFrames]),
    )
  })

  test('applies a 9:16 crop to every visual item and to the canvas', async () => {
    const engine = engineFrom(create)
    await engine.load(fixture())

    engine.applyAspect('9:16')

    const composition = engine.composition()
    expect(composition.canvas).toEqual({ ...ASPECT_CANVAS['9:16'], aspect: '9:16' })
    const video = locateItem(composition, 'item-source').item as MediaItem
    expect(video.crop).toEqual({ x: 0.341796875, y: 0, width: 0.31640625, height: 1 })
    const audio = locateItem(composition, 'item-dialogue').item as MediaItem
    expect(audio.crop).toBeNull()
  })

  test('undoes and redoes every edit, one at a time', async () => {
    const engine = engineFrom(create)
    await engine.load(fixture())
    const original = canonicalize(engine.composition())
    engine.trimItem('item-source', 30, 600)
    const trimmed = canonicalize(engine.composition())
    engine.applyAspect('9:16')
    const cropped = canonicalize(engine.composition())

    expect(engine.undo()).toBe(true)
    expect(canonicalize(engine.composition())).toBe(trimmed)
    expect(engine.undo()).toBe(true)
    expect(canonicalize(engine.composition())).toBe(original)
    expect(engine.undo()).toBe(false)

    expect(engine.redo()).toBe(true)
    expect(canonicalize(engine.composition())).toBe(trimmed)
    expect(engine.redo()).toBe(true)
    expect(canonicalize(engine.composition())).toBe(cropped)
    expect(engine.redo()).toBe(false)
  })

  test('hands its undo history over and takes it back unchanged', async () => {
    const engine = engineFrom(create)
    await engine.load(fixture())
    engine.trimItem('item-source', 30, 600)
    engine.applyAspect('9:16')
    engine.undo()
    const snapshot = engine.serializeHistory()
    const state = canonicalize(engine.composition())

    const restored = engineFrom(create)
    await restored.load(fixture())
    restored.restoreHistory(snapshot)

    expect(canonicalize(restored.composition())).toBe(state)
    expect(restored.redo()).toBe(true)
    expect(restored.undo()).toBe(true)
    expect(canonicalize(restored.composition())).toBe(state)
  })

  test('renders a preview frame, or says which capability it needs to', async () => {
    const engine = engineFrom(create)
    await engine.load(fixture())

    const rendered = await engine.renderPreviewFrame(120).catch((error: unknown) => error)

    if (rendered instanceof EngineCapabilityError) {
      expect(rendered.capability).not.toBe('')
      return
    }
    expect(rendered).toEqual({ frame: 120, width: 1920, height: 1080 })
  })

  test('reads a waveform, or says which capability it needs to', async () => {
    const engine = engineFrom(create)
    await engine.load(fixture())

    const waveform = await engine.getWaveform('asset-proxy').catch((error: unknown) => error)

    if (waveform instanceof EngineCapabilityError) {
      expect(waveform.capability).not.toBe('')
      return
    }
    expect(waveform).toMatchObject({ assetId: 'asset-proxy' })
  })

  test('is inert once disposed', async () => {
    const engine = create()
    await engine.load(fixture())

    await engine.dispose()

    const refusal = trap(() => engine.composition())
    expect(refusal).toBeInstanceOf(EngineCapabilityError)
    expect((refusal as EngineCapabilityError).capability).toBe('disposed')
  })

  test(`${name} refuses a composition it cannot represent rather than losing part of it`, async () => {
    const engine = engineFrom(create)
    const broken = cloneComposition(fixture())
    const item = locateItem(broken, 'item-source').item as MediaItem
    item.opacity = Number.POSITIVE_INFINITY

    await expect(engine.load(broken)).rejects.toBeInstanceOf(EngineCapabilityError)
  })
})

describe('the bake-off comparison', () => {
  test('every candidate ends the same sequence holding the same composition', async () => {
    const results: string[] = []
    for (const [, create] of CANDIDATES) {
      const engine = engineFrom(create)
      await runGateSequence(engine)
      results.push(canonicalize(engine.composition()))
    }

    expect(new Set(results).size).toBe(1)
  })
})
