import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { OverlayLayer, overlayMediaMs } from '@/features/editor/OverlayLayer'
import type { CompositionV1 } from '@/lib/api/generated/model'

type Overlay = CompositionV1['overlays'][number]

const ASSET_ID = '78cea5a5-3313-45b1-b609-1bcf1e1c607b'
const MEDIA = { [ASSET_ID]: 'https://storage.test/broll-proxy.mp4' }

type VideoOverlay = Extract<Overlay, { type: 'video' }>

/** The B-roll overlay accepting a suggestion produced, as the backend stored it. */
function broll(overrides: Partial<VideoOverlay> = {}): VideoOverlay {
  return {
    id: 'broll-1',
    type: 'video',
    assetId: ASSET_ID,
    blendMode: 'normal',
    keyframes: [],
    motion: 'none',
    opacity: 1,
    origin: { type: 'brollSuggestion', suggestionId: 'suggestion-1', provenanceId: null },
    placement: 'cover',
    preserveDialogueAudio: true,
    sourceInMs: 0,
    sourceOutMs: 3_120,
    timelineStartMs: 14_723,
    timelineEndMs: 17_843,
    ...overrides,
  } as VideoOverlay
}

function caption(): Overlay {
  return {
    id: 'text-1',
    type: 'text',
    text: 'Setelah dilamar',
    keyframes: [],
    motion: 'none',
    opacity: 1,
    placement: 'top',
    timelineStartMs: 0,
    timelineEndMs: 5_000,
    style: {
      fontFamily: 'Montserrat',
      fontSize: 72,
      color: '#FFFFFF',
      align: 'center',
      weight: 700,
      italic: false,
      decoration: 'none',
      letterSpacing: 0,
      lineHeight: 1.2,
      backgroundEnabled: false,
      backgroundColor: '#000000',
    },
  } as Overlay
}

function layer(overlays: Overlay[], playheadMs: number, playing = false) {
  return (
    <OverlayLayer
      overlays={overlays}
      playheadMs={playheadMs}
      playing={playing}
      canvasWidth={1080}
      media={MEDIA}
    />
  )
}

let play: ReturnType<typeof vi.spyOn>
let pause: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
  pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the overlay layer', () => {
  test('an accepted B-roll clip is drawn only while the timeline is inside its window', () => {
    const view = render(layer([broll()], 10_000))
    expect(screen.queryByTestId('overlay-broll-1')).not.toBeInTheDocument()

    view.rerender(layer([broll()], 15_000))
    const video = screen.getByTestId('overlay-broll-1')
    expect(video).toHaveAttribute('src', MEDIA[ASSET_ID])
    expect((video as HTMLVideoElement).muted).toBe(true)

    view.rerender(layer([broll()], 17_843))
    expect(screen.queryByTestId('overlay-broll-1')).not.toBeInTheDocument()
  })

  test('a B-roll clip plays the part of its own media the timeline has reached', () => {
    expect(overlayMediaMs(broll(), 14_723)).toBe(0)
    expect(overlayMediaMs(broll({ sourceInMs: 2_000 }), 15_723)).toBe(3_000)
  })

  test('a B-roll clip plays and pauses with the clip', () => {
    const view = render(layer([broll()], 15_000, true))
    expect(play).toHaveBeenCalled()

    view.rerender(layer([broll()], 15_000, false))
    expect(pause).toHaveBeenCalled()
  })

  test('media whose preview link has not arrived yet is simply not drawn', () => {
    render(
      <OverlayLayer
        overlays={[broll()]}
        playheadMs={15_000}
        playing={false}
        canvasWidth={1080}
        media={{}}
      />,
    )

    expect(screen.queryByTestId('overlay-broll-1')).not.toBeInTheDocument()
  })

  test('a still is drawn over the frame for its window', () => {
    render(layer([broll({ type: 'image' } as never)], 15_000))

    expect(screen.getByTestId('overlay-broll-1').tagName).toBe('IMG')
  })

  test('text is drawn where the export draws it', () => {
    render(layer([caption()], 1_000))

    const text = screen.getByText('Setelah dilamar')
    expect(text.closest('[data-placement]')).toHaveAttribute('data-placement', 'top')
  })
})
