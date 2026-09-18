import { describe, expect, test } from 'vitest'

import type { StoryboardResponse } from '@/lib/api/generated/model'
import {
  posterTimeMs,
  scrubTimeMs,
  spriteStyle,
  tileAt,
  tilesAcross,
} from '@/lib/media/storyboard'
import { formatClock } from '@/lib/media/time'
import { peakBuckets } from '@/lib/media/waveform'

function storyboard(overrides: Partial<StoryboardResponse> = {}): StoryboardResponse {
  return {
    version: 1,
    intervalMs: 2_000,
    tileWidth: 160,
    tileHeight: 90,
    columns: 10,
    rows: 10,
    durationMs: 205_000,
    expiresAt: '2026-09-17T00:05:00+00:00',
    sheets: [
      { index: 0, startMs: 0, tileCount: 100, url: 'https://media.test/sheet-0.jpg' },
      { index: 1, startMs: 200_000, tileCount: 3, url: 'https://media.test/sheet-1.jpg' },
    ],
    ...overrides,
  }
}

describe('storyboard geometry', () => {
  test('finds the tile holding any moment of the source', () => {
    expect(tileAt(storyboard(), 0)).toMatchObject({
      url: 'https://media.test/sheet-0.jpg',
      column: 0,
      row: 0,
    })
    expect(tileAt(storyboard(), 23_500)).toMatchObject({ column: 1, row: 1 })
    expect(tileAt(storyboard(), 204_900)).toMatchObject({
      url: 'https://media.test/sheet-1.jpg',
      column: 2,
      row: 0,
    })
  })

  test('clamps past the end instead of inventing a frame', () => {
    expect(tileAt(storyboard(), 999_999)).toMatchObject({
      url: 'https://media.test/sheet-1.jpg',
      column: 2,
      row: 0,
    })
    expect(tileAt(storyboard(), -5)).toMatchObject({ column: 0, row: 0 })
  })

  test('falls back to the last frame it has when a later sheet is missing', () => {
    const partial = storyboard({ sheets: [storyboard().sheets[0]!] })
    expect(tileAt(partial, 204_900)).toMatchObject({
      url: 'https://media.test/sheet-0.jpg',
      column: 9,
      row: 9,
    })
    expect(tileAt(storyboard({ sheets: [] }), 0)).toBeNull()
  })

  test('draws a tile as a CSS sprite offset', () => {
    const tile = tileAt(storyboard(), 23_500)!
    expect(spriteStyle(tile)).toEqual({
      backgroundImage: 'url("https://media.test/sheet-0.jpg")',
      backgroundSize: '1000% 1000%',
      backgroundPosition: '11.1111% 11.1111%',
    })
  })

  test('refuses to let a URL break out of its CSS string', () => {
    const tile = { ...tileAt(storyboard(), 0)!, url: 'https://media.test/a"b\\c.jpg' }
    expect(spriteStyle(tile).backgroundImage).toBe('url("https://media.test/a%22b%5Cc.jpg")')
  })

  test('posters open one second in, or at the middle of a very short clip', () => {
    expect(posterTimeMs(5_000, 35_000)).toBe(6_000)
    expect(posterTimeMs(5_000, 6_000)).toBe(5_500)
  })

  test('scrubbing maps the pointer across the clip range and stays inside it', () => {
    expect(scrubTimeMs(5_000, 35_000, 0.5)).toBe(20_000)
    expect(scrubTimeMs(5_000, 35_000, -1)).toBe(5_000)
    expect(scrubTimeMs(5_000, 35_000, 2)).toBe(35_000)
  })

  test('spreads filmstrip tiles evenly across a range', () => {
    const tiles = tilesAcross(storyboard(), 0, 40_000, 4)
    expect(tiles.map((tile) => [tile.column, tile.row])).toEqual([
      [2, 0],
      [7, 0],
      [2, 1],
      [7, 1],
    ])
    expect(tilesAcross(storyboard({ sheets: [] }), 0, 40_000, 4)).toEqual([])
  })
})

describe('waveform buckets', () => {
  test('keeps the loudest peak of each bucket, scaled to one', () => {
    const peaks = new Uint8Array([0, 51, 255, 102, 0, 0, 204, 0])
    expect(peakBuckets(peaks, 20, 0, 400, 4)).toEqual([0.2, 1, 0, 0.8])
  })

  test('reads only the requested range', () => {
    const peaks = new Uint8Array([255, 0, 0, 51])
    expect(peakBuckets(peaks, 20, 100, 200, 1)).toEqual([0.2])
  })

  test('an empty range or no buckets draws silence', () => {
    expect(peakBuckets(new Uint8Array([255]), 20, 500, 500, 3)).toEqual([0, 0, 0])
    expect(peakBuckets(new Uint8Array([255]), 20, 0, 50, 0)).toEqual([])
  })
})

describe('clock', () => {
  test('reads minutes and seconds', () => {
    expect(formatClock(0)).toBe('0:00')
    expect(formatClock(64_400)).toBe('1:04')
    expect(formatClock(722_588)).toBe('12:03')
  })
})
