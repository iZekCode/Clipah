import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { captionPhrases } from '@/features/editor/caption-phrases'
import { Timeline, fitZoom } from '@/features/editor/Timeline'
import { TimelineToolbar } from '@/features/editor/TimelineToolbar'

import { composition } from './support/fixtures'

function renderTimeline(overrides: Partial<Parameters<typeof Timeline>[0]> = {}) {
  const handlers = {
    onSelect: vi.fn(),
    onSelectTrack: vi.fn(),
    onSeek: vi.fn(),
    onMove: vi.fn(),
    onResize: vi.fn(),
    onRemoveMarker: vi.fn(),
  }
  render(
    <Timeline
      composition={composition({ bookmarks: [{ id: 'b1', label: 'Payoff', timelineMs: 12_000 }] })}
      selectedItemId={null}
      selectedTrackId={null}
      playheadMs={5_000}
      zoom={16}
      snapping
      lockedTrackIds={[]}
      storyboard={null}
      peaks={null}
      peaksPerSecond={null}
      sourceAssetId={composition().sourceAssetId}
      {...handlers}
      {...overrides}
    />,
  )
  return handlers
}

describe('caption phrases', () => {
  test('group words into short phrases, breaking at pauses', () => {
    const words = composition().captions.words
    expect(captionPhrases(words).map((phrase) => phrase.text)).toEqual([
      'Ini cara',
      'kerja',
      'editornya',
    ])
  })
})

describe('Timeline', () => {
  test('the ruler is the scrub control and names the playhead position', () => {
    const { onSeek } = renderTimeline()

    const scrub = screen.getByRole('slider', { name: 'Scrub the clip' })
    expect(scrub).toHaveAttribute('aria-valuetext', '0:05.00')
    fireEvent.change(scrub, { target: { value: '12000' } })

    expect(onSeek).toHaveBeenCalledWith(12_000)
    expect(screen.getByTestId('editor-playhead')).toHaveStyle({ left: '80px' })
  })

  test('each lane has a label column, and markers sit on the ruler as flags', async () => {
    const user = userEvent.setup()
    const { onSeek, onRemoveMarker } = renderTimeline()

    const timeline = screen.getByRole('region', { name: 'Timeline' })
    expect(within(timeline).getByText('Video')).toBeInTheDocument()
    expect(within(timeline).getByRole('group', { name: 'Captions lane' })).toBeInTheDocument()
    await user.click(within(timeline).getByRole('button', { name: '0:12 Payoff' }))
    await user.click(within(timeline).getByRole('button', { name: 'Remove the marker Payoff' }))

    expect(onSeek).toHaveBeenCalledWith(12_000)
    expect(onRemoveMarker).toHaveBeenCalledWith('b1')
  })

  test('video items show frames when a storyboard exists', () => {
    renderTimeline({
      storyboard: {
        version: 1,
        intervalMs: 2_000,
        tileWidth: 160,
        tileHeight: 90,
        columns: 10,
        rows: 10,
        durationMs: 60_000,
        expiresAt: '2026-02-01T00:05:00+00:00',
        sheets: [{ index: 0, startMs: 0, tileCount: 30, url: 'https://media.test/0.jpg' }],
      },
    })

    const item = screen.getByRole('button', { name: 'Select scene-1' })
    expect(within(item).getAllByTestId('sprite').length).toBeGreaterThan(0)
  })

  test('fit picks the closest zoom that shows the whole clip', () => {
    expect(fitZoom(1_000, 30_000)).toBe(32)
    expect(fitZoom(100, 600_000)).toBe(4)
  })
})

describe('TimelineToolbar', () => {
  test('tools are named icon buttons and snap and ripple are pressed toggles', async () => {
    const user = userEvent.setup()
    const onRipple = vi.fn()
    render(
      <TimelineToolbar
        snapping
        ripple={false}
        hasSelection
        markerCount={0}
        zoom={16}
        onSnapping={vi.fn()}
        onRipple={onRipple}
        onSplit={vi.fn()}
        onSplitAwayLeft={vi.fn()}
        onSplitAwayRight={vi.fn()}
        onDuplicate={vi.fn()}
        onDelete={vi.fn()}
        onAddMarker={vi.fn()}
        onPreviousMarker={vi.fn()}
        onNextMarker={vi.fn()}
        onAddTrack={vi.fn()}
        onZoom={vi.fn()}
        onFit={vi.fn()}
      />,
    )

    for (const name of [
      'Split',
      'Split away the left',
      'Split away the right',
      'Duplicate',
      'Delete',
      'Add marker',
      'Add a music lane',
      'Add an audio lane',
      'Zoom in',
      'Zoom out',
      'Fit the clip',
    ]) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument()
    }
    expect(screen.getByRole('button', { name: 'Snap to edges' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await user.click(screen.getByRole('button', { name: 'Ripple edits' }))
    expect(onRipple).toHaveBeenCalledWith(true)
  })
})
