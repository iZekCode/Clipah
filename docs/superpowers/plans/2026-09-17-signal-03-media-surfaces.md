# Media Surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (the repository owner requires inline execution without subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make media visible everywhere: posters that scrub on hover, filmstrips, waveforms, and a real stage bar, applied to Home, Projects, Clips, the clip page, and the render queue.

**Architecture:** Pure geometry in `lib/media/` turns a storyboard manifest into CSS-sprite tiles and a peaks array into bars. Hooks in `features/media/` fetch manifests lazily (only when on screen) and cache them for four of their five signed minutes. Presentational components in `components/media/` draw tiles and bars and never fetch on their own except `Poster`, which owns its fallback chain. Screens compose these.

**Tech Stack:** Next.js 15, React 19, TypeScript strict, TanStack Query 5, Tailwind CSS 3, Vitest + Testing Library.

**Spec:** `redesign-plan-v2.md` → Components (`Poster`, `Filmstrip`, `Waveform`, `StageBar`), Screens (Home, Projects, Clips and clip page). Plan index: `docs/superpowers/plans/2026-09-17-signal-studio-redesign.md`.

## Global Constraints

- Everything in the plan index's "Global constraints" applies.
- Depends on Plan 1 (tokens, primitives, `MediaCard` focus handling added here) and Plan 2 (generated `storyboardApiV1ProjectsProjectIdStoryboardGet`, `waveformApiV1ProjectsProjectIdWaveformGet`, `StoryboardResponse`, `WaveformResponse`, `ClipSummaryResponse.editUpdatedAt`, `order: 'recent'`). If Plan 2's task notes recorded different generated names, use those.
- A poster never shows the text "No preview yet". Fallback order: storyboard tile → Project thumbnail → designed graphite frame.
- Hover scrubbing sends no request per frame and is disabled under `prefers-reduced-motion: reduce`.
- Poster overlays use solid plates (`bg-background/85`) and never a second filled lime; rank is lime text on a graphite plate.
- Signed object-store URLs are fetched with plain `fetch(url, { credentials: 'omit' })` — the same exception `features/uploads/uploader.ts` already makes for signed part uploads. API calls still go through `apiFetch`.
- Owner commit message for this plan: `feat: show media across the studio`.

## File map

| File | Responsibility |
| --- | --- |
| `frontend/lib/media/time.ts` | `formatClock` |
| `frontend/lib/media/storyboard.ts` | Tile lookup, sprite style, poster and scrub times, evenly spaced tiles |
| `frontend/lib/media/waveform.ts` | Peak bucketing |
| `frontend/features/media/use-in-view.ts`, `use-reduced-motion.ts`, `use-storyboard.ts`, `use-waveform.ts` | Visibility, motion preference, manifest and peak reads |
| `frontend/components/media/sprite-frame.tsx` | One storyboard tile, cover-cropped |
| `frontend/components/media/poster.tsx` | Poster with scrub, overlays, and fallbacks; `DesignedFrame` |
| `frontend/components/media/filmstrip.tsx`, `waveform-canvas.tsx`, `stage-bar.tsx` | Filmstrip, waveform bars, pipeline stages |
| `frontend/components/media-card.tsx` | Rebuilt card: focus context, portrait 9:16, hidden-title option |
| `frontend/components/url-tabs.tsx` | Tabs whose choice lives in `?tab=` |
| `frontend/features/workspaces/workspace-overview.tsx` | Home |
| `frontend/features/projects/projects-panel.tsx`, `frontend/features/clips/ClipBrowser.tsx`, `frontend/features/clips/ClipDetail.tsx`, `frontend/features/jobs/job-center.tsx`, `frontend/features/uploads/UploadPanel.tsx`, `frontend/features/projects/project-detail.tsx`, `frontend/features/projects/new-project.tsx` | Screens adopting the media components |

---

### Task 1: Media geometry

**Files:**
- Create: `frontend/lib/media/time.ts`, `frontend/lib/media/storyboard.ts`, `frontend/lib/media/waveform.ts`
- Modify: `frontend/features/clips/ClipCard.tsx` (`formatDuration` delegates to `formatClock`)
- Create: `frontend/tests/media-geometry.test.ts`

**Interfaces:**
- Produces:
  - `formatClock(ms: number): string` → `m:ss`.
  - `interface StoryboardTile { url: string; column: number; row: number; columns: number; rows: number; tileWidth: number; tileHeight: number }`.
  - `tileAt(storyboard: StoryboardResponse, ms: number): StoryboardTile | null`.
  - `spriteStyle(tile: StoryboardTile): { backgroundImage: string; backgroundSize: string; backgroundPosition: string }`.
  - `posterTimeMs(startMs: number, endMs: number): number`, `scrubTimeMs(startMs: number, endMs: number, fraction: number): number`.
  - `tilesAcross(storyboard: StoryboardResponse, startMs: number, endMs: number, count: number): StoryboardTile[]`.
  - `peakBuckets(peaks: Uint8Array, peaksPerSecond: number, startMs: number, endMs: number, buckets: number): number[]` (values 0–1).

- [ ] **Step 1: Write the failing tests**

`frontend/tests/media-geometry.test.ts`:

```ts
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
    expect(tileAt(storyboard(), 0)).toMatchObject({ url: 'https://media.test/sheet-0.jpg', column: 0, row: 0 })
    expect(tileAt(storyboard(), 23_500)).toMatchObject({ column: 1, row: 1 })
    expect(tileAt(storyboard(), 204_900)).toMatchObject({ url: 'https://media.test/sheet-1.jpg', column: 2, row: 0 })
  })

  test('clamps past the end instead of inventing a frame', () => {
    expect(tileAt(storyboard(), 999_999)).toMatchObject({ url: 'https://media.test/sheet-1.jpg', column: 2, row: 0 })
    expect(tileAt(storyboard(), -5)).toMatchObject({ column: 0, row: 0 })
  })

  test('falls back to the last frame it has when a later sheet is missing', () => {
    const partial = storyboard({ sheets: [storyboard().sheets[0]!] })
    expect(tileAt(partial, 204_900)).toMatchObject({ url: 'https://media.test/sheet-0.jpg', column: 9, row: 9 })
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
    expect(tiles.map((tile) => [tile.column, tile.row])).toEqual([[2, 0], [7, 0], [2, 1], [7, 1]])
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/media-geometry.test.ts`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`frontend/lib/media/time.ts`:

```ts
/** Render milliseconds as `m:ss`, the way a creator reads a clip length. */
export function formatClock(ms: number): string {
  const totalSeconds = Math.max(0, Math.round(ms / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}
```

`frontend/lib/media/storyboard.ts`:

```ts
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
  const position = exact === undefined
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
```

Check the filmstrip expectation: centres at 5 s, 15 s, 25 s, 35 s → frames 2, 7, 12, 17 → (2,0), (7,0), (2,1), (7,1).

`frontend/lib/media/waveform.ts`:

```ts
/**
 * The loudest peak in each of `buckets` equal slices of a time range, scaled to 0–1.
 *
 * Peaks are one byte per `1000 / peaksPerSecond` milliseconds, as the backend stores them.
 */
export function peakBuckets(
  peaks: Uint8Array,
  peaksPerSecond: number,
  startMs: number,
  endMs: number,
  buckets: number,
): number[] {
  if (buckets <= 0) return []
  const first = Math.max(0, Math.floor((startMs * peaksPerSecond) / 1000))
  const last = Math.min(peaks.length, Math.ceil((endMs * peaksPerSecond) / 1000))
  if (last <= first) return new Array<number>(buckets).fill(0)
  const span = (last - first) / buckets
  return Array.from({ length: buckets }, (_, bucket) => {
    const from = first + Math.floor(bucket * span)
    const to = Math.max(from + 1, first + Math.floor((bucket + 1) * span))
    let loudest = 0
    for (let position = from; position < to && position < last; position += 1) {
      loudest = Math.max(loudest, peaks[position] ?? 0)
    }
    return Math.round((loudest / 255) * 100) / 100
  })
}
```

In `features/clips/ClipCard.tsx`, replace the body of `formatDuration` with `return formatClock(durationMs)` and import `formatClock` from `@/lib/media/time`.

- [ ] **Step 4: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/media-geometry.test.ts` → PASS.

---

### Task 2: Media hooks

**Files:**
- Create: `frontend/features/media/use-in-view.ts`, `frontend/features/media/use-reduced-motion.ts`, `frontend/features/media/use-storyboard.ts`, `frontend/features/media/use-waveform.ts`
- Create: `frontend/tests/media-hooks.test.tsx`

**Interfaces:**
- Produces:
  - `useInView<T extends Element>(): [RefObject<T | null>, boolean]`.
  - `useReducedMotion(): boolean`.
  - `useStoryboard(projectId: string, options: { enabled: boolean }): UseQueryResult<StoryboardResponse, ApiError>`.
  - `useWaveform(projectId: string, options: { enabled: boolean }): { peaks: Uint8Array | null; peaksPerSecond: number | null; durationMs: number | null; isError: boolean }`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/media-hooks.test.tsx`:

```tsx
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { useStoryboard } from '@/features/media/use-storyboard'
import { useWaveform } from '@/features/media/use-waveform'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { ApiWrapper, stubApi } from './support/api'
import { currentUser, workspace } from './support/fixtures'

const PROJECT_ID = '44444444-4444-4444-8444-444444444444'

function wrapper({ children }: { children: ReactNode }) {
  return (
    <ApiWrapper>
      <WorkspaceProvider>{children}</WorkspaceProvider>
    </ApiWrapper>
  )
}

beforeEach(() => {
  window.sessionStorage.clear()
})

describe('media hooks', () => {
  test('a storyboard is asked for once, for the active workspace, and only when wanted', async () => {
    const api = stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
      [`GET /api/v1/projects/${PROJECT_ID}/storyboard`]: {
        body: { version: 1, intervalMs: 2000, tileWidth: 160, tileHeight: 90, columns: 10, rows: 10, durationMs: 4000, expiresAt: '2026-09-17T00:05:00+00:00', sheets: [] },
      },
    })

    const idle = renderHook(() => useStoryboard(PROJECT_ID, { enabled: false }), { wrapper })
    const wanted = renderHook(() => useStoryboard(PROJECT_ID, { enabled: true }), { wrapper })

    await waitFor(() => expect(wanted.result.current.data?.durationMs).toBe(4000))
    const reads = api.calls.filter((call) => call.path.endsWith('/storyboard'))
    expect(reads).toHaveLength(1)
    expect(reads[0]?.params.get('workspace_id')).toBe(workspace().id)
    expect(idle.result.current.fetchStatus).toBe('idle')
  })

  test('waveform peaks are read from the signed URL without credentials', async () => {
    stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
      [`GET /api/v1/projects/${PROJECT_ID}/waveform`]: {
        body: { version: 1, peaksPerSecond: 20, durationMs: 150, url: 'https://media.test/peaks.bin', expiresAt: '2026-09-17T00:05:00+00:00' },
      },
    })
    const apiFetch = globalThis.fetch
    const media = vi.fn(async () => new Response(new Uint8Array([0, 128, 255])))
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).startsWith('https://media.test/') ? media(input, init) : apiFetch(input, init),
    )

    const { result } = renderHook(() => useWaveform(PROJECT_ID, { enabled: true }), { wrapper })

    await waitFor(() => expect(result.current.peaks).not.toBeNull())
    expect(Array.from(result.current.peaks ?? [])).toEqual([0, 128, 255])
    expect(result.current.peaksPerSecond).toBe(20)
    expect(media).toHaveBeenCalledWith('https://media.test/peaks.bin', expect.objectContaining({ credentials: 'omit' }))
  })

  test('a project with no waveform reports an error instead of silence', async () => {
    stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
    })

    const { result } = renderHook(() => useWaveform(PROJECT_ID, { enabled: true }), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.peaks).toBeNull()
  })
})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/media-hooks.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement the hooks**

`frontend/features/media/use-in-view.ts`:

```ts
'use client'

import { useEffect, useRef, useState, type RefObject } from 'react'

/**
 * Whether an element has been on screen yet. Once seen it stays "seen", so a long grid only
 * signs media for the cards somebody scrolled to, and never asks twice for the same card.
 */
export function useInView<T extends Element>(): [RefObject<T | null>, boolean] {
  const element = useRef<T | null>(null)
  const [seen, setSeen] = useState(false)

  useEffect(() => {
    const target = element.current
    if (target === null || seen) return
    if (typeof IntersectionObserver === 'undefined') {
      setSeen(true)
      return
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setSeen(true)
        observer.disconnect()
      }
    }, { rootMargin: '200px' })
    observer.observe(target)
    return () => observer.disconnect()
  }, [seen])

  return [element, seen]
}
```

`frontend/features/media/use-reduced-motion.ts`:

```ts
'use client'

import { useEffect, useState } from 'react'

/** Whether the member asked their system for less motion. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    setReduced(query.matches)
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])
  return reduced
}
```

`frontend/features/media/use-storyboard.ts`:

```ts
'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { StoryboardResponse } from '@/lib/api/generated/model'
import { storyboardApiV1ProjectsProjectIdStoryboardGet } from '@/lib/api/generated/studio/studio'

/** Signed URLs live five minutes; a manifest is re-signed after four. */
export const SIGNED_MEDIA_STALE_MS = 4 * 60_000

/** One Project's storyboard manifest, asked for only when a picture is about to be drawn. */
export function useStoryboard(
  projectId: string,
  { enabled }: { enabled: boolean },
): UseQueryResult<StoryboardResponse, ApiError> {
  const { active } = useWorkspaceScope()
  return useQuery<StoryboardResponse, ApiError>({
    queryKey: ['/api/v1/projects/storyboard', active.id, projectId],
    queryFn: ({ signal }) =>
      storyboardApiV1ProjectsProjectIdStoryboardGet(projectId, { workspace_id: active.id }, { signal }),
    enabled,
    retry: false,
    staleTime: SIGNED_MEDIA_STALE_MS,
  })
}
```

`frontend/features/media/use-waveform.ts`:

```ts
'use client'

import { useQuery } from '@tanstack/react-query'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { WaveformResponse } from '@/lib/api/generated/model'
import { waveformApiV1ProjectsProjectIdWaveformGet } from '@/lib/api/generated/studio/studio'

import { SIGNED_MEDIA_STALE_MS } from './use-storyboard'

/**
 * One Project's waveform peaks.
 *
 * The manifest is an API read; the bytes come from the signed object URL it names, fetched
 * without credentials. Peaks are immutable per version, so the bytes are read once.
 */
export function useWaveform(projectId: string, { enabled }: { enabled: boolean }) {
  const { active } = useWorkspaceScope()
  const manifest = useQuery<WaveformResponse, ApiError>({
    queryKey: ['/api/v1/projects/waveform', active.id, projectId],
    queryFn: ({ signal }) =>
      waveformApiV1ProjectsProjectIdWaveformGet(projectId, { workspace_id: active.id }, { signal }),
    enabled,
    retry: false,
    staleTime: SIGNED_MEDIA_STALE_MS,
  })
  const url = manifest.data?.url ?? null
  const peaks = useQuery<Uint8Array, Error>({
    queryKey: ['waveform-peaks', active.id, projectId, manifest.data?.version ?? 0],
    queryFn: async ({ signal }) => {
      const response = await fetch(url ?? '', { signal, credentials: 'omit' })
      if (!response.ok) throw new Error(`waveform ${response.status}`)
      return new Uint8Array(await response.arrayBuffer())
    },
    enabled: url !== null,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  })
  return {
    peaks: peaks.data ?? null,
    peaksPerSecond: manifest.data?.peaksPerSecond ?? null,
    durationMs: manifest.data?.durationMs ?? null,
    isError: manifest.isError || peaks.isError,
  }
}
```

- [ ] **Step 4: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/media-hooks.test.tsx` → PASS.

---

### Task 3: `SpriteFrame`, `Poster`, and the rebuilt `MediaCard`

**Files:**
- Create: `frontend/components/media/sprite-frame.tsx`, `frontend/components/media/poster.tsx`, `frontend/components/media/card-focus.ts`
- Modify: `frontend/components/media-card.tsx` (remove `ProjectThumbnail` and `MediaPlaceholder`; add focus context, portrait 9:16, `hideTitle`)
- Modify: `frontend/features/projects/project-detail.tsx` (replace `MediaPlaceholder` with `DesignedFrame`)
- Modify: `frontend/tests/design-rules.test.ts` (remove the `components/media-card.tsx` allowlist entry)
- Create: `frontend/tests/poster.test.tsx`

**Interfaces:**
- Produces:
  - `SpriteFrame({ tile: StoryboardTile; containerAspect: number })`.
  - `Poster(props: { projectId: string; startMs?: number; endMs?: number; aspect?: 'video' | 'portrait'; hasMedia?: boolean; rank?: number; durationMs?: number; hook?: string; className?: string })`.
  - `DesignedFrame({ startMs?: number; endMs?: number; durationMs?: number })`.
  - `MediaCard` props: existing plus `aspect?: 'video' | 'portrait'` (portrait is now 9:16) and `hideTitle?: boolean`; `CardFocusContext` (React context, boolean) in `components/media/card-focus.ts`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/poster.test.tsx`:

```tsx
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { MediaCard } from '@/components/media-card'
import { Poster } from '@/components/media/poster'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi } from './support/api'
import { currentUser, workspace } from './support/fixtures'

const PROJECT_ID = '44444444-4444-4444-8444-444444444444'
const STORYBOARD = `GET /api/v1/projects/${PROJECT_ID}/storyboard`
const THUMBNAIL = `GET /api/v1/projects/${PROJECT_ID}/thumbnail`

const manifest = {
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

function signedIn(extra: Record<string, unknown> = {}) {
  return stubApi({
    'GET /api/v1/me': { body: currentUser() },
    'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
    ...(extra as Record<string, { body?: unknown; status?: number }>),
  })
}

function renderPoster(props: Partial<Parameters<typeof Poster>[0]> = {}) {
  return renderWithApi(
    <WorkspaceProvider>
      <div style={{ width: 200 }}>
        <Poster projectId={PROJECT_ID} startMs={10_000} endMs={40_000} aspect="portrait" {...props} />
      </div>
    </WorkspaceProvider>,
  )
}

beforeEach(() => {
  window.sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Poster', () => {
  test('draws the frame one second into the clip from the storyboard sheet', async () => {
    signedIn({ [STORYBOARD]: { body: manifest } })
    renderPoster()

    const sprite = await screen.findByTestId('sprite')

    expect(sprite.style.backgroundImage).toBe('url("https://media.test/sheet-0.jpg")')
    expect(sprite.style.backgroundPosition).toBe('55.5556% 0%')
  })

  test('scrubs across the clip on hover without asking for anything new', async () => {
    const api = signedIn({ [STORYBOARD]: { body: manifest } })
    renderPoster()
    const sprite = await screen.findByTestId('sprite')
    const poster = screen.getByTestId('poster')
    vi.spyOn(poster, 'getBoundingClientRect').mockReturnValue({ left: 0, width: 200, top: 0, height: 356, right: 200, bottom: 356, x: 0, y: 0, toJSON: () => ({}) })

    fireEvent.pointerMove(poster, { clientX: 200 })

    expect(sprite.style.backgroundPosition).toBe('0% 22.2222%')
    fireEvent.pointerLeave(poster)
    expect(sprite.style.backgroundPosition).toBe('55.5556% 0%')
    expect(api.calls.filter((call) => call.path.endsWith('/storyboard'))).toHaveLength(1)
  })

  test('holds still for a member who asked for reduced motion', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined }))
    signedIn({ [STORYBOARD]: { body: manifest } })
    renderPoster()
    const sprite = await screen.findByTestId('sprite')
    const poster = screen.getByTestId('poster')
    vi.spyOn(poster, 'getBoundingClientRect').mockReturnValue({ left: 0, width: 200, top: 0, height: 356, right: 200, bottom: 356, x: 0, y: 0, toJSON: () => ({}) })

    fireEvent.pointerMove(poster, { clientX: 200 })

    expect(sprite.style.backgroundPosition).toBe('55.5556% 0%')
  })

  test('without a storyboard it shows the project thumbnail', async () => {
    signedIn({ [THUMBNAIL]: { body: { url: 'https://media.test/thumb.jpg', expiresAt: '2026-09-17T00:05:00+00:00', contentType: 'image/jpeg' } } })
    const { container } = renderPoster()

    await waitFor(() => expect(container.querySelector('img')).toHaveAttribute('src', 'https://media.test/thumb.jpg'))
  })

  test('with no media at all it draws a designed frame with the clip range, never an apology', async () => {
    signedIn()
    renderPoster()

    expect(await screen.findByText('0:10 – 0:40')).toBeInTheDocument()
    expect(screen.queryByText(/no preview/i)).not.toBeInTheDocument()
  })

  test('a project still waiting for media asks for nothing', () => {
    const api = signedIn()
    renderPoster({ hasMedia: false })

    expect(api.calls.filter((call) => call.path.includes(PROJECT_ID))).toHaveLength(0)
  })

  test('keyboard focus on its card shows the middle of the clip', async () => {
    signedIn({ [STORYBOARD]: { body: manifest } })
    renderWithApi(
      <WorkspaceProvider>
        <MediaCard
          href="/dashboard/clips/one"
          title="The surprising opening"
          aspect="portrait"
          thumbnail={<Poster projectId={PROJECT_ID} startMs={10_000} endMs={40_000} aspect="portrait" />}
        />
      </WorkspaceProvider>,
    )
    const sprite = await screen.findByTestId('sprite')

    screen.getByRole('link', { name: 'The surprising opening' }).focus()

    await waitFor(() => expect(sprite.style.backgroundPosition).toBe('22.2222% 11.1111%'))
  })

  test('overlays rank, length, and hook as decoration the card already names', async () => {
    signedIn({ [STORYBOARD]: { body: manifest } })
    renderPoster({ rank: 1, durationMs: 30_000, hook: 'The surprising opening' })

    const poster = await screen.findByTestId('poster')
    expect(poster).toHaveAttribute('aria-hidden', 'true')
    expect(poster).toHaveTextContent('#1')
    expect(poster).toHaveTextContent('0:30')
    expect(poster).toHaveTextContent('The surprising opening')
  })
})
```

Expected positions, derived from the geometry: the poster opens at 11 s → frame 5 → column 5, row 0 (`55.5556% 0%`); a pointer at the right edge scrubs to 40 s → frame 20 → column 0, row 2 (`0% 22.2222%`); focus shows the middle, 25 s → frame 12 → column 2, row 1 (`22.2222% 11.1111%`).

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/poster.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement `SpriteFrame`**

`frontend/components/media/sprite-frame.tsx`:

```tsx
import { spriteStyle, type StoryboardTile } from '@/lib/media/storyboard'

/**
 * One storyboard tile, cover-cropped into its box.
 *
 * The inner element keeps the tile's own shape and is centred over a box of any shape, so a
 * landscape frame fills a 9:16 poster by cropping its sides rather than stretching.
 */
export function SpriteFrame({ tile, containerAspect }: { tile: StoryboardTile; containerAspect: number }) {
  const tileAspect = tile.tileWidth / tile.tileHeight
  const fillHeight = tileAspect > containerAspect
  return (
    <div className="absolute inset-0 overflow-hidden bg-stage">
      <div
        data-testid="sprite"
        className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-no-repeat"
        style={{
          ...(fillHeight ? { height: '100%' } : { width: '100%' }),
          aspectRatio: `${tile.tileWidth} / ${tile.tileHeight}`,
          ...spriteStyle(tile),
        }}
      />
    </div>
  )
}
```

- [ ] **Step 4: Implement `Poster` and `DesignedFrame`**

`frontend/components/media/poster.tsx`:

```tsx
'use client'

import { useQuery } from '@tanstack/react-query'
import { Film } from 'lucide-react'
import { useContext, useState, type PointerEvent } from 'react'

import { CardFocusContext } from '@/components/media/card-focus'
import { useInView } from '@/features/media/use-in-view'
import { useReducedMotion } from '@/features/media/use-reduced-motion'
import { SIGNED_MEDIA_STALE_MS, useStoryboard } from '@/features/media/use-storyboard'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { MediaPreviewResponse } from '@/lib/api/generated/model'
import { thumbnailApiV1ProjectsProjectIdThumbnailGet } from '@/lib/api/generated/studio/studio'
import { posterTimeMs, scrubTimeMs, tileAt } from '@/lib/media/storyboard'
import { formatClock } from '@/lib/media/time'
import { cn } from '@/lib/utils'

import { SpriteFrame } from './sprite-frame'

const ASPECTS = { video: 16 / 9, portrait: 9 / 16 } as const

/**
 * A picture of a Project or one of its clips.
 *
 * It draws a storyboard tile, scrubs across the clip under the pointer, and shows the middle
 * of the clip while its card has keyboard focus. Without a storyboard it falls back to the
 * Project thumbnail, and without that to a designed frame. Everything on it is decoration:
 * the card around it carries the name and the link.
 */
export function Poster({
  projectId,
  startMs,
  endMs,
  aspect = 'video',
  hasMedia = true,
  rank,
  durationMs,
  hook,
  className,
}: {
  projectId: string
  startMs?: number
  endMs?: number
  aspect?: 'video' | 'portrait'
  hasMedia?: boolean
  rank?: number
  durationMs?: number
  hook?: string
  className?: string
}) {
  const { active } = useWorkspaceScope()
  const [frame, visible] = useInView<HTMLDivElement>()
  const reducedMotion = useReducedMotion()
  const cardFocused = useContext(CardFocusContext)
  const [scrubMs, setScrubMs] = useState<number | null>(null)
  const [brokenThumbnail, setBrokenThumbnail] = useState(false)

  const storyboard = useStoryboard(projectId, { enabled: hasMedia && visible })
  const thumbnail = useQuery<MediaPreviewResponse, ApiError>({
    queryKey: ['/api/v1/projects/thumbnail', active.id, projectId],
    queryFn: ({ signal }) =>
      thumbnailApiV1ProjectsProjectIdThumbnailGet(projectId, { workspace_id: active.id }, { signal }),
    enabled: hasMedia && visible && storyboard.isError,
    retry: false,
    staleTime: SIGNED_MEDIA_STALE_MS,
  })

  const manifest = storyboard.data ?? null
  const range =
    manifest === null ? null : { start: startMs ?? 0, end: endMs ?? manifest.durationMs }
  const shownMs =
    range === null
      ? null
      : scrubMs ?? (cardFocused ? scrubTimeMs(range.start, range.end, 0.5) : posterTimeMs(range.start, range.end))
  const tile = manifest === null || shownMs === null ? null : tileAt(manifest, shownMs)
  const length = durationMs ?? (startMs === undefined && manifest !== null ? manifest.durationMs : undefined)

  function scrub(event: PointerEvent<HTMLDivElement>): void {
    if (range === null || reducedMotion) return
    const bounds = event.currentTarget.getBoundingClientRect()
    if (bounds.width <= 0) return
    setScrubMs(scrubTimeMs(range.start, range.end, (event.clientX - bounds.left) / bounds.width))
  }

  return (
    <div
      ref={frame}
      data-testid="poster"
      aria-hidden="true"
      onPointerMove={scrub}
      onPointerLeave={() => setScrubMs(null)}
      className={cn('absolute inset-0 overflow-hidden bg-stage', className)}
    >
      {tile !== null ? (
        <SpriteFrame tile={tile} containerAspect={ASPECTS[aspect]} />
      ) : thumbnail.data !== undefined && !brokenThumbnail ? (
        // Signed object-store URLs are not known to the Next image optimizer.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={thumbnail.data.url}
          alt=""
          loading="lazy"
          onError={() => setBrokenThumbnail(true)}
          className="absolute inset-0 size-full object-cover"
        />
      ) : (
        <DesignedFrame startMs={startMs} endMs={endMs} durationMs={length} />
      )}
      {rank === undefined ? null : (
        <span className="absolute left-2 top-2 rounded-sm bg-background/85 px-1.5 py-0.5 font-mono text-caption text-primary">
          #{rank}
        </span>
      )}
      {length === undefined ? null : (
        <span className="absolute right-2 top-2 rounded-sm bg-background/85 px-1.5 py-0.5 font-mono text-caption text-foreground tabular">
          {formatClock(length)}
        </span>
      )}
      {hook === undefined ? null : (
        <p className="font-display absolute inset-x-0 bottom-0 line-clamp-3 bg-background/60 px-3 py-2 text-title leading-tight text-foreground">
          {hook}
        </p>
      )}
    </div>
  )
}

/** The intentional stand-in for media that has not been produced: graphite and a timecode. */
export function DesignedFrame({
  startMs,
  endMs,
  durationMs,
}: {
  startMs?: number
  endMs?: number
  durationMs?: number
}) {
  const label =
    startMs !== undefined && endMs !== undefined
      ? `${formatClock(startMs)} – ${formatClock(endMs)}`
      : durationMs !== undefined
        ? formatClock(durationMs)
        : null
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-stage">
      <Film aria-hidden="true" strokeWidth={1.75} className="size-5 text-subtle-foreground" />
      {label === null ? null : <span className="font-mono text-caption text-muted-foreground">{label}</span>}
      <span className="absolute inset-x-6 bottom-4 h-px bg-line-strong" />
    </div>
  )
}
```

When `durationMs` is passed with a clip range, the top-right badge shows it; the designed frame shows the range; both are allowed.

- [ ] **Step 5: Add the focus context and rebuild `frontend/components/media-card.tsx`**

`frontend/components/media/card-focus.ts` (its own module, so `Poster` and `MediaCard` never import each other):

```ts
import { createContext } from 'react'

/** Whether keyboard focus is inside a media card, so its poster can show the middle frame. */
export const CardFocusContext = createContext(false)
```

`frontend/components/media-card.tsx`:

```tsx
'use client'

import Link from 'next/link'
import { useState, type ReactNode } from 'react'

import { CardFocusContext } from '@/components/media/card-focus'
import { cn } from '@/lib/utils'

/**
 * One piece of media in a grid: a picture, a name that opens it, and its state.
 *
 * The whole card is the link through the title's stretched hit area; contextual actions sit
 * in `menu`, above the link, so opening a menu never navigates.
 */
export function MediaCard({
  href,
  title,
  thumbnail,
  subtitle,
  status,
  menu,
  footer,
  aspect = 'video',
  hideTitle = false,
}: {
  href: string
  title: string
  thumbnail: ReactNode
  subtitle?: ReactNode
  status?: ReactNode
  menu?: ReactNode
  footer?: ReactNode
  aspect?: 'video' | 'portrait'
  hideTitle?: boolean
}) {
  const [focused, setFocused] = useState(false)
  return (
    <article
      onFocus={() => setFocused(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false)
      }}
      className="group relative flex flex-col overflow-hidden rounded-lg border bg-card transition-colors duration-fast ease-signal hover:border-line-strong focus-within:border-line-strong"
    >
      <CardFocusContext.Provider value={focused}>
        <div className={cn('relative overflow-hidden bg-stage', aspect === 'video' ? 'aspect-video' : 'aspect-[9/16]')}>
          {thumbnail}
          {status === undefined ? null : <div className="absolute bottom-2 left-2">{status}</div>}
        </div>
      </CardFocusContext.Provider>
      <div className="flex flex-1 items-start gap-2 p-3">
        <div className="min-w-0 flex-1 space-y-0.5">
          <h3 className={cn('truncate text-small font-semibold', hideTitle && 'sr-only')}>
            <Link href={href} className="after:absolute after:inset-0 after:content-['']">
              {title}
            </Link>
          </h3>
          {subtitle === undefined ? null : (
            <div className="truncate text-caption text-muted-foreground">{subtitle}</div>
          )}
          {footer === undefined ? null : <div className="relative z-10 pt-1.5">{footer}</div>}
        </div>
        {menu === undefined ? null : <div className="relative z-10 shrink-0">{menu}</div>}
      </div>
    </article>
  )
}
```

Poster sets `absolute inset-0`, so its parent (the card's picture box) must be `relative` — it is.

- [ ] **Step 6: Replace the remaining placeholder use and the allowlist entry**

In `features/projects/project-detail.tsx`, replace `import { MediaPlaceholder } from '@/components/media-card'` with `import { DesignedFrame } from '@/components/media/poster'` and `<MediaPlaceholder label="The preview appears once the video is prepared" />` with:

```tsx
<div className="relative size-full">
  <DesignedFrame />
</div>
```

Remove `'components/media-card.tsx'` from `PENDING_REDESIGN` in `tests/design-rules.test.ts`. `ProjectThumbnail` is still imported by Home, Projects, and Clips until Tasks 6–8; temporarily re-export a compatibility wrapper at the bottom of `media-card.tsx` so the suite compiles:

```tsx
/** Temporary bridge for screens not yet on `Poster`; removed in Task 8 of this plan. */
export { Poster as ProjectThumbnail } from '@/components/media/poster'
```

and at each call site `workspaceId` becomes unused — leave those call sites for Tasks 6–8 and add `// eslint-disable-next-line @typescript-eslint/no-unused-vars` only if lint fails on an unused prop. `Poster` ignores unknown props only if typed to accept them, so instead change each temporary call site in the same step from `<ProjectThumbnail workspaceId={…} projectId={…} … />` to `<ProjectThumbnail projectId={…} … />`.

- [ ] **Step 7: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/poster.test.tsx tests/design-rules.test.ts` → PASS.
Run: `pnpm typecheck && pnpm test` → PASS.

---

### Task 4: `Filmstrip` and `WaveformCanvas`

**Files:**
- Create: `frontend/components/media/filmstrip.tsx`, `frontend/components/media/waveform-canvas.tsx`
- Create: `frontend/tests/filmstrip-waveform.test.tsx`

**Interfaces:**
- Produces:
  - `Filmstrip({ storyboard: StoryboardResponse | null; startMs: number; endMs: number; tileCount: number; className?: string })`.
  - `WaveformCanvas({ peaks: Uint8Array | null; peaksPerSecond: number | null; startMs: number; endMs: number; selection?: readonly [number, number] | null; className?: string })`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/filmstrip-waveform.test.tsx`:

```tsx
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
    const { container } = render(<Filmstrip storyboard={null} startMs={0} endMs={40_000} tileCount={4} />)

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
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
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
```

Check the filmstrip expectation: four centres over 0–40 s → the fourth at 35 s → frame 17 → column 7, row 1 → `77.7778% 11.1111%`.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/filmstrip-waveform.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`frontend/components/media/filmstrip.tsx`:

```tsx
import type { StoryboardResponse } from '@/lib/api/generated/model'
import { tilesAcross } from '@/lib/media/storyboard'
import { cn } from '@/lib/utils'

import { SpriteFrame } from './sprite-frame'

/** Frames of a range laid side by side, the way a timeline shows what is on each lane. */
export function Filmstrip({
  storyboard,
  startMs,
  endMs,
  tileCount,
  className,
}: {
  storyboard: StoryboardResponse | null
  startMs: number
  endMs: number
  tileCount: number
  className?: string
}) {
  const tiles = storyboard === null || tileCount <= 0 ? [] : tilesAcross(storyboard, startMs, endMs, tileCount)
  return (
    <div aria-hidden="true" className={cn('flex overflow-hidden bg-stage', className)}>
      {tiles.map((tile, index) => (
        <div key={index} className="relative h-full min-w-0 flex-1 border-r border-background/60 last:border-r-0">
          <SpriteFrame tile={tile} containerAspect={tile.tileWidth / tile.tileHeight} />
        </div>
      ))}
    </div>
  )
}
```

`frontend/components/media/waveform-canvas.tsx`:

```tsx
'use client'

import { useEffect, useRef, useState } from 'react'

import { peakBuckets } from '@/lib/media/waveform'
import { cn } from '@/lib/utils'

const BAR_WIDTH = 2
const BAR_GAP = 1

/** Loudness bars for a time range, lime inside the selected range and muted elsewhere. */
export function WaveformCanvas({
  peaks,
  peaksPerSecond,
  startMs,
  endMs,
  selection = null,
  className,
}: {
  peaks: Uint8Array | null
  peaksPerSecond: number | null
  startMs: number
  endMs: number
  selection?: readonly [number, number] | null
  className?: string
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  useEffect(() => {
    const element = canvas.current
    if (element === null) return
    const measure = () => setSize({ width: element.clientWidth, height: element.clientHeight })
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const element = canvas.current
    if (element === null || peaks === null || peaksPerSecond === null || size.width === 0) return
    const context = element.getContext('2d')
    if (context === null) return
    const ratio = window.devicePixelRatio || 1
    element.width = Math.round(size.width * ratio)
    element.height = Math.round(size.height * ratio)
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    context.clearRect(0, 0, size.width, size.height)
    const styles = getComputedStyle(element)
    const idle = `rgb(${styles.getPropertyValue('--muted-foreground').trim() || '161 161 168'})`
    const chosen = `rgb(${styles.getPropertyValue('--primary').trim() || '198 255 61'})`
    const buckets = Math.floor(size.width / (BAR_WIDTH + BAR_GAP))
    peakBuckets(peaks, peaksPerSecond, startMs, endMs, buckets).forEach((value, index) => {
      const atMs = startMs + ((index + 0.5) / buckets) * (endMs - startMs)
      const barHeight = Math.max(1, value * size.height)
      context.fillStyle = selection !== null && atMs >= selection[0] && atMs < selection[1] ? chosen : idle
      context.fillRect(index * (BAR_WIDTH + BAR_GAP), (size.height - barHeight) / 2, BAR_WIDTH, barHeight)
    })
  }, [peaks, peaksPerSecond, startMs, endMs, selection, size])

  return <canvas ref={canvas} aria-hidden="true" className={cn('block h-full w-full', className)} />
}
```

- [ ] **Step 4: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/filmstrip-waveform.test.tsx` → PASS.

---

### Task 5: `StageBar` and the upload panel

**Files:**
- Create: `frontend/components/media/stage-bar.tsx`
- Modify: `frontend/features/uploads/UploadPanel.tsx` (replace `PipelineSteps` and the reconnect line)
- Create: `frontend/tests/stage-bar.test.tsx`
- Modify: `frontend/tests/uploads.test.tsx` (stage names)

**Interfaces:**
- Produces: `PIPELINE_KINDS: ReadonlySet<string>`; `pipelineStageIndex(kind: string | null): number` (0 uploading, 1 importing, 2 transcribing, 3 finding moments, −1 other); `StageBar({ kind: string | null; status: string | null; uploadPercent?: number | null; reconnecting?: boolean; actions?: ReactNode; className?: string })`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/stage-bar.test.tsx`:

```tsx
import { render, screen, within } from '@testing-library/react'
import { describe, expect, test } from 'vitest'

import { StageBar, pipelineStageIndex } from '@/components/media/stage-bar'

function stages() {
  return within(screen.getByRole('list', { name: 'Processing stages' })).getAllByRole('listitem')
}

describe('StageBar', () => {
  test('names the four real stages and marks the one in progress', () => {
    render(<StageBar kind="transcribe" status="running" />)

    expect(stages().map((stage) => stage.textContent)).toEqual([
      'Uploading (done)',
      'Importing (done)',
      'Transcribing (in progress)',
      'Finding moments (not started)',
    ])
  })

  test('shows the upload percentage only while the browser is uploading', () => {
    render(<StageBar kind={null} status={null} uploadPercent={42} />)

    expect(stages()[0]).toHaveTextContent('Uploading 42% (in progress)')
  })

  test('a finished analysis completes every stage', () => {
    render(<StageBar kind="analyze" status="succeeded" />)

    expect(stages().every((stage) => stage.textContent?.endsWith('(done)'))).toBe(true)
  })

  test('says when live updates are reconnecting and hosts recovery actions', () => {
    render(<StageBar kind="ingest" status="running" reconnecting actions={<button type="button">Stop</button>} />)

    expect(screen.getByText('Reconnecting to live updates…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
  })

  test('maps job kinds onto stages and leaves other work off the bar', () => {
    expect(pipelineStageIndex('source_import')).toBe(1)
    expect(pipelineStageIndex('ingest')).toBe(1)
    expect(pipelineStageIndex('transcribe')).toBe(2)
    expect(pipelineStageIndex('analyze')).toBe(3)
    expect(pipelineStageIndex('preview_media')).toBe(-1)
    expect(pipelineStageIndex(null)).toBe(-1)
  })
})
```

In `frontend/tests/uploads.test.tsx`, update assertions that name stage pills (for example `transcribe \(in progress\)`) to the new names (`Transcribing (in progress)`), and keep the status paragraph assertions (`Importing media`, `Transcribing audio`, `Finding moments`, `Ready to review`) unchanged.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/stage-bar.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `frontend/components/media/stage-bar.tsx`**

```tsx
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

const STAGES = ['Uploading', 'Importing', 'Transcribing', 'Finding moments'] as const

const KIND_STAGE: Record<string, number> = {
  source_import: 1,
  ingest: 1,
  transcribe: 2,
  analyze: 3,
}

/** Job kinds that are stages of the pipeline a member waits on. */
export const PIPELINE_KINDS: ReadonlySet<string> = new Set(Object.keys(KIND_STAGE))

/** Which stage one job kind is, or −1 for work that is not a pipeline stage. */
export function pipelineStageIndex(kind: string | null): number {
  return kind === null ? -1 : KIND_STAGE[kind] ?? -1
}

/**
 * The pipeline a video passes through, marked only from what was reported.
 *
 * The only number is the upload percentage the browser measures itself; a backend stage is
 * done, in progress, or not started, never a guessed fraction.
 */
export function StageBar({
  kind,
  status,
  uploadPercent = null,
  reconnecting = false,
  actions,
  className,
}: {
  kind: string | null
  status: string | null
  uploadPercent?: number | null
  reconnecting?: boolean
  actions?: ReactNode
  className?: string
}) {
  const reported = pipelineStageIndex(kind)
  const current = uploadPercent !== null ? 0 : reported
  const finished = status === 'succeeded' && reported === STAGES.length - 1

  return (
    <div className={cn('space-y-2', className)}>
      <ol aria-label="Processing stages" className="grid grid-cols-4 gap-1.5">
        {STAGES.map((label, index) => {
          const done = finished || index < current
          const active = !finished && index === current
          const state = done ? ' (done)' : active ? ' (in progress)' : ' (not started)'
          return (
            <li key={label} className="min-w-0 space-y-1.5">
              <span
                aria-hidden="true"
                className={cn(
                  'block h-1 rounded-full',
                  done ? 'bg-primary' : active ? 'bg-foreground motion-safe:animate-pulse' : 'bg-line-strong',
                )}
              />
              <span className={cn('block truncate text-caption', active ? 'text-foreground' : 'text-muted-foreground')}>
                {label}
                {index === 0 && active && uploadPercent !== null ? (
                  <span className="font-mono tabular"> {uploadPercent}%</span>
                ) : null}
                <span className="sr-only">{state}</span>
              </span>
            </li>
          )
        })}
      </ol>
      {reconnecting || actions !== undefined ? (
        <div className="flex flex-wrap items-center gap-3">
          {reconnecting ? <p className="text-caption text-warning">Reconnecting to live updates…</p> : null}
          {actions}
        </div>
      ) : null}
    </div>
  )
}
```

The first test expects `'Uploading (done)'`: with `kind="transcribe"` and no upload, `current` is 2, so stages 0 and 1 are done. `textContent` joins the visible label and the screen-reader suffix without a separator, so the suffix starts with a space.

- [ ] **Step 4: Adopt it in `features/uploads/UploadPanel.tsx`**

Delete `PIPELINE` and `PipelineSteps`. In the status box replace `<PipelineSteps job={job} uploading={uploading !== null} />` and the `connected ? null : <p …>Reconnecting…</p>` block with:

```tsx
<StageBar
  kind={job?.kind ?? null}
  status={job?.status ?? null}
  uploadPercent={uploading === null ? null : percent(uploading)}
  reconnecting={!connected}
/>
```

Remove the `role="progressbar"` bar only if no test asserts it: run `grep -n "progressbar" frontend/tests/uploads.test.tsx frontend/tests/projects.test.tsx`; keep it when a test depends on it, restyled to `h-1 rounded-full bg-secondary` with a `bg-primary` fill.

- [ ] **Step 5: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/stage-bar.test.tsx tests/uploads.test.tsx` → PASS.

---

### Task 6: Home

**Files:**
- Modify: `frontend/features/workspaces/workspace-overview.tsx` (full rewrite)
- Modify: `frontend/features/projects/new-project.tsx` (`NewProjectButton` gains `variant` and `label`)
- Modify: `frontend/tests/dashboard.test.tsx`

**Interfaces:**
- Consumes: `browseClipCollectionApiV1ClipsGet` with `order: 'recent'`, `Poster`, `MediaCard`, `StageBar`, `PIPELINE_KINDS`, `NewProjectButton`.
- Produces: `NewProjectButton` props `variant?: ButtonProps['variant']`, `label?: string`.

- [ ] **Step 1: Rewrite the Home tests**

Replace the `describe('the Workspace overview', …)` block in `frontend/tests/dashboard.test.tsx` with (keep the file's `summary()` helper and `describe('the public demo', …)`):

```tsx
const CLIPS = 'GET /api/v1/clips'

function edited(overrides: Record<string, unknown> = {}) {
  return {
    id: '55555555-5555-4555-8555-555555555551',
    projectId: '44444444-4444-4444-8444-444444444444',
    projectName: 'Episode 12',
    rank: 1,
    score: 0.91,
    hook: 'The surprising opening',
    reason: 'A complete and useful moment',
    category: 'insight',
    startMs: 1000,
    endMs: 31000,
    durationMs: 30000,
    stage: 'edited',
    editId: '66666666-6666-4666-8666-666666666661',
    currentRevision: 3,
    exportCount: 0,
    createdAt: '2026-02-01T00:00:00+00:00',
    editUpdatedAt: '2026-02-03T09:30:00+00:00',
    ...overrides,
  }
}

function signedIn(summaryBody = summary(), clips: unknown[] = []) {
  return stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [SUMMARY]: { body: summaryBody },
    [CLIPS]: { body: { clips, nextCursor: null } },
  })
}

function renderHome() {
  renderWithApi(
    <WorkspaceProvider>
      <WorkspaceOverview />
    </WorkspaceProvider>,
  )
}

describe('Home', () => {
  test('leads with the clip edited most recently and one way back into it', async () => {
    const api = signedIn(summary(), [edited(), edited({ id: '55555555-5555-4555-8555-555555555552', hook: 'Second cut', editId: '66666666-6666-4666-8666-666666666662' })])
    renderHome()

    const hero = await screen.findByRole('region', { name: 'Continue editing' })
    expect(within(hero).getByText('The surprising opening')).toBeInTheDocument()
    expect(within(hero).getByText(/revision 3/i)).toBeInTheDocument()
    expect(within(hero).getByRole('link', { name: 'Continue editing' })).toHaveAttribute('href', '/editor/66666666-6666-4666-8666-666666666661')
    expect(within(screen.getByRole('list', { name: 'More clips in editing' })).getByRole('link', { name: 'Second cut' })).toHaveAttribute('href', '/editor/66666666-6666-4666-8666-666666666662')
    const read = api.calls.find((call) => call.path === '/api/v1/clips')
    expect(read?.params.get('stage')).toBe('edited')
    expect(read?.params.get('order')).toBe('recent')
  })

  test('shows what is processing on the real stage bar', async () => {
    signedIn(
      summary({
        jobs: {
          active: [
            { id: 'job-1', projectId: '44444444-4444-4444-8444-444444444444', kind: 'transcribe', status: 'running', stage: 'transcribing', progress: 0, updatedAt: '2026-02-02T00:00:00+00:00' },
            { id: 'job-2', projectId: '44444444-4444-4444-8444-444444444444', kind: 'preview_media', status: 'running', stage: 'preview_media', progress: 0, updatedAt: '2026-02-02T00:00:01+00:00' },
          ],
        },
      }),
    )
    renderHome()

    const processing = await screen.findByRole('list', { name: 'Processing now' })
    // One row: preview work is not a pipeline stage. Each row's StageBar holds its own list.
    expect(processing.children).toHaveLength(1)
    expect(within(processing).getByText('Episode 12')).toBeInTheDocument()
    expect(within(processing).getByText(/transcribing \(in progress\)/i)).toBeInTheDocument()
  })

  test('offers the strongest moments as a reel of posters', async () => {
    signedIn()
    renderHome()

    const reel = await screen.findByRole('list', { name: 'Ready to review' })
    expect(within(reel).getByRole('link', { name: 'The surprising opening' })).toHaveAttribute('href', '/dashboard/clips/99999999-9999-4999-8999-999999999999')
  })

  test('lists recent projects with their state', async () => {
    signedIn()
    renderHome()

    const recent = await screen.findByRole('list', { name: /recent projects/i })
    expect(within(recent).getByRole('link', { name: 'Episode 12' })).toHaveAttribute('href', '/dashboard/projects/44444444-4444-4444-8444-444444444444')
    expect(within(recent).getByText('Ready to review')).toBeInTheDocument()
  })

  test('an empty workspace is the importer itself', async () => {
    signedIn(summary({ projects: { activeCount: 0, recent: [] }, topCandidates: [] }))
    renderHome()

    expect(await screen.findByRole('heading', { name: 'Drop a long video to start' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Choose a video' })).toBeInTheDocument()
    expect(screen.queryByText(/nothing is waiting for review/i)).not.toBeInTheDocument()
  })

  test('leaves detailed budget numbers to Settings', async () => {
    signedIn()
    renderHome()

    await screen.findByRole('list', { name: /recent projects/i })
    expect(screen.queryByText('4 of 30')).not.toBeInTheDocument()
  })

  test('asks the backend only for the Workspace the member is looking at', async () => {
    const api = signedIn()
    renderHome()

    await screen.findByRole('list', { name: /recent projects/i })
    const reads = api.calls.filter((call) => call.path === '/api/v1/dashboard/summary')
    expect(reads).toHaveLength(1)
    expect(reads[0]?.params.get('workspace_id')).toBe(workspace().id)
  })

  test('shows the failure instead of an empty Workspace when the summary cannot be read', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [SUMMARY]: { status: 500 },
    })
    renderHome()

    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong. Please try again.')
  })
})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/dashboard.test.tsx`
Expected: FAIL — no "Continue editing" region, no reel list, old first-run copy.

- [ ] **Step 3: Extend `NewProjectButton`**

In `features/projects/new-project.tsx`, add `variant?: ButtonProps['variant']` and `label?: string` (default `'New project'`) to the button branch's props, pass `variant={variant}` to `Button`, and render `{label}` in place of the literal text. Import `type ButtonProps` from `@/components/ui/button`.

- [ ] **Step 4: Rewrite `features/workspaces/workspace-overview.tsx`**

```tsx
'use client'

import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Clapperboard } from 'lucide-react'
import Link from 'next/link'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { MediaCard } from '@/components/media-card'
import { Poster } from '@/components/media/poster'
import { PIPELINE_KINDS, StageBar } from '@/components/media/stage-bar'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { NewProjectButton } from '@/features/projects/new-project'
import { projectStatusLabel, projectStatusTone } from '@/features/projects/status-labels'
import type { ApiError } from '@/lib/api/client'
import { showApiV1DashboardSummaryGet } from '@/lib/api/generated/dashboard/dashboard'
import type {
  ClipPageResponse,
  ClipSummaryResponse,
  DashboardCandidateResponse,
  DashboardJobResponse,
  DashboardProjectResponse,
  DashboardSummaryResponse,
} from '@/lib/api/generated/model'
import { browseClipCollectionApiV1ClipsGet } from '@/lib/api/generated/studio/studio'

import { useWorkspaceScope } from './workspace-context'

/**
 * Home: the work to pick up, the work in motion, and the moments waiting for a decision.
 *
 * An empty Workspace is the importer itself. Otherwise the clip edited most recently leads,
 * processing shows the real pipeline stages, the strongest suggestions sit in a reel of
 * posters, and recent Projects follow.
 */
export function WorkspaceOverview() {
  const { active } = useWorkspaceScope()
  const summary = useQuery<DashboardSummaryResponse, ApiError>({
    queryKey: ['/api/v1/dashboard/summary', active.id],
    queryFn: ({ signal }) => showApiV1DashboardSummaryGet({ workspace_id: active.id }, { signal }),
    retry: false,
  })
  const editing = useQuery<ClipPageResponse, ApiError>({
    queryKey: ['/api/v1/clips', active.id, 'edited', 'recent'],
    queryFn: ({ signal }) =>
      browseClipCollectionApiV1ClipsGet(
        { workspace_id: active.id, stage: 'edited', order: 'recent', limit: 5 },
        { signal },
      ),
    retry: false,
  })

  if (summary.isPending) {
    return (
      <div className="space-y-6">
        <PageHeader title="Home" />
        <LoadingState label="Loading your workspace…" variant="cards" />
      </div>
    )
  }
  if (summary.isError) {
    return (
      <div className="space-y-6">
        <PageHeader title="Home" />
        <ErrorNotice error={summary.error} onRetry={() => void summary.refetch()} />
      </div>
    )
  }

  const { projects, topCandidates, jobs } = summary.data
  const clips = (editing.data?.clips ?? []).filter((clip) => clip.editId !== null)
  const lead = clips[0] ?? null
  const firstRun = projects.activeCount === 0
  const names = new Map(projects.recent.map((project) => [project.id, project.name]))
  const processing = latestPipelineJobs(jobs.active)

  return (
    <div className="space-y-10">
      <PageHeader
        title={summary.data.workspace.name}
        actions={<NewProjectButton size="lg" variant={lead === null && !firstRun ? 'default' : 'secondary'} />}
      />
      {firstRun ? <FirstRun /> : null}
      {lead === null ? null : <ContinueEditing lead={lead} others={clips.slice(1)} />}
      {processing.length === 0 ? null : <ProcessingNow jobs={processing} names={names} />}
      {topCandidates.length > 0 ? (
        <ReadyToReview candidates={topCandidates} />
      ) : firstRun ? null : (
        <EmptyState
          compact
          icon={Clapperboard}
          title="Nothing is waiting for review"
          description="Suggested moments appear here once a video has been processed."
        />
      )}
      {projects.recent.length === 0 ? null : <RecentProjects projects={projects.recent} />}
    </div>
  )
}

function FirstRun() {
  return (
    <section
      aria-labelledby="first-run-title"
      className="rounded-lg border-2 border-dashed border-line-strong bg-card/40 px-6 py-12 sm:px-10"
    >
      <h2 id="first-run-title" className="font-display text-h1 sm:text-display">
        Drop a long video to start
      </h2>
      <p className="mt-3 max-w-xl text-body text-muted-foreground">
        Drag a podcast, interview, or stream recording anywhere on this page, or choose a file
        or a YouTube link. Clipah transcribes it and finds the moments worth posting.
      </p>
      <div className="mt-6">
        <NewProjectButton size="lg" label="Choose a video" />
      </div>
    </section>
  )
}

function ContinueEditing({ lead, others }: { lead: ClipSummaryResponse; others: ClipSummaryResponse[] }) {
  return (
    <section aria-labelledby="continue-title" className="space-y-4">
      <h2 id="continue-title" className="text-title">
        Continue editing
      </h2>
      <div className="grid gap-6 rounded-lg border bg-card p-4 sm:grid-cols-[200px_minmax(0,1fr)] sm:p-6 lg:grid-cols-[240px_minmax(0,1fr)]">
        <div className="relative aspect-[9/16] overflow-hidden rounded-md">
          <Poster projectId={lead.projectId} startMs={lead.startMs} endMs={lead.endMs} aspect="portrait" durationMs={lead.durationMs} />
        </div>
        <div className="flex min-w-0 flex-col justify-center gap-3">
          <p className="text-caption font-medium uppercase tracking-wide text-subtle-foreground">{lead.projectName}</p>
          <p className="font-display text-balance text-h1 lg:text-display">{lead.hook}</p>
          <p className="font-mono text-caption text-muted-foreground">
            {lead.editUpdatedAt === null ? '' : `Saved ${formatWhen(lead.editUpdatedAt)} · `}
            Revision {lead.currentRevision ?? 1}
          </p>
          <div>
            <Button asChild size="lg">
              <Link href={`/editor/${lead.editId}`}>Continue editing</Link>
            </Button>
          </div>
        </div>
      </div>
      {others.length === 0 ? null : (
        <ul aria-label="More clips in editing" className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {others.map((clip) => (
            <li key={clip.id}>
              <MediaCard
                href={`/editor/${clip.editId}`}
                title={clip.hook}
                aspect="portrait"
                subtitle={clip.projectName}
                thumbnail={<Poster projectId={clip.projectId} startMs={clip.startMs} endMs={clip.endMs} aspect="portrait" durationMs={clip.durationMs} />}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function ProcessingNow({ jobs, names }: { jobs: DashboardJobResponse[]; names: Map<string, string> }) {
  return (
    <section aria-labelledby="processing-title" className="space-y-3">
      <h2 id="processing-title" className="text-title">
        Processing now
      </h2>
      <ul aria-label="Processing now" className="space-y-2">
        {jobs.map((job) => (
          <li
            key={job.id}
            className="grid gap-3 rounded-lg border bg-card p-4 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)_auto] sm:items-center"
          >
            <p className="truncate text-small font-semibold">{names.get(job.projectId) ?? 'Project'}</p>
            <StageBar kind={job.kind} status={job.status} />
            <Link href={`/dashboard/projects/${job.projectId}`} className="text-small font-semibold text-primary hover:underline">
              Open project
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ReadyToReview({ candidates }: { candidates: DashboardCandidateResponse[] }) {
  return (
    <section aria-labelledby="ready-title" className="space-y-3">
      <div className="flex items-end justify-between gap-3">
        <h2 id="ready-title" className="text-title">
          Ready to review
        </h2>
        <Link href="/dashboard/clips" className="inline-flex items-center gap-1 text-small font-semibold text-primary hover:underline">
          All clips <ArrowRight aria-hidden="true" strokeWidth={1.75} className="size-4" />
        </Link>
      </div>
      <ul aria-label="Ready to review" className="-mx-4 flex snap-x gap-3 overflow-x-auto px-4 pb-2 sm:-mx-6 sm:px-6">
        {candidates.map((candidate) => (
          <li key={candidate.id} className="w-40 shrink-0 snap-start sm:w-44">
            <MediaCard
              href={`/dashboard/clips/${candidate.id}`}
              title={candidate.hook}
              hideTitle
              aspect="portrait"
              subtitle={candidate.projectName}
              thumbnail={
                <Poster
                  projectId={candidate.projectId}
                  startMs={candidate.startMs}
                  endMs={candidate.endMs}
                  aspect="portrait"
                  rank={candidate.rank}
                  durationMs={candidate.endMs - candidate.startMs}
                  hook={candidate.hook}
                />
              }
            />
          </li>
        ))}
      </ul>
    </section>
  )
}

function RecentProjects({ projects }: { projects: DashboardProjectResponse[] }) {
  return (
    <section aria-labelledby="recent-title" className="space-y-3">
      <div className="flex items-end justify-between gap-3">
        <h2 id="recent-title" className="text-title">
          Recent projects
        </h2>
        <Link href="/dashboard/projects" className="inline-flex items-center gap-1 text-small font-semibold text-primary hover:underline">
          All projects <ArrowRight aria-hidden="true" strokeWidth={1.75} className="size-4" />
        </Link>
      </div>
      <ul aria-label="Recent projects" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {projects.map((project) => (
          <li key={project.id}>
            <MediaCard
              href={`/dashboard/projects/${project.id}`}
              title={project.name}
              thumbnail={<Poster projectId={project.id} hasMedia={project.status !== 'created' && project.status !== 'uploading'} />}
              status={
                <StatusBadge tone={projectStatusTone(project.status)} appearance="overlay">
                  {projectStatusLabel(project.status)}
                </StatusBadge>
              }
              subtitle={`Updated ${formatWhen(project.updatedAt)}`}
            />
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The newest pipeline job per Project, so each Project gets one stage bar. */
function latestPipelineJobs(jobs: DashboardJobResponse[]): DashboardJobResponse[] {
  const latest = new Map<string, DashboardJobResponse>()
  for (const job of jobs) {
    if (!PIPELINE_KINDS.has(job.kind)) continue
    const known = latest.get(job.projectId)
    if (known === undefined || known.updatedAt < job.updatedAt) latest.set(job.projectId, job)
  }
  return [...latest.values()]
}

function formatWhen(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
```

The `ReadyToReview` reel links to the clip page; Plan 4 retargets it to review mode. `hideTitle` keeps the link named by the hook while the poster shows it in caption type.


- [ ] **Step 5: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/dashboard.test.tsx` → PASS.

---

### Task 7: Projects grid

**Files:**
- Modify: `frontend/features/projects/projects-panel.tsx`
- Modify: `frontend/tests/projects.test.tsx` (only if an assertion reads removed text)

- [ ] **Step 1: Write the failing test**

Add to "the project list" in `frontend/tests/projects.test.tsx`:

```tsx
  test('shows each project as a poster that asks for its storyboard only once it has media', async () => {
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECTS]: {
        body: {
          projects: [
            project({ id: '44444444-4444-4444-8444-444444444441', name: 'Waiting', status: 'created' }),
            project({ id: '44444444-4444-4444-8444-444444444442', name: 'Ready', status: 'ready' }),
          ],
          nextCursor: null,
        },
      },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ProjectsPanel />
      </WorkspaceProvider>,
    )

    const list = await screen.findByRole('list', { name: 'Projects' })
    expect(within(list).getAllByTestId('poster')).toHaveLength(2)
    await waitFor(() =>
      expect(api.calls.some((call) => call.path === '/api/v1/projects/44444444-4444-4444-8444-444444444442/storyboard')).toBe(true),
    )
    expect(api.calls.some((call) => call.path === '/api/v1/projects/44444444-4444-4444-8444-444444444441/storyboard')).toBe(false)
  })
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pnpm --dir frontend exec vitest run tests/projects.test.tsx -t "poster"`
Expected: FAIL — no `poster` test id (the temporary bridge still renders through the old import path, or asks for the waiting project).

- [ ] **Step 3: Move the grid onto `Poster`**

In `projects-panel.tsx`: import `Poster` from `@/components/media/poster` and `ItemMenu` from `@/components/ui/item-menu`; drop the `ProjectThumbnail` import. In `ProjectCard` set:

```tsx
thumbnail={<Poster projectId={project.id} hasMedia={project.status !== 'created' && project.status !== 'uploading'} />}
status={
  <StatusBadge tone={projectStatusTone(project.status)} appearance="overlay">
    {projectStatusLabel(project.status)}
  </StatusBadge>
}
```

Change the grid class to `grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4`, the restore notice container to `flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line-strong bg-card px-4 py-3 text-small`, and move the rename field to the `Input` primitive (`import { Input } from '@/components/ui/input'`).

- [ ] **Step 4: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/projects.test.tsx` → PASS.

---

### Task 8: Clips browser, clip page tabs, render-queue posters, and removing the bridge

**Files:**
- Create: `frontend/components/url-tabs.tsx`
- Modify: `frontend/features/clips/ClipBrowser.tsx`, `frontend/features/clips/ClipDetail.tsx`, `frontend/features/jobs/job-center.tsx`, `frontend/components/media-card.tsx` (delete the `ProjectThumbnail` bridge)
- Modify: `frontend/tests/creator-studio.test.tsx`, `frontend/tests/broll-editor.test.tsx`
- Create: `frontend/tests/url-tabs.test.tsx`

**Interfaces:**
- Produces: `useUrlTab<T extends string>(ids: readonly T[], fallback: T): [T, (next: T) => void]` (reads `?tab=` from `window.location` after mount, writes with `history.replaceState`); `TabList<T extends string>({ label: string; tabs: ReadonlyArray<{ id: T; label: string }>; active: T; onChoose: (id: T) => void; idPrefix: string })`; `TabPanel({ idPrefix: string; id: string; active: string; children: ReactNode })`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/url-tabs.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test } from 'vitest'

import { TabList, TabPanel, useUrlTab } from '@/components/url-tabs'

const TABS = [
  { id: 'exports', label: 'Exports' },
  { id: 'broll', label: 'B-roll' },
] as const

function Tabs() {
  const [tab, choose] = useUrlTab(['exports', 'broll'] as const, 'exports')
  return (
    <>
      <TabList label="Clip sections" tabs={TABS} active={tab} onChoose={choose} idPrefix="clip" />
      <TabPanel idPrefix="clip" id="exports" active={tab}>Exports body</TabPanel>
      <TabPanel idPrefix="clip" id="broll" active={tab}>B-roll body</TabPanel>
    </>
  )
}

beforeEach(() => {
  window.history.replaceState(null, '', '/dashboard/clips/one')
})

describe('URL tabs', () => {
  test('opens the tab a link named and keeps the choice in the address', async () => {
    window.history.replaceState(null, '', '/dashboard/clips/one?tab=broll')
    const user = userEvent.setup()
    render(<Tabs />)

    expect(await screen.findByText('B-roll body')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Exports' }))

    expect(screen.getByRole('tab', { name: 'Exports' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Exports body')).toBeInTheDocument()
    expect(window.location.search).toBe('?tab=exports')
  })

  test('ignores an unknown tab', () => {
    window.history.replaceState(null, '', '/dashboard/clips/one?tab=nope')
    render(<Tabs />)

    expect(screen.getByText('Exports body')).toBeInTheDocument()
  })
})
```

In `frontend/tests/creator-studio.test.tsx` "shows every clip before anything is searched", add after the existing assertions:

```tsx
    expect(within(list).getAllByTestId('poster')).toHaveLength(2)
```

In `frontend/tests/broll-editor.test.tsx`, in the three clip-page tests that read B-roll ("every picture in the clip is listed…", "generated media is labelled…", "a suggestion nobody accepted…"), open the tab first:

```tsx
    await userEvent.click(await screen.findByRole('tab', { name: 'B-roll' }))
```

and add a new test:

```tsx
  test('the clip page opens on its exports and keeps each section one tab away', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [CLIP_DETAIL]: { body: clipDetail() },
      [SUGGESTIONS]: { body: { suggestions: [] } },
    })
    renderClipDetail()

    const tabs = await screen.findByRole('tablist', { name: 'Clip sections' })
    expect(within(tabs).getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Exports',
      'Revisions',
      'B-roll',
      'Variants',
      'Evidence',
      'Campaign copy',
    ])
    expect(within(tabs).getByRole('tab', { name: 'Exports' })).toHaveAttribute('aria-selected', 'true')
  })
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/url-tabs.test.tsx tests/creator-studio.test.tsx tests/broll-editor.test.tsx`
Expected: FAIL — `url-tabs` missing, no tablist on the clip page, no posters in the clip browser.

- [ ] **Step 3: Implement `frontend/components/url-tabs.tsx`**

```tsx
'use client'

import { useEffect, useState, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** A tab choice that lives in `?tab=`, so a link can open a section and a refresh keeps it. */
export function useUrlTab<T extends string>(ids: readonly T[], fallback: T): [T, (next: T) => void] {
  const [tab, setTab] = useState<T>(fallback)

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get('tab')
    const known = ids.find((id) => id === requested)
    if (known !== undefined) setTab(known)
    // The address is read once, when the page opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function choose(next: T): void {
    setTab(next)
    const url = new URL(window.location.href)
    url.searchParams.set('tab', next)
    window.history.replaceState(window.history.state, '', url.toString())
  }

  return [tab, choose]
}

export function TabList<T extends string>({
  label,
  tabs,
  active,
  onChoose,
  idPrefix,
}: {
  label: string
  tabs: ReadonlyArray<{ id: T; label: string }>
  active: T
  onChoose: (id: T) => void
  idPrefix: string
}) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b">
      {tabs.map((entry) => (
        <button
          key={entry.id}
          type="button"
          role="tab"
          id={`${idPrefix}-tab-${entry.id}`}
          aria-selected={active === entry.id}
          aria-controls={`${idPrefix}-panel-${entry.id}`}
          onClick={() => onChoose(entry.id)}
          className={cn(
            '-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-small font-semibold transition-colors duration-fast ease-signal',
            active === entry.id
              ? 'border-primary text-foreground'
              : 'border-transparent text-muted-foreground hover:text-foreground',
          )}
        >
          {entry.label}
        </button>
      ))}
    </div>
  )
}

/** Only the chosen panel is mounted, so a tab never reads data nobody is looking at. */
export function TabPanel({
  idPrefix,
  id,
  active,
  children,
}: {
  idPrefix: string
  id: string
  active: string
  children: ReactNode
}) {
  if (id !== active) return null
  return (
    <div role="tabpanel" id={`${idPrefix}-panel-${id}`} aria-labelledby={`${idPrefix}-tab-${id}`} className="pt-5">
      {children}
    </div>
  )
}
```

- [ ] **Step 4: Rebuild the clip browser**

In `features/clips/ClipBrowser.tsx`:

- Replace the filter buttons with `SegmentedControl` (`label="Show clips"`, options from `FILTERS` mapped to `{ value: id, label }`, `value={stage}`, `onChange={setStage}`).
- Keep the Project `Select` (label "Project") and the search form (input moves to the `Input` primitive).
- `ClipTile` becomes:

```tsx
function ClipTile({ clip }: { clip: ClipSummaryResponse }) {
  const badge = STAGE_BADGES[clip.stage]
  return (
    <MediaCard
      href={`/dashboard/clips/${clip.id}`}
      title={clip.hook}
      hideTitle
      aspect="portrait"
      thumbnail={
        <Poster
          projectId={clip.projectId}
          startMs={clip.startMs}
          endMs={clip.endMs}
          aspect="portrait"
          rank={clip.rank}
          durationMs={clip.durationMs}
          hook={clip.hook}
        />
      }
      subtitle={
        <span className="flex items-center gap-2">
          <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
          <span className="truncate">{clip.projectName}</span>
        </span>
      }
      footer={
        clip.editId === null ? null : (
          <Link href={`/editor/${clip.editId}`} className="text-caption font-semibold text-primary hover:underline">
            Continue editing
          </Link>
        )
      }
    />
  )
}
```

- The grid becomes `grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6`.

- [ ] **Step 5: Rebuild the clip page**

In `features/clips/ClipDetail.tsx` (`ResolvedClip`):

- Layout: `grid gap-8 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]`. Left column: a `relative aspect-[9/16] overflow-hidden rounded-lg bg-stage` box containing `ClipPreview` (its `video` gets `className="absolute inset-0 size-full object-contain"`). Right column: the `PageHeader` (title, crumbs, meta, `EditAction`), then a "Why this moment" block (reason, excerpt, context warnings in `bg-warning-soft text-warning` with `rounded-lg p-3`), then Export and Publish links when an export exists (`/dashboard/publishing/new?renderId=<renderId>` — reuse the existing link helper from `features/exports/export-list.tsx` if one exists; otherwise link to the Exports tab).
- Below both columns:

```tsx
const SECTIONS = [
  { id: 'exports', label: 'Exports' },
  { id: 'revisions', label: 'Revisions' },
  { id: 'broll', label: 'B-roll' },
  { id: 'variants', label: 'Variants' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'campaign', label: 'Campaign copy' },
] as const
type SectionId = (typeof SECTIONS)[number]['id']
```

```tsx
const [section, choose] = useUrlTab<SectionId>(SECTIONS.map((entry) => entry.id), 'exports')
…
<div>
  <TabList label="Clip sections" tabs={SECTIONS} active={section} onChoose={choose} idPrefix="clip" />
  <TabPanel idPrefix="clip" id="exports" active={section}>{/* existing exports list or empty state */}</TabPanel>
  <TabPanel idPrefix="clip" id="revisions" active={section}>{edit === null ? <EmptyState compact title="No revisions yet" description="Edit this clip to save its first version." /> : <RevisionHistory editId={edit.id} />}</TabPanel>
  <TabPanel idPrefix="clip" id="broll" active={section}><BrollProvenanceList projectId={project.id} candidateId={candidate.id} /></TabPanel>
  <TabPanel idPrefix="clip" id="variants" active={section}><VariantLab … /></TabPanel>
  <TabPanel idPrefix="clip" id="evidence" active={section}><EvidencePanel … /></TabPanel>
  <TabPanel idPrefix="clip" id="campaign" active={section}>{/* existing campaign branch */}</TabPanel>
</div>
```

Delete `Disclosure` and the stacked `Section`s they replace. `RevisionHistory` renders as a vertical timeline: an `ol` with `relative border-l border-line-strong pl-4` and each `li` with a `before:` dot (`before:absolute before:-left-[5px] before:top-2 before:size-2 before:rounded-full before:bg-line-strong`, the current revision `before:bg-primary`), keeping the list's `aria-label="Revisions"`.

- [ ] **Step 6: Posters in the render queue, and remove the bridge**

In `features/jobs/job-center.tsx`, give each job row with a `projectId` a leading `relative aspect-video w-16 shrink-0 overflow-hidden rounded-sm` box containing `<Poster projectId={job.projectId} />`, and lay the row out as `flex gap-3`.

Delete the `ProjectThumbnail` bridge export from `components/media-card.tsx`, then run `grep -rn "ProjectThumbnail\|MediaPlaceholder" frontend/app frontend/components frontend/features` → no output.

- [ ] **Step 7: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/url-tabs.test.tsx tests/creator-studio.test.tsx tests/broll-editor.test.tsx tests/jobs.test.tsx` → PASS.

---

### Task 9: Gates, screenshots, and handover

- [ ] **Step 1: Run the frontend gates from the repository root**

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

Expected: all PASS.

- [ ] **Step 2: Capture and inspect**

Rebuild the frontend service, then from `frontend/`:

```bash
CLIPAH_CAPTURE_SCREENS=media CLIPAH_E2E_BASE_URL=http://localhost:3000 pnpm exec playwright test e2e/design-screens.spec.ts --project=chromium
```

Expected: PASS with no sideways overflow. Seeded projects have no storyboard, so posters show designed frames; confirm no screenshot contains the words "No preview yet". If Plan 2's backfill ran against a real Project, open Home and Projects in a browser, hover a poster, and confirm the frame changes without new network requests (DevTools Network panel, filter `storyboard`).

- [ ] **Step 3: Run the browser suite**

From `frontend/`: `CLIPAH_E2E_BASE_URL=http://localhost:3000 pnpm exec playwright test --project=chromium --project=webkit`. Fix selectors broken by the tab and segmented-control changes (clip page sections now open by tab; the Home reel is `Ready to review`), and re-run until counts match the last recorded run.

- [ ] **Step 4: Record progress**

Append "Signal Studio redesign — Plan 3, media surfaces" to `PROGRESS.md`: components, screens, the deliberate choice to show source duration from the storyboard (the project list read carries no duration or clip count), gate output, screenshot folder, and the owner commit message `feat: show media across the studio`. Do not run `git commit`.
