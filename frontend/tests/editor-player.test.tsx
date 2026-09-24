import { act, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, test } from 'vitest'

import type { PreviewEngine } from '@/features/editor/engine'
import { Player } from '@/features/editor/Player'
import type { CompositionV1 } from '@/lib/api/generated/model'

const SOURCE_IN_MS = 1_931_485

/** One 30-second clip cut from late in its source, on a portrait canvas. */
function composition(crop: CompositionV1['tracks'][number]['items'][number]['crop']): CompositionV1 {
  return {
    schemaVersion: 1,
    sourceAssetId: 'source',
    durationMs: 30_000,
    canvas: { width: 1080, height: 1920, background: '#000000' },
    sourceRange: { inMs: SOURCE_IN_MS, outMs: SOURCE_IN_MS + 30_000 },
    template: null,
    brandKit: null,
    tracks: [
      {
        id: 'main-video',
        type: 'video',
        items: [
          {
            id: 'scene-1',
            sourceAssetId: 'source',
            timelineStartMs: 0,
            sourceInMs: SOURCE_IN_MS,
            sourceOutMs: SOURCE_IN_MS + 30_000,
            transform: { x: 0.5, y: 0.5, scale: 1, rotation: 0 },
            crop,
            opacity: 1,
            blendMode: 'normal',
            motion: 'none',
            origin: { type: 'source', suggestionId: null, provenanceId: null },
            keyframes: [],
          },
        ],
      },
    ],
    captions: {
      mode: 'off',
      words: [],
      style: {
        fontFamily: 'Montserrat',
        fontSize: 64,
        color: '#FFFFFF',
        highlightColor: '#FFD166',
        align: 'center',
        weight: 700,
        italic: false,
        decoration: 'none',
        letterSpacing: 0,
        lineHeight: 1.2,
        backgroundEnabled: false,
        backgroundColor: '#000000',
      },
    },
    overlays: [],
    audio: { gainDb: 0, musicGainDb: -18 },
    bookmarks: [],
  } as CompositionV1
}

/** An engine that plays nothing, but remembers every seek and where it is. */
function recordingEngine() {
  let at = 0
  const seeks: number[] = []
  const engine: PreviewEngine = {
    attach: () => {},
    seek: (ms) => {
      seeks.push(ms)
      at = ms
    },
    play: () => {},
    pause: () => {},
    isPlaying: () => false,
    currentSourceMs: () => at,
    dispose: () => {},
  }
  return {
    engine,
    seeks,
    /** Move the media on by itself, as playback does, and report it. */
    advanceTo(ms: number) {
      at = ms
      const video = screen.getByTestId('editor-video')
      Object.defineProperty(video, 'currentTime', { configurable: true, value: ms / 1000 })
      fireEvent.timeUpdate(video)
    },
  }
}

/** The Player as the editor holds it: the playhead follows whatever the Player reports. */
function Harness({
  engine,
  initialPlaying,
  crop = null,
  url = 'proxy.mp4',
}: {
  engine: PreviewEngine
  initialPlaying: boolean
  crop?: CompositionV1['tracks'][number]['items'][number]['crop']
  url?: string
}) {
  const [playheadMs, setPlayheadMs] = useState(0)
  const [playing, setPlaying] = useState(initialPlaying)
  return (
    <>
      <Player
        composition={composition(crop)}
        source={{ url, durationMs: null, width: 1920, height: 1080 }}
        playheadMs={playheadMs}
        playing={playing}
        onSeek={setPlayheadMs}
        onPlayingChange={setPlaying}
        engine={engine}
      />
      <p data-testid="state">{playing ? 'playing' : 'paused'}</p>
      <button type="button" onClick={() => setPlayheadMs(12_000)}>
        Jump
      </button>
      <button type="button" onClick={() => setPlayheadMs((ms) => ms + 33)}>
        Step
      </button>
    </>
  )
}

describe('Player', () => {
  test('playback is never pulled back to the time it has just reported', () => {
    const recorder = recordingEngine()
    render(<Harness engine={recorder.engine} initialPlaying />)
    const before = recorder.seeks.length

    act(() => recorder.advanceTo(SOURCE_IN_MS + 250))
    act(() => recorder.advanceTo(SOURCE_IN_MS + 500))

    expect(recorder.seeks.slice(before)).toEqual([])
  })

  test('a re-signed proxy that reloads from its start is put back at the playhead', () => {
    const recorder = recordingEngine()
    const view = render(<Harness engine={recorder.engine} initialPlaying />)
    act(() => screen.getByRole('button', { name: 'Jump' }).click())

    // Coming back to the tab re-signs the proxy; the new URL loads from 0:00.
    view.rerender(<Harness engine={recorder.engine} initialPlaying url="proxy.mp4?fresh" />)
    act(() => recorder.advanceTo(0))
    act(() => {
      fireEvent.loadedMetadata(screen.getByTestId('editor-video'))
    })

    expect(screen.getByTestId('state')).toHaveTextContent('playing')
    expect(recorder.seeks.at(-1)).toBe(SOURCE_IN_MS + 12_000)
  })

  test('playback still stops once the media runs past the end of the clip', () => {
    const recorder = recordingEngine()
    render(<Harness engine={recorder.engine} initialPlaying />)

    act(() => recorder.advanceTo(SOURCE_IN_MS + 30_000))

    expect(screen.getByTestId('state')).toHaveTextContent('paused')
  })

  test('moving the playhead elsewhere still seeks the media there', () => {
    const recorder = recordingEngine()
    render(<Harness engine={recorder.engine} initialPlaying />)

    act(() => screen.getByRole('button', { name: 'Jump' }).click())

    expect(recorder.seeks.at(-1)).toBe(SOURCE_IN_MS + 12_000)
  })

  test('while paused, a move of a single frame still seeks', () => {
    const recorder = recordingEngine()
    render(<Harness engine={recorder.engine} initialPlaying={false} />)
    act(() => screen.getByRole('button', { name: 'Jump' }).click())

    act(() => screen.getByRole('button', { name: 'Step' }).click())

    expect(recorder.seeks.at(-1)).toBe(SOURCE_IN_MS + 12_033)
  })

  test('a crop fills the canvas with its window, without stretching the frame again', () => {
    // A 16:9 source framed for 9:16: the window is 0.316 of the width, all of the height.
    const width = 9 / 16 / (16 / 9)
    const recorder = recordingEngine()
    render(
      <Harness
        engine={recorder.engine}
        initialPlaying={false}
        crop={{ x: (1 - width) / 2, y: 0, width, height: 1 }}
      />,
    )

    const video = screen.getByTestId('editor-video')
    expect(video.style.transform).toBe('')
    expect(video.style.objectFit).toBe('fill')
    expect(parseFloat(video.style.width)).toBeCloseTo(100 / width, 3)
    expect(parseFloat(video.style.height)).toBeCloseTo(100, 3)
    expect(parseFloat(video.style.left)).toBeCloseTo((-(1 - width) / 2 / width) * 100, 3)
    expect(parseFloat(video.style.top)).toBeCloseTo(0, 3)
  })
})

describe('the caption the preview draws', () => {
  const words = [
    { id: 'w1', startMs: 0, endMs: 400, text: 'Terus', speaker: null },
    { id: 'w2', startMs: 400, endMs: 800, text: 'titik', speaker: null },
    { id: 'w3', startMs: 800, endMs: 1_300, text: 'baliknya', speaker: null },
    { id: 'w4', startMs: 1_300, endMs: 1_600, text: 'tuh', speaker: null },
    { id: 'w5', startMs: 1_600, endMs: 2_000, text: 'kapan', speaker: null },
    { id: 'w6', startMs: 2_000, endMs: 2_400, text: 'lagi', speaker: null },
  ]

  /** A still Player at one instant, with the given caption mode. */
  function drawAt(playheadMs: number, mode: CompositionV1['captions']['mode']) {
    const base = composition(null)
    const recorder = recordingEngine()
    render(
      <Player
        composition={{ ...base, captions: { ...base.captions, mode, words } }}
        source={{ url: 'proxy.mp4', durationMs: null, width: 1920, height: 1080 }}
        playheadMs={playheadMs}
        playing={false}
        onSeek={() => {}}
        onPlayingChange={() => {}}
        engine={recorder.engine}
      />,
    )
    return screen.getByTestId('editor-caption')
  }

  test('shows the whole line the export draws, not only the word being said', () => {
    // The export draws at most five words a line, so the sixth starts the next one.
    expect(drawAt(900, 'block')).toHaveTextContent('Terus titik baliknya tuh kapan')
  })

  test('karaoke lights the words already said and leaves the rest', () => {
    const caption = drawAt(900, 'karaoke')

    const lit = [...caption.querySelectorAll('[data-said="true"]')].map((word) => word.textContent)
    expect(lit.map((text) => text?.trim())).toEqual(['Terus', 'titik', 'baliknya'])
  })

  test('the next line begins once the first is full', () => {
    expect(drawAt(2_100, 'block')).toHaveTextContent(/^lagi$/)
  })
})
