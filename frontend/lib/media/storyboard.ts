import type { StoryboardResponse } from '@/lib/api/generated/model'

/** One storyboard frame: which sheet, and where on it. */
export interface StoryboardTile {
  url: string
  column: number
  row: number
  columns: number
  rows: number
  tileWidth: number
  tileHeight: number
}

/**
 * The tile holding one moment of a source.
 *
 * Moments past the end clamp to the last frame, and a moment on a sheet that was never
 * produced falls back to the last frame that was — a poster shows a real picture or none.
 */
export function tileAt(storyboard: StoryboardResponse, ms: number): StoryboardTile | null {
  const sheets = [...storyboard.sheets].sort((left, right) => left.index - right.index)
  const last = sheets.at(-1)
  if (last === undefined) return null
  const perSheet = storyboard.columns * storyboard.rows
  const clamped = Math.min(Math.max(0, ms), Math.max(0, storyboard.durationMs - 1))
  const frame = Math.floor(clamped / storyboard.intervalMs)
  const sheetIndex = Math.floor(frame / perSheet)
  const exact = sheets.find((sheet) => sheet.index === sheetIndex)
  const sheet = exact ?? (sheetIndex > last.index ? last : sheets[0]!)
  const position =
    exact === undefined
      ? Math.max(0, sheet.tileCount - 1)
      : Math.min(frame - sheetIndex * perSheet, Math.max(0, sheet.tileCount - 1))
  return {
    url: sheet.url,
    column: position % storyboard.columns,
    row: Math.floor(position / storyboard.columns),
    columns: storyboard.columns,
    rows: storyboard.rows,
    tileWidth: storyboard.tileWidth,
    tileHeight: storyboard.tileHeight,
  }
}

/** Draw one tile as a CSS sprite that scales with its box. */
export function spriteStyle(tile: StoryboardTile): {
  backgroundImage: string
  backgroundSize: string
  backgroundPosition: string
} {
  const safeUrl = tile.url.replaceAll('\\', '%5C').replaceAll('"', '%22')
  return {
    backgroundImage: `url("${safeUrl}")`,
    backgroundSize: `${tile.columns * 100}% ${tile.rows * 100}%`,
    backgroundPosition: `${offset(tile.column, tile.columns)} ${offset(tile.row, tile.rows)}`,
  }
}

/** A poster opens one second in, or at the middle of a clip shorter than two seconds. */
export function posterTimeMs(startMs: number, endMs: number): number {
  return Math.min(startMs + 1_000, startMs + Math.floor((endMs - startMs) / 2))
}

/** Where a pointer at `fraction` of a poster's width lands inside the clip. */
export function scrubTimeMs(startMs: number, endMs: number, fraction: number): number {
  const bounded = Math.min(1, Math.max(0, fraction))
  return Math.round(startMs + (endMs - startMs) * bounded)
}

/** `count` tiles sampled at the centres of equal slices of a range. */
export function tilesAcross(
  storyboard: StoryboardResponse,
  startMs: number,
  endMs: number,
  count: number,
): StoryboardTile[] {
  const tiles: StoryboardTile[] = []
  for (let index = 0; index < count; index += 1) {
    const tile = tileAt(storyboard, startMs + ((index + 0.5) / count) * (endMs - startMs))
    if (tile === null) return []
    tiles.push(tile)
  }
  return tiles
}

function offset(index: number, count: number): string {
  // Four decimals is sub-pixel on any sheet; `Number` drops trailing zeros the way a
  // browser's own serialisation does, so `0%` stays `0%`.
  return `${Number((count <= 1 ? 0 : (index / (count - 1)) * 100).toFixed(4))}%`
}
