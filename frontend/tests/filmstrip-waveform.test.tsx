import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'

import { Filmstrip } from '@/components/media/filmstrip'
import { WaveformCanvas } from '@/components/media/waveform-canvas'
import type { StoryboardResponse } from '@/lib/api/generated/model'

const storyboard: StoryboardResponse = {
  version: 1,
  intervalMs: 2_000,
  tileWidth: 160,
  tileHeight: 90,
  columns: 10,
  rows: 10,
  durationMs: 205_000,
  expiresAt: '2026-09-17T00:05:00+00:00',
  sheets: [{ index: 0, startMs: 0, tileCount: 100, url: 'https://media.test/sheet-0.jpg' }],
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Filmstrip', () => {
  test('lays evenly spaced frames across the range', () => {
    render(<Filmstrip storyboard={storyboard} startMs={0} endMs={40_000} tileCount={4} />)

    const sprites = screen.getAllByTestId('sprite')
    expect(sprites).toHaveLength(4)
    expect(sprites[3]?.style.backgroundPosition).toBe('77.7778% 11.1111%')
  })

  test('without a storyboard it is an empty graphite strip', () => {
    const { container } = render(
      <Filmstrip storyboard={null} startMs={0} endMs={40_000} tileCount={4} />,
    )

    expect(screen.queryAllByTestId('sprite')).toHaveLength(0)
    expect(container.firstElementChild).toHaveClass('bg-stage')
  })
})

describe('WaveformCanvas', () => {
  test('draws one bar per three pixels and lime bars inside the selection', () => {
    const fillRect = vi.fn()
    const styles: string[] = []
    const context = {
      setTransform: vi.fn(),
      clearRect: vi.fn(),
      fillRect,
      set fillStyle(value: string) {
        styles.push(value)
      },
    }
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      context as unknown as CanvasRenderingContext2D,
    )
    vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(30)
    vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(20)

    render(
      <WaveformCanvas
        peaks={new Uint8Array(20).fill(255)}
        peaksPerSecond={20}
        startMs={0}
        endMs={1_000}
        selection={[0, 500]}
      />,
    )

    expect(fillRect).toHaveBeenCalledTimes(10)
    expect(styles.slice(0, 5).every((style) => style.includes('198 255 61'))).toBe(true)
    expect(styles.slice(5).every((style) => style.includes('161 161 168'))).toBe(true)
  })

  test('draws nothing until peaks arrive', () => {
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext')

    render(<WaveformCanvas peaks={null} peaksPerSecond={null} startMs={0} endMs={1_000} />)

    expect(getContext).not.toHaveBeenCalled()
  })
})
