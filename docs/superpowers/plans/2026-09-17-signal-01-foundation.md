# Signal Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (the repository owner requires inline execution without subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-skin the whole product in the dark "Signal" identity through tokens, fonts, and shared primitives, and replace the shell with the icon rail, command palette, render queue, and global drop — without changing any screen's data flow.

**Architecture:** Tokens live once, as RGB triplets in `app/globals.css`, mirrored by a typed hex table in `lib/design/tokens.ts` that a unit test checks for drift and contrast. The existing shadcn variable names are kept and re-pointed, so every existing class (`bg-card`, `text-muted-foreground`, `bg-primary`) adopts Signal at once. Native form controls move behind `components/ui/` wrappers, and a lint rule keeps them there. The shell is split into small files under `components/shell/`.

**Tech Stack:** Next.js 15 (`next/font/local`), React 19, Tailwind CSS 3.4, tailwind-merge 2, Radix Tooltip/Dialog/Switch, cmdk 1, sonner 1, Vitest 3 + Testing Library + user-event 14, Playwright.

**Spec:** `redesign-plan-v2.md` → Visual System, Components, Shell. Plan index: `docs/superpowers/plans/2026-09-17-signal-studio-redesign.md`.

## Global Constraints

- Everything in the plan index's "Global constraints" applies.
- Token hex values (exact): background `#0E0E10`, panel `#16161A`, raised `#1E1E23`, overlay `#26262C`, stage `#0A0A0B`, line `#2A2A30`, line-strong `#3A3A42`, control-border `#72727C`, text `#F2F2EE`, text-secondary `#A1A1A8`, text-muted `#8F8F98`, accent `#C6FF3D`, accent-hover `#D4FF66`, accent-soft `#2B321E`, on-accent `#0E0E10`, signal `#FF5A50`, signal-soft `#372022`, on-signal `#0E0E10`, warning `#FFB020`, warning-soft `#372C1B`.
- Radius: 4 px controls, 6 px cards and tiles. Motion: 120 ms hover, 180 ms panels, 240 ms dialogs, `cubic-bezier(0.2, 0, 0, 1)`.
- Type scale (px size / line height): caption 12/16, small 13/20, body 15/22, title 18/24 (weight 650), h2 24/28, h1 36/36, display 56/52, hero 88/80.
- Fonts: Archivo (variable `wght` + `wdth`) and JetBrains Mono (variable `wght`), vendored and loaded with `next/font/local`.
- At most one filled-lime control per view. Selected states mark with lime text or a lime outline, not a second lime fill.
- Icons: `lucide-react`, `strokeWidth={1.75}`, 16 or 20 px, never inside a tinted square.
- Owner commit message for this plan: `feat: add signal studio foundation`.

## Token name mapping (used by Tasks 2–13)

| Spec token | CSS variable | Tailwind colour |
| --- | --- | --- |
| background | `--background` | `background` |
| panel | `--card` | `card` |
| raised | `--secondary`, `--muted`, `--accent` | `secondary`, `muted`, `accent` |
| overlay | `--popover` | `popover` |
| stage | `--stage` | `stage` |
| line | `--border` | `border` |
| line-strong | `--line-strong` | `line-strong` |
| control-border | `--input` | `input` |
| text | `--foreground`, `--card-foreground`, `--popover-foreground`, `--secondary-foreground`, `--accent-foreground` | `foreground` … |
| text-secondary | `--muted-foreground`, `--info` | `muted-foreground`, `info` |
| text-muted | `--subtle-foreground` | `subtle-foreground` |
| accent (lime) | `--primary`, `--ring`, `--success` | `primary`, `ring`, `success` |
| accent-hover | `--primary-hover` | `primary-hover` |
| accent-soft | `--primary-soft`, `--success-soft` | `primary-soft`, `success-soft` |
| on-accent | `--primary-foreground` | `primary-foreground` |
| signal | `--destructive` | `destructive` |
| signal-soft | `--destructive-soft` | `destructive-soft` |
| on-signal | `--destructive-foreground` | `destructive-foreground` |
| warning | `--warning` | `warning` |
| warning-soft | `--warning-soft` | `warning-soft` |
| raised (info tint) | `--info-soft` | `info-soft` |

## File map

| File | Responsibility |
| --- | --- |
| `frontend/e2e/design-screens.spec.ts` | Opt-in screenshot capture of every route at 1440/820/390 px, plus a horizontal-overflow assertion |
| `frontend/lib/design/tokens.ts` | Typed hex table, contrast pairs, and pure colour maths |
| `frontend/lib/design/fonts.ts` + `frontend/lib/design/fonts/*` | Vendored font files and the `next/font/local` loaders |
| `frontend/app/globals.css` | RGB token triplets, base styles, native control skins |
| `frontend/tailwind.config.ts` | Colour, radius, type, and motion scales |
| `frontend/lib/utils.ts` | `cn` with the custom font-size group registered in tailwind-merge |
| `frontend/components/ui/{button,icon-button,select,checkbox,radio,slider,segmented-control,switch,input,tooltip,sonner,item-menu,skeleton}.tsx` | Signal primitives |
| `frontend/lib/notify.ts` | The one way product code raises a toast |
| `frontend/components/{page-header,status-badge,empty-state,error-notice,loading-state,field}.tsx` | Rebuilt shared components |
| `frontend/components/shell/{navigation.ts,rail.tsx,top-bar.tsx,account-menu.tsx,phone-tabs.tsx,render-queue.tsx,command-palette.tsx,drop-target.tsx,wordmark.tsx}` | The shell, one concern per file |
| `frontend/components/dashboard-shell.tsx` | Composes the shell pieces |
| `frontend/tests/{design-tokens,design-rules,root-layout,ui-primitives,shared-components,shell,command-palette}.test.ts(x)` | New tests |

---

### Task 1: Capture baseline screenshots

**Files:**
- Create: `frontend/e2e/design-screens.spec.ts`
- Create: `docs/design/signal/.gitkeep`

**Interfaces:**
- Consumes: `seedMemberWithClip`, `signIn`, `uniqueEmail` from `frontend/e2e/support/seed.ts`.
- Produces: `docs/design/signal/<phase>/<route>-<width>.png` images; later plans run the same spec with another `CLIPAH_CAPTURE_SCREENS` value.

- [ ] **Step 1: Write the capture spec**

```ts
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'

import { seedMemberWithClip, signIn, uniqueEmail } from './support/seed'

/**
 * Design screenshots of every route at the three widths the redesign is reviewed at.
 *
 * Opt-in: nothing runs unless `CLIPAH_CAPTURE_SCREENS` names the folder under
 * `docs/design/signal/` to write into. The same run proves no route scrolls sideways.
 */
const PHASE = process.env.CLIPAH_CAPTURE_SCREENS
const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'
const WIDTHS = [1440, 820, 390] as const
const OUTPUT = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../docs/design/signal',
  PHASE ?? 'unset',
)

test.skip(PHASE === undefined, 'set CLIPAH_CAPTURE_SCREENS=<folder> to capture screenshots')

test('every route renders without sideways scrolling at each review width', async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== 'chromium', 'screenshots are captured once, in Chromium')
  test.setTimeout(600_000)
  mkdirSync(OUTPUT, { recursive: true })

  const member = await seedMemberWithClip({
    email: uniqueEmail('design-screens'),
    displayName: 'Maya Creator',
    workspaceName: "Maya's Studio",
    projectName: 'Podcast episode 42',
  })
  await signIn(page.context(), member, SITE)
  const { projectId, candidateId } = member.project
  const created = await page.request.post(
    `/api/v1/projects/${projectId}/candidates/${candidateId}/edits?workspace_id=${member.workspaceId}`,
    {
      headers: { 'X-CSRF-Token': member.csrfToken, Origin: SITE },
      data: { templateId: null, brandKitId: null },
    },
  )
  expect(created.ok()).toBe(true)
  const editId = ((await created.json()) as { id: string }).id

  const routes: Array<[string, string]> = [
    ['landing', '/'],
    ['signin', '/signin'],
    ['demo', '/demo'],
    ['home', '/dashboard'],
    ['projects', '/dashboard/projects'],
    ['project', `/dashboard/projects/${projectId}`],
    ['clips', '/dashboard/clips'],
    ['clip', `/dashboard/clips/${candidateId}`],
    ['publishing', '/dashboard/publishing'],
    ['publishing-new', '/dashboard/publishing/new'],
    ['assets', '/dashboard/assets'],
    ['templates', '/dashboard/templates'],
    ['brand-kits', '/dashboard/brand-kits'],
    ['settings', '/dashboard/settings'],
    ['team', '/dashboard/team'],
    ['connections', '/dashboard/settings/connections'],
    ['search', '/dashboard/search?q=moment'],
    ['editor', `/editor/${editId}`],
  ]

  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
    for (const [name, path] of routes) {
      await page.goto(path)
      await settle(page)
      await page.screenshot({ path: `${OUTPUT}/${name}-${width}.png`, fullPage: true })
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      )
      expect(overflow, `${name} at ${width}px scrolls sideways`).toBeLessThanOrEqual(0)
    }
  }
})

/** Wait until the network is quiet so a screenshot shows data rather than skeletons. */
async function settle(page: Page): Promise<void> {
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(400)
}
```

- [ ] **Step 2: Create the output folder marker**

Run: `mkdir -p docs/design/signal && touch docs/design/signal/.gitkeep`

- [ ] **Step 3: Capture the baseline against the running Compose stack**

Run from `frontend/` (the stack from `infra/compose.yaml` must be up and serving `http://localhost:3000`; seeding adds one member to the local database and deletes nothing):

```bash
CLIPAH_CAPTURE_SCREENS=before CLIPAH_E2E_BASE_URL=http://localhost:3000 pnpm exec playwright test e2e/design-screens.spec.ts --project=chromium
```

Expected: PASS, and 54 PNG files in `docs/design/signal/before/`.

Executed 2026-09-18. Two changes from the code above were needed: (1) 54 page loads by one member exceed the 60-per-minute read limit, so `settle` became `visit(page, path)`, which reloads a route after 61 s whenever any response during its load was a 429 (a full capture takes about 5 minutes until Plan 2 raises the limit); (2) the seeding command runs on the host, so it needs `CLIPAH_DATABASE_URL`, `CLIPAH_SESSION_SECRET`, and `CLIPAH_WORKER_DATABASE_URL` copied from the `api` and `worker-render` containers with the host rewritten to `127.0.0.1:55433`. If an overflow assertion fails on the current UI, record the route and width in the task notes and re-run with that one assertion commented out locally — the baseline documents today's UI, it does not fix it. Restore the assertion before moving on.

- [ ] **Step 4: Confirm the spec is inert by default**

Run: `pnpm exec playwright test e2e/design-screens.spec.ts --project=chromium --list`
Expected: the test is listed; running without `CLIPAH_CAPTURE_SCREENS` reports it as skipped.

---

### Task 2: Signal tokens, scales, and the contrast test

**Files:**
- Create: `frontend/lib/design/tokens.ts`
- Create: `frontend/tests/design-tokens.test.ts`
- Modify: `frontend/app/globals.css` (full rewrite)
- Modify: `frontend/tailwind.config.ts` (full rewrite)
- Modify: `frontend/lib/utils.ts`

**Interfaces:**
- Produces: `SIGNAL_TOKENS: Record<SignalToken, string>` (hex), `type SignalToken`, `TEXT_ON_SURFACE_PAIRS`, `SOLID_PAIRS`, `BOUNDARY_PAIRS`, `contrastRatio(a: string, b: string): number`, `hexToRgb(hex): [number, number, number]`, `hslSaturationAndHue(hex): { hue: number; saturation: number }`, `parseTokenTriplets(css: string): Record<string, string>` (hex by variable name).
- Produces Tailwind classes used by every later task: `text-caption|small|body|title|h2|h1|display|hero`, `font-display` (component class), `font-mono`, `bg-stage`, `bg-primary-soft`, `text-subtle-foreground`, `border-line-strong`, `duration-fast|panel|dialog`, `ease-signal`, `max-w-studio`, `.surface`, `.signal-checkbox`, `.signal-radio`, `.signal-range`.

- [ ] **Step 1: Write the failing token test**

`frontend/tests/design-tokens.test.ts`:

```ts
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, test } from 'vitest'

import {
  BOUNDARY_PAIRS,
  SIGNAL_TOKENS,
  SOLID_PAIRS,
  TEXT_ON_SURFACE_PAIRS,
  contrastRatio,
  hslSaturationAndHue,
  parseTokenTriplets,
  type SignalToken,
} from '@/lib/design/tokens'
import { cn } from '@/lib/utils'

const css = readFileSync(resolve(__dirname, '../app/globals.css'), 'utf8')

describe('the Signal palette', () => {
  test('globals.css stores exactly the hex values the design system declares', () => {
    const stored = parseTokenTriplets(css)
    for (const [name, hex] of Object.entries(SIGNAL_TOKENS)) {
      expect(stored[name], `--${name}`).toBe(hex)
    }
  })

  test('every text colour reads at 4.5:1 on every surface it is placed on', () => {
    for (const [text, surface] of TEXT_ON_SURFACE_PAIRS) {
      expect(ratio(text, surface), `${text} on ${surface}`).toBeGreaterThanOrEqual(4.5)
    }
  })

  test('text on a filled or tinted colour reads at 4.5:1', () => {
    for (const [text, fill] of SOLID_PAIRS) {
      expect(ratio(text, fill), `${text} on ${fill}`).toBeGreaterThanOrEqual(4.5)
    }
  })

  test('control borders are visible at 3:1 on every surface', () => {
    for (const [border, surface] of BOUNDARY_PAIRS) {
      expect(ratio(border, surface), `${border} on ${surface}`).toBeGreaterThanOrEqual(3)
    }
  })

  test('no chromatic token is violet, indigo, or purple', () => {
    for (const [name, hex] of Object.entries(SIGNAL_TOKENS)) {
      const { hue, saturation } = hslSaturationAndHue(hex)
      const banned = saturation >= 20 && hue >= 240 && hue <= 300
      expect(banned, `--${name} ${hex}`).toBe(false)
    }
  })

  test('the type scale is registered with tailwind-merge so size and colour both survive', () => {
    expect(cn('text-caption', 'text-muted-foreground')).toBe('text-caption text-muted-foreground')
    expect(cn('text-small', 'text-h1')).toBe('text-h1')
  })
})

function ratio(foreground: SignalToken, background: SignalToken): number {
  return contrastRatio(SIGNAL_TOKENS[foreground], SIGNAL_TOKENS[background])
}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pnpm --dir frontend exec vitest run tests/design-tokens.test.ts`
Expected: FAIL — `Cannot find module '@/lib/design/tokens'`.

- [ ] **Step 3: Write the token table and colour maths**

`frontend/lib/design/tokens.ts`:

```ts
/**
 * The Signal palette as data.
 *
 * `app/globals.css` stores these values as RGB triplets for Tailwind; this table is the
 * readable copy a test holds the stylesheet to, and the place contrast is proven rather
 * than eyeballed. Variable names follow shadcn so every existing class adopts Signal.
 */
export const SIGNAL_TOKENS = {
  background: '#0E0E10',
  foreground: '#F2F2EE',
  card: '#16161A',
  'card-foreground': '#F2F2EE',
  popover: '#26262C',
  'popover-foreground': '#F2F2EE',
  primary: '#C6FF3D',
  'primary-hover': '#D4FF66',
  'primary-foreground': '#0E0E10',
  'primary-soft': '#2B321E',
  secondary: '#1E1E23',
  'secondary-foreground': '#F2F2EE',
  muted: '#1E1E23',
  'muted-foreground': '#A1A1A8',
  'subtle-foreground': '#8F8F98',
  accent: '#1E1E23',
  'accent-foreground': '#F2F2EE',
  destructive: '#FF5A50',
  'destructive-foreground': '#0E0E10',
  'destructive-soft': '#372022',
  warning: '#FFB020',
  'warning-soft': '#372C1B',
  success: '#C6FF3D',
  'success-soft': '#2B321E',
  info: '#A1A1A8',
  'info-soft': '#1E1E23',
  border: '#2A2A30',
  'line-strong': '#3A3A42',
  input: '#72727C',
  ring: '#C6FF3D',
  stage: '#0A0A0B',
} as const

export type SignalToken = keyof typeof SIGNAL_TOKENS

const SURFACES = ['background', 'card', 'secondary', 'popover'] as const satisfies readonly SignalToken[]
const TEXTS = [
  'foreground',
  'muted-foreground',
  'subtle-foreground',
  'primary',
  'destructive',
  'warning',
] as const satisfies readonly SignalToken[]

/** Every text colour against every surface text is placed on. */
export const TEXT_ON_SURFACE_PAIRS: ReadonlyArray<readonly [SignalToken, SignalToken]> =
  TEXTS.flatMap((text) => SURFACES.map((surface) => [text, surface] as const))

/** Text drawn on a filled control or a tinted banner. */
export const SOLID_PAIRS: ReadonlyArray<readonly [SignalToken, SignalToken]> = [
  ['primary-foreground', 'primary'],
  ['primary-foreground', 'primary-hover'],
  ['destructive-foreground', 'destructive'],
  ['foreground', 'primary-soft'],
  ['primary', 'primary-soft'],
  ['foreground', 'destructive-soft'],
  ['destructive', 'destructive-soft'],
  ['foreground', 'warning-soft'],
  ['warning', 'warning-soft'],
]

/** A control's border against every surface a control sits on. */
export const BOUNDARY_PAIRS: ReadonlyArray<readonly [SignalToken, SignalToken]> = SURFACES.map(
  (surface) => ['input', surface] as const,
)

/** Split `#RRGGBB` into its three channels. */
export function hexToRgb(hex: string): [number, number, number] {
  const match = /^#([0-9A-F]{2})([0-9A-F]{2})([0-9A-F]{2})$/i.exec(hex)
  if (match === null) {
    throw new Error(`not a six-digit hex colour: ${hex}`)
  }
  return [parseInt(match[1] ?? '0', 16), parseInt(match[2] ?? '0', 16), parseInt(match[3] ?? '0', 16)]
}

/** The WCAG 2.2 contrast ratio between two opaque colours. */
export function contrastRatio(first: string, second: string): number {
  const [lighter, darker] = [luminance(first), luminance(second)].sort((a, b) => b - a) as [
    number,
    number,
  ]
  return (lighter + 0.05) / (darker + 0.05)
}

/** Hue in degrees and saturation in percent, as HSL reports them. */
export function hslSaturationAndHue(hex: string): { hue: number; saturation: number } {
  const [r, g, b] = hexToRgb(hex).map((channel) => channel / 255) as [number, number, number]
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const lightness = (max + min) / 2
  const delta = max - min
  if (delta === 0) {
    return { hue: 0, saturation: 0 }
  }
  const saturation = delta / (1 - Math.abs(2 * lightness - 1))
  let hue: number
  if (max === r) {
    hue = ((g - b) / delta) % 6
  } else if (max === g) {
    hue = (b - r) / delta + 2
  } else {
    hue = (r - g) / delta + 4
  }
  return { hue: Math.round((hue * 60 + 360) % 360), saturation: Math.round(saturation * 100) }
}

/** Read `--name: r g b;` declarations from a stylesheet as upper-case hex by name. */
export function parseTokenTriplets(css: string): Record<string, string> {
  const tokens: Record<string, string> = {}
  for (const match of css.matchAll(/--([a-z-]+):\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s*;/g)) {
    const [, name, r, g, b] = match
    if (name === undefined || r === undefined || g === undefined || b === undefined) {
      continue
    }
    tokens[name] = `#${[r, g, b].map((channel) => Number(channel).toString(16).padStart(2, '0')).join('').toUpperCase()}`
  }
  return tokens
}

function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((channel) => {
    const value = channel / 255
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  }) as [number, number, number]
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
```

- [ ] **Step 4: Rewrite `frontend/app/globals.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/*
 * Signal: Clipah's dark studio.
 *
 * Graphite surfaces step up in lightness instead of casting shadows, and one acid lime
 * marks the action that moves a creator forward, the playhead, and what is selected. Red
 * means live, destructive, or failed and nothing else. Values are RGB triplets so Tailwind
 * opacity modifiers keep working; `lib/design/tokens.ts` holds the same values as hex and a
 * test keeps the two identical. Variable names follow shadcn so existing classes adopt the
 * palette; the plan's token mapping table names which spec token each one is.
 */
@layer base {
  :root {
    color-scheme: dark;
    --background: 14 14 16;
    --foreground: 242 242 238;
    --card: 22 22 26;
    --card-foreground: 242 242 238;
    --popover: 38 38 44;
    --popover-foreground: 242 242 238;
    --primary: 198 255 61;
    --primary-hover: 212 255 102;
    --primary-foreground: 14 14 16;
    --primary-soft: 43 50 30;
    --secondary: 30 30 35;
    --secondary-foreground: 242 242 238;
    --muted: 30 30 35;
    --muted-foreground: 161 161 168;
    --subtle-foreground: 143 143 152;
    --accent: 30 30 35;
    --accent-foreground: 242 242 238;
    --destructive: 255 90 80;
    --destructive-foreground: 14 14 16;
    --destructive-soft: 55 32 34;
    --warning: 255 176 32;
    --warning-soft: 55 44 27;
    --success: 198 255 61;
    --success-soft: 43 50 30;
    --info: 161 161 168;
    --info-soft: 30 30 35;
    --border: 42 42 48;
    --line-strong: 58 58 66;
    --input: 114 114 124;
    --ring: 198 255 61;
    --stage: 10 10 11;
    --radius: 6px;
  }
}

@layer base {
  * {
    @apply border-border;
  }
  html {
    -webkit-text-size-adjust: 100%;
  }
  body {
    @apply bg-background font-sans text-body text-foreground antialiased;
  }
  :focus-visible {
    outline: 2px solid rgb(var(--ring));
    outline-offset: 2px;
  }
  ::selection {
    background-color: rgb(var(--primary) / 0.3);
  }
}

@layer components {
  /* Condensed display type: page titles, scores, counts, and caption-style overlays. */
  .font-display {
    font-stretch: 75%;
    font-weight: 800;
    letter-spacing: -0.01em;
    text-transform: uppercase;
  }

  .signal-checkbox,
  .signal-radio {
    appearance: none;
    flex-shrink: 0;
    width: 1rem;
    height: 1rem;
    border: 1px solid rgb(var(--input));
    background-color: rgb(var(--secondary));
    background-position: center;
    background-repeat: no-repeat;
    cursor: pointer;
    transition:
      background-color 120ms cubic-bezier(0.2, 0, 0, 1),
      border-color 120ms cubic-bezier(0.2, 0, 0, 1);
  }
  .signal-checkbox {
    border-radius: 3px;
    background-size: 0.75rem;
  }
  .signal-checkbox:checked {
    border-color: rgb(var(--primary));
    background-color: rgb(var(--primary));
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%230E0E10' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M3.5 8.5l3 3 6-7'/%3E%3C/svg%3E");
  }
  .signal-radio {
    border-radius: 9999px;
  }
  .signal-radio:checked {
    border-color: rgb(var(--primary));
    background-color: rgb(var(--primary));
    box-shadow: inset 0 0 0 3px rgb(var(--secondary));
  }
  .signal-checkbox:disabled,
  .signal-radio:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  .signal-range {
    appearance: none;
    height: 1rem;
    background: transparent;
    cursor: pointer;
  }
  .signal-range::-webkit-slider-runnable-track {
    height: 4px;
    border-radius: 2px;
    background: rgb(var(--line-strong));
  }
  .signal-range::-webkit-slider-thumb {
    appearance: none;
    width: 14px;
    height: 14px;
    margin-top: -5px;
    border: 2px solid rgb(var(--background));
    border-radius: 9999px;
    background: rgb(var(--foreground));
  }
  .signal-range::-moz-range-track {
    height: 4px;
    border-radius: 2px;
    background: rgb(var(--line-strong));
  }
  .signal-range::-moz-range-progress {
    height: 4px;
    border-radius: 2px;
    background: rgb(var(--primary));
  }
  .signal-range::-moz-range-thumb {
    width: 10px;
    height: 10px;
    border: 2px solid rgb(var(--background));
    border-radius: 9999px;
    background: rgb(var(--foreground));
  }
}

@layer utilities {
  .text-balance {
    text-wrap: balance;
  }
  .surface {
    @apply rounded-lg border bg-card text-card-foreground;
  }
  .tabular {
    font-variant-numeric: tabular-nums;
  }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 5: Rewrite `frontend/tailwind.config.ts`**

```ts
import type { Config } from 'tailwindcss'

/** One Signal colour, read from its RGB triplet so opacity modifiers keep working. */
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './features/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        background: token('background'),
        foreground: token('foreground'),
        card: { DEFAULT: token('card'), foreground: token('card-foreground') },
        popover: { DEFAULT: token('popover'), foreground: token('popover-foreground') },
        primary: {
          DEFAULT: token('primary'),
          hover: token('primary-hover'),
          foreground: token('primary-foreground'),
          soft: token('primary-soft'),
        },
        secondary: { DEFAULT: token('secondary'), foreground: token('secondary-foreground') },
        muted: { DEFAULT: token('muted'), foreground: token('muted-foreground') },
        'subtle-foreground': token('subtle-foreground'),
        accent: { DEFAULT: token('accent'), foreground: token('accent-foreground') },
        destructive: {
          DEFAULT: token('destructive'),
          foreground: token('destructive-foreground'),
          soft: token('destructive-soft'),
        },
        warning: { DEFAULT: token('warning'), soft: token('warning-soft') },
        success: { DEFAULT: token('success'), soft: token('success-soft') },
        info: { DEFAULT: token('info'), soft: token('info-soft') },
        border: token('border'),
        'line-strong': token('line-strong'),
        input: token('input'),
        ring: token('ring'),
        stage: token('stage'),
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: '4px',
        sm: '2px',
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        caption: ['12px', { lineHeight: '16px' }],
        small: ['13px', { lineHeight: '20px' }],
        body: ['15px', { lineHeight: '22px' }],
        title: ['18px', { lineHeight: '24px', fontWeight: '650' }],
        h2: ['24px', { lineHeight: '28px' }],
        h1: ['36px', { lineHeight: '36px' }],
        display: ['56px', { lineHeight: '52px' }],
        hero: ['88px', { lineHeight: '80px' }],
      },
      maxWidth: {
        studio: '1600px',
      },
      transitionDuration: {
        fast: '120ms',
        panel: '180ms',
        dialog: '240ms',
      },
      transitionTimingFunction: {
        signal: 'cubic-bezier(0.2, 0, 0, 1)',
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.18s cubic-bezier(0.2, 0, 0, 1)',
        'accordion-up': 'accordion-up 0.18s cubic-bezier(0.2, 0, 0, 1)',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
}

export default config
```

- [ ] **Step 6: Register the type scale in `frontend/lib/utils.ts`**

```ts
import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// Signal's named font sizes look like colours to tailwind-merge unless they are declared,
// and an undeclared `text-caption` would silently erase `text-muted-foreground`.
const merge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [
        { text: ['caption', 'small', 'body', 'title', 'h2', 'h1', 'display', 'hero'] },
      ],
    },
  },
})

/** Join class names, letting a later Tailwind class replace an earlier conflicting one. */
export function cn(...inputs: ClassValue[]): string {
  return merge(clsx(inputs))
}
```

- [ ] **Step 7: Run the token test and see it pass**

Run: `pnpm --dir frontend exec vitest run tests/design-tokens.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 8: Replace the two remaining `hsl(var(` usages**

Run: `grep -rn "hsl(var(" frontend/app frontend/components frontend/features frontend/lib`
Expected before: `components/ui/sidebar.tsx:521` and `features/editor/ExportDialog.tsx:131`. `sidebar.tsx` is deleted in Task 4. In `ExportDialog.tsx`, change `className="mt-1 accent-[hsl(var(--primary))]"` to `className="mt-1"` (Task 7 moves this radio onto the `Radio` primitive). Re-run the grep; only `sidebar.tsx` may remain until Task 4.

- [ ] **Step 9: Run the full frontend suite**

Run: `pnpm test`
Expected: PASS. jsdom applies no stylesheet, so no behavioural test changes.

---

### Task 3: Vendored fonts and the root layout

**Files:**
- Create: `frontend/lib/design/fonts/Archivo-Variable.ttf`, `frontend/lib/design/fonts/JetBrainsMono-Variable.ttf`, `frontend/lib/design/fonts/OFL-Archivo.txt`, `frontend/lib/design/fonts/OFL-JetBrainsMono.txt`, `frontend/lib/design/fonts/SOURCES.md`
- Create: `frontend/lib/design/fonts.ts`
- Create: `frontend/tests/root-layout.test.tsx`
- Modify: `frontend/app/layout.tsx`

**Interfaces:**
- Produces: `fontVariables: string` (the two `next/font` variable class names, space-separated), CSS variables `--font-sans` and `--font-mono` on `<html>`.

- [ ] **Step 1: Write the failing layout test**

`frontend/tests/root-layout.test.tsx`:

```tsx
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, test, vi } from 'vitest'

vi.mock('@/lib/design/fonts', () => ({ fontVariables: 'font-sans-var font-mono-var' }))

import RootLayout, { metadata } from '@/app/layout'

describe('the root layout', () => {
  test('puts both Signal font variables on the document and keeps English as the language', () => {
    const markup = renderToStaticMarkup(
      <RootLayout>
        <p>Studio</p>
      </RootLayout>,
    )

    expect(markup).toContain('<html lang="en" class="font-sans-var font-mono-var">')
    expect(markup).toContain('<p>Studio</p>')
  })

  test('describes the product in plain words', () => {
    expect(metadata.description).toBe('Turn one long video into short clips worth posting.')
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pnpm --dir frontend exec vitest run tests/root-layout.test.tsx`
Expected: FAIL — the class attribute is absent and the description differs.

- [ ] **Step 3: Vendor the font files**

Run from the repository root:

```bash
COMMIT=$(git ls-remote https://github.com/google/fonts HEAD | cut -f1)
BASE="https://raw.githubusercontent.com/google/fonts/$COMMIT/ofl"
mkdir -p frontend/lib/design/fonts
curl -fL "$BASE/archivo/Archivo%5Bwdth,wght%5D.ttf" -o frontend/lib/design/fonts/Archivo-Variable.ttf
curl -fL "$BASE/archivo/OFL.txt" -o frontend/lib/design/fonts/OFL-Archivo.txt
curl -fL "$BASE/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf" -o frontend/lib/design/fonts/JetBrainsMono-Variable.ttf
curl -fL "$BASE/jetbrainsmono/OFL.txt" -o frontend/lib/design/fonts/OFL-JetBrainsMono.txt
shasum -a 256 frontend/lib/design/fonts/*.ttf
echo "$COMMIT"
```

Expected: both downloads succeed and two digests print. If a path 404s, list the family directory with `curl -fsSL "https://api.github.com/repos/google/fonts/contents/ofl/archivo?ref=$COMMIT"` and use the variable `.ttf` it names.

Write `frontend/lib/design/fonts/SOURCES.md` with the commit, each source URL, each SHA-256 digest from the command output, and the licence (SIL Open Font License 1.1, included beside each file).

- [ ] **Step 4: Write the loaders**

`frontend/lib/design/fonts.ts`:

```ts
import localFont from 'next/font/local'

/**
 * Signal's two faces, served from the repository rather than fetched at build time, so a
 * production build is reproducible offline and every environment draws the same glyphs.
 */
const archivo = localFont({
  src: './fonts/Archivo-Variable.ttf',
  variable: '--font-sans',
  display: 'swap',
  weight: '100 900',
  style: 'normal',
  declarations: [{ prop: 'font-stretch', value: '62% 125%' }],
})

const jetbrainsMono = localFont({
  src: './fonts/JetBrainsMono-Variable.ttf',
  variable: '--font-mono',
  display: 'swap',
  weight: '100 800',
  style: 'normal',
})

/** Both font variables, for the root element. */
export const fontVariables = `${archivo.variable} ${jetbrainsMono.variable}`
```

- [ ] **Step 5: Apply them in `frontend/app/layout.tsx`**

```tsx
import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import { Providers } from '@/app/providers'
import { fontVariables } from '@/lib/design/fonts'

import './globals.css'

export const metadata: Metadata = {
  title: 'Clipah',
  description: 'Turn one long video into short clips worth posting.',
}

/** The single document shell: Signal's fonts, global styles, and the data providers. */
export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className={fontVariables}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
```

- [ ] **Step 6: Run the layout test and the build**

Run: `pnpm --dir frontend exec vitest run tests/root-layout.test.tsx` → PASS.
Run: `pnpm build` → PASS. Confirm `.next/static/media` contains two `.ttf`-derived font files.

---

### Task 4: Remove light-theme machinery and add the design-rules test

**Files:**
- Delete: `frontend/components/theme-provider.tsx`, `frontend/components/ui/sidebar.tsx`, `frontend/components/ui/chart.tsx`, `frontend/hooks/use-mobile.tsx` (only if `grep -rn "use-mobile" frontend/app frontend/components frontend/features` finds no other importer)
- Modify: `frontend/components/ui/sonner.tsx`, `frontend/components/ui/alert.tsx`, `frontend/package.json`, `pnpm-lock.yaml`
- Create: `frontend/tests/design-rules.test.ts`

**Interfaces:**
- Produces: `PENDING_REDESIGN` allowlist inside the test. Plan 3 removes `components/media-card.tsx`; Plan 6 removes `app/page.tsx`, `app/demo/page.tsx`, `features/assets/AssetBrowser.tsx`, and `features/templates/TemplateLibrary.tsx`. When the list is empty, the test enforces the rule everywhere.

- [ ] **Step 1: Write the failing rules test**

`frontend/tests/design-rules.test.ts`:

```ts
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'

import { describe, expect, test } from 'vitest'

const ROOT = resolve(__dirname, '..')
const SCANNED = ['app', 'components', 'features', 'lib']
const SKIPPED = ['lib/api/generated']

/** Files that still carry a banned pattern and are rebuilt by a named later plan. */
const PENDING_REDESIGN: Record<string, string> = {
  'app/page.tsx': 'Plan 6 rebuilds the landing page',
  'app/demo/page.tsx': 'Plan 6 rebuilds the demo page',
  'components/media-card.tsx': 'Plan 3 replaces the placeholder with Poster',
  'features/assets/AssetBrowser.tsx': 'Plan 6 rebuilds the asset browser on Poster',
  'features/templates/TemplateLibrary.tsx': 'Plan 6 rebuilds template previews as look cards',
}

const BANNED: Array<[string, RegExp]> = [
  ['light/dark theme switching', /next-themes|\bdark:[a-z[]/],
  ['HSL token wiring', /hsl\(var\(/],
  ['violet, indigo, purple, or fuchsia colour classes', /\b(?:bg|text|from|via|to|border|ring|fill|stroke|shadow)-(?:violet|indigo|purple|fuchsia)-\d{2,3}\b/],
  ['gradient imagery', /\bbg-gradient-to-/],
]

function sourceFiles(): string[] {
  const files: string[] = []
  const walk = (directory: string) => {
    for (const entry of readdirSync(directory)) {
      const path = join(directory, entry)
      const rel = relative(ROOT, path)
      if (SKIPPED.some((skip) => rel.startsWith(skip))) continue
      if (statSync(path).isDirectory()) walk(path)
      else if (/\.(ts|tsx|css)$/.test(entry)) files.push(rel)
    }
  }
  SCANNED.forEach((directory) => walk(join(ROOT, directory)))
  return files
}

describe('Signal design rules', () => {
  test.each(BANNED)('no source file uses %s', (_name, pattern) => {
    const offenders = sourceFiles().filter(
      (file) => !(file in PENDING_REDESIGN) && pattern.test(readFileSync(join(ROOT, file), 'utf8')),
    )
    expect(offenders).toEqual([])
  })

  test('every pending file still needs its redesign, so the allowlist cannot go stale', () => {
    for (const file of Object.keys(PENDING_REDESIGN)) {
      const text = readFileSync(join(ROOT, file), 'utf8')
      expect(BANNED.some(([, pattern]) => pattern.test(text)), file).toBe(true)
    }
  })

  test('the frontend no longer depends on a theme switcher', () => {
    const manifest = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8')) as {
      dependencies: Record<string, string>
    }
    expect(manifest.dependencies['next-themes']).toBeUndefined()
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pnpm --dir frontend exec vitest run tests/design-rules.test.ts`
Expected: FAIL — offenders include `components/theme-provider.tsx`, `components/ui/sonner.tsx`, `components/ui/alert.tsx`, `components/ui/chart.tsx`, `components/ui/sidebar.tsx`, and the dependency test fails.

- [ ] **Step 3: Delete the unused theme and vendor files**

Run:

```bash
grep -rn "theme-provider\|components/ui/sidebar\|components/ui/chart\|use-mobile" frontend/app frontend/components frontend/features frontend/tests
```

Expected: matches only inside the files being deleted. Then delete `frontend/components/theme-provider.tsx`, `frontend/components/ui/sidebar.tsx`, `frontend/components/ui/chart.tsx`, and `frontend/hooks/use-mobile.tsx`.

- [ ] **Step 4: Rewrite `frontend/components/ui/sonner.tsx` without a theme hook**

```tsx
'use client'

import { Toaster as Sonner } from 'sonner'

type ToasterProps = React.ComponentProps<typeof Sonner>

/** Signal's toasts: dark, bottom centre, on the overlay surface. */
export function Toaster(props: ToasterProps) {
  return (
    <Sonner
      theme="dark"
      position="bottom-center"
      toastOptions={{
        classNames: {
          toast:
            'rounded-lg border border-line-strong bg-popover text-popover-foreground text-small shadow-xl',
          description: 'text-muted-foreground',
          actionButton: 'rounded-md bg-primary text-primary-foreground font-medium',
          cancelButton: 'rounded-md bg-secondary text-foreground',
        },
      }}
      {...props}
    />
  )
}
```

- [ ] **Step 5: Remove the `dark:` variant from `frontend/components/ui/alert.tsx`**

Replace `"border-destructive/50 text-destructive dark:border-destructive [&>svg]:text-destructive"` with `"border-destructive/50 bg-destructive-soft text-destructive [&>svg]:text-destructive"`.

- [ ] **Step 6: Drop the dependency**

Run from the repository root: `pnpm --filter clipah-frontend remove next-themes`
Expected: `package.json` and `pnpm-lock.yaml` no longer mention `next-themes`.

- [ ] **Step 7: Run the rules test and the suite**

Run: `pnpm --dir frontend exec vitest run tests/design-rules.test.ts` → PASS.
Run: `pnpm typecheck && pnpm test` → PASS.

---

### Task 5: Signal primitives

**Files:**
- Modify: `frontend/components/ui/button.tsx`, `frontend/components/ui/switch.tsx`, `frontend/components/ui/input.tsx`, `frontend/components/ui/tooltip.tsx`, `frontend/components/ui/skeleton.tsx`, `frontend/components/ui/dialog.tsx`, `frontend/components/ui/sheet.tsx`
- Replace: `frontend/components/ui/select.tsx` (the unused Radix select becomes a native wrapper), `frontend/components/ui/checkbox.tsx`
- Create: `frontend/components/ui/icon-button.tsx`, `frontend/components/ui/radio.tsx`, `frontend/components/ui/slider.tsx` (replace the unused Radix slider), `frontend/components/ui/segmented-control.tsx`
- Create: `frontend/tests/ui-primitives.test.tsx`

**Interfaces:**
- Produces:
  - `Button` props: `variant?: 'default' | 'primary' | 'secondary' | 'outline' | 'ghost' | 'destructive' | 'link'` (`default` and `primary` are the lime fill; `outline` renders like `secondary` for existing callers), `size?: 'sm' | 'default' | 'lg' | 'icon'`, `loading?: boolean`, `asChild?: boolean`.
  - `IconButton` props: `label: string`, `icon: ReactNode`, `shortcut?: string`, `variant?: 'ghost' | 'secondary' | 'primary'`, `size?: 'sm' | 'md'`, `tooltipSide?: 'top' | 'right' | 'bottom' | 'left'`, plus button attributes.
  - `Select` props: native `<select>` attributes plus `controlSize?: 'sm' | 'md'` and `wrapperClassName?: string`.
  - `Checkbox`, `Radio`: native input attributes without `type`.
  - `Slider`: native range input attributes without `type`.
  - `SegmentedControl<T extends string>` props: `label: string`, `value: T | null`, `options: ReadonlyArray<{ value: T; label: ReactNode; accessibleName?: string }>`, `onChange: (value: T) => void`, `size?: 'sm' | 'md'`, `className?: string`.

- [ ] **Step 1: Write the failing primitive tests**

`frontend/tests/ui-primitives.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Undo2 } from 'lucide-react'
import { describe, expect, test, vi } from 'vitest'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { IconButton } from '@/components/ui/icon-button'
import { Radio } from '@/components/ui/radio'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Select } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'

describe('Signal primitives', () => {
  test('Select stays a native, labelled select that selectOptions can drive', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <label>
        Sort clips
        <Select defaultValue="rank" onChange={(event) => onChange(event.target.value)}>
          <option value="rank">Ranked</option>
          <option value="score">Highest score</option>
        </Select>
      </label>,
    )

    await user.selectOptions(screen.getByRole('combobox', { name: 'Sort clips' }), 'score')

    expect(onChange).toHaveBeenCalledWith('score')
  })

  test('Checkbox and Radio are native inputs with Signal skins', async () => {
    const user = userEvent.setup()
    render(
      <>
        <label>
          <Checkbox /> Snap to edges
        </label>
        <label>
          <Radio name="preset" value="portrait" /> Vertical
        </label>
      </>,
    )

    await user.click(screen.getByRole('checkbox', { name: 'Snap to edges' }))
    await user.click(screen.getByRole('radio', { name: 'Vertical' }))

    expect(screen.getByRole('checkbox', { name: 'Snap to edges' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Snap to edges' })).toHaveClass('signal-checkbox')
    expect(screen.getByRole('radio', { name: 'Vertical' })).toBeChecked()
  })

  test('Slider is a native range the keyboard can move', () => {
    render(<Slider aria-label="Volume" min={0} max={100} defaultValue={40} />)

    expect(screen.getByRole('slider', { name: 'Volume' })).toHaveValue('40')
  })

  test('SegmentedControl marks exactly one pressed option and reports a choice', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <SegmentedControl
        label="Canvas shape"
        value="9:16"
        options={[
          { value: '9:16', label: '9:16' },
          { value: '1:1', label: '1:1' },
        ]}
        onChange={onChange}
      />,
    )

    expect(screen.getByRole('group', { name: 'Canvas shape' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '9:16' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '1:1' })).toHaveAttribute('aria-pressed', 'false')

    await user.click(screen.getByRole('button', { name: '1:1' }))

    expect(onChange).toHaveBeenCalledWith('1:1')
  })

  test('IconButton is named by its label and explains itself with its shortcut on focus', async () => {
    const user = userEvent.setup()
    render(<IconButton label="Undo" shortcut="⌘Z" icon={<Undo2 />} />)

    await user.tab()

    expect(screen.getByRole('button', { name: 'Undo' })).toHaveFocus()
    expect(await screen.findByRole('tooltip')).toHaveTextContent('Undo⌘Z')
  })

  test('a loading Button keeps its label for width and says it is busy', () => {
    render(<Button loading>Export</Button>)

    const button = screen.getByRole('button', { name: /export/i })
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(button).toBeDisabled()
  })
})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/ui-primitives.test.tsx`
Expected: FAIL — missing modules `icon-button`, `radio`, `segmented-control`; `Select` is the Radix root.

- [ ] **Step 3: Rewrite `frontend/components/ui/button.tsx`**

```tsx
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { Loader2 } from 'lucide-react'
import * as React from 'react'

import { cn } from '@/lib/utils'

export const buttonVariants = cva(
  'relative inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-small font-semibold transition-colors duration-fast ease-signal disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-primary-hover',
        primary: 'bg-primary text-primary-foreground hover:bg-primary-hover',
        secondary: 'border border-line-strong bg-secondary text-foreground hover:border-input',
        outline: 'border border-line-strong bg-secondary text-foreground hover:border-input',
        ghost: 'text-muted-foreground hover:bg-secondary hover:text-foreground',
        destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
        link: 'h-auto px-0 text-primary underline-offset-4 hover:underline',
      },
      size: {
        sm: 'h-8 px-3',
        default: 'h-9 px-4',
        lg: 'h-11 px-5 text-body',
        icon: 'size-9',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
  /** Show work in progress without changing the button's width or label. */
  loading?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, loading = false, disabled, children, ...props }, ref) => {
    if (asChild) {
      return (
        <Slot ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props}>
          {children}
        </Slot>
      )
    }
    return (
      <button
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        disabled={disabled === true || loading}
        aria-busy={loading || undefined}
        {...props}
      >
        {loading ? (
          <>
            <span className="invisible inline-flex items-center gap-2">{children}</span>
            <Loader2 aria-hidden="true" className="absolute animate-spin" strokeWidth={1.75} />
          </>
        ) : (
          children
        )}
      </button>
    )
  },
)
Button.displayName = 'Button'
```

- [ ] **Step 4: Restyle `frontend/components/ui/tooltip.tsx`**

Keep the file's exports and replace the `TooltipContent` class list with:

```ts
'z-50 overflow-hidden rounded-md border border-line-strong bg-popover px-2 py-1 text-caption font-medium text-popover-foreground shadow-lg data-[state=delayed-open]:animate-in data-[state=delayed-open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0'
```

- [ ] **Step 5: Create `frontend/components/ui/icon-button.tsx`**

```tsx
'use client'

import { cva } from 'class-variance-authority'
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

const iconButtonVariants = cva(
  'inline-flex shrink-0 items-center justify-center rounded-md transition-colors duration-fast ease-signal disabled:pointer-events-none disabled:opacity-40 [&_svg]:size-4',
  {
    variants: {
      variant: {
        ghost: 'text-muted-foreground hover:bg-secondary hover:text-foreground aria-pressed:text-primary',
        secondary: 'border border-line-strong bg-secondary text-foreground hover:border-input aria-pressed:text-primary',
        primary: 'bg-primary text-primary-foreground hover:bg-primary-hover',
      },
      size: { sm: 'size-8', md: 'size-9' },
    },
    defaultVariants: { variant: 'ghost', size: 'md' },
  },
)

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  /** The accessible name, also shown in the tooltip. */
  label: string
  icon: ReactNode
  /** The keyboard shortcut as a member reads it, for example `⌘Z`. */
  shortcut?: string
  variant?: 'ghost' | 'secondary' | 'primary'
  size?: 'sm' | 'md'
  tooltipSide?: 'top' | 'right' | 'bottom' | 'left'
}

/**
 * A square button that is only an icon, and therefore always carries a name.
 *
 * The tooltip repeats the name and adds the shortcut, so a member learns the keyboard from
 * the pointer. It brings its own provider, so it works outside the application shell too.
 */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, shortcut, variant, size, tooltipSide = 'bottom', className, type = 'button', ...props },
  ref,
) {
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            ref={ref}
            type={type}
            aria-label={label}
            className={cn(iconButtonVariants({ variant, size }), className)}
            {...props}
          >
            {icon}
          </button>
        </TooltipTrigger>
        <TooltipContent side={tooltipSide}>
          <span>{label}</span>
          {shortcut === undefined ? null : (
            <kbd className="ml-2 font-mono text-caption text-subtle-foreground">{shortcut}</kbd>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
})
```

- [ ] **Step 6: Replace `frontend/components/ui/select.tsx`**

```tsx
import { ChevronDown } from 'lucide-react'
import { forwardRef, type SelectHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  controlSize?: 'sm' | 'md'
  wrapperClassName?: string
}

/**
 * A native select in Signal's skin.
 *
 * Native on purpose: phones open their own picker, forms and labels work unaided, and the
 * operating system draws the option list in the page's dark colour scheme.
 */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, wrapperClassName, controlSize = 'md', children, ...props },
  ref,
) {
  return (
    <span className={cn('relative inline-flex min-w-0', wrapperClassName)}>
      <select
        ref={ref}
        className={cn(
          'w-full min-w-0 appearance-none truncate rounded-md border border-input bg-secondary pl-3 pr-8 text-small text-foreground transition-colors duration-fast ease-signal hover:border-foreground/60 disabled:cursor-not-allowed disabled:opacity-50',
          controlSize === 'sm' ? 'h-8' : 'h-9',
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden="true"
        strokeWidth={1.75}
        className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
      />
    </span>
  )
})
```

- [ ] **Step 7: Replace `checkbox.tsx`, create `radio.tsx`, replace `slider.tsx`**

`frontend/components/ui/checkbox.tsx`:

```tsx
import { forwardRef, type InputHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

/** A native checkbox in Signal's skin; checked is a lime square with a graphite tick. */
export const Checkbox = forwardRef<HTMLInputElement, Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>>(
  function Checkbox({ className, ...props }, ref) {
    return <input ref={ref} type="checkbox" className={cn('signal-checkbox', className)} {...props} />
  },
)
```

`frontend/components/ui/radio.tsx`:

```tsx
import { forwardRef, type InputHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

/** A native radio button in Signal's skin. */
export const Radio = forwardRef<HTMLInputElement, Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>>(
  function Radio({ className, ...props }, ref) {
    return <input ref={ref} type="radio" className={cn('signal-radio', className)} {...props} />
  },
)
```

`frontend/components/ui/slider.tsx`:

```tsx
import { forwardRef, type InputHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

/** A native range input in Signal's skin; the keyboard and screen readers get it for free. */
export const Slider = forwardRef<HTMLInputElement, Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>>(
  function Slider({ className, ...props }, ref) {
    return <input ref={ref} type="range" className={cn('signal-range w-full', className)} {...props} />
  },
)
```

Before replacing, confirm nothing imports the Radix versions: `grep -rn "components/ui/checkbox\|components/ui/slider\|components/ui/select" frontend/app frontend/components frontend/features` → no matches.

- [ ] **Step 8: Create `frontend/components/ui/segmented-control.tsx`**

```tsx
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  /** Needed when `label` is an icon. */
  accessibleName?: string
}

/**
 * A small set of mutually exclusive choices, shown all at once.
 *
 * Each choice is a button with `aria-pressed`, so every option is one Tab away and a test
 * or a screen reader finds the chosen one by its pressed state. The chosen option is
 * marked with lime text on the raised surface, never a second lime fill.
 */
export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
  size = 'md',
  className,
}: {
  label: string
  value: T | null
  options: ReadonlyArray<SegmentedOption<T>>
  onChange: (value: T) => void
  size?: 'sm' | 'md'
  className?: string
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn('inline-flex items-center gap-0.5 rounded-md border border-border bg-background p-0.5', className)}
    >
      {options.map((option) => {
        const pressed = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={pressed}
            aria-label={option.accessibleName}
            onClick={() => onChange(option.value)}
            className={cn(
              'inline-flex items-center justify-center gap-1.5 rounded-[3px] px-2.5 font-medium transition-colors duration-fast ease-signal [&_svg]:size-4',
              size === 'sm' ? 'h-7 text-caption' : 'h-8 text-small',
              pressed ? 'bg-secondary text-primary' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 9: Restyle the remaining Radix and vendor primitives**

Class-list changes only; exports and behaviour stay:

- `switch.tsx` root: `'peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border border-input transition-colors duration-fast ease-signal disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=unchecked]:bg-secondary'`; thumb: `'pointer-events-none block size-3.5 rounded-full bg-foreground transition-transform duration-fast ease-signal data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-primary-foreground data-[state=unchecked]:translate-x-0.5'`.
- `input.tsx`: `'flex h-9 w-full rounded-md border border-input bg-secondary px-3 text-small text-foreground placeholder:text-subtle-foreground transition-colors duration-fast ease-signal file:border-0 file:bg-transparent file:text-small file:font-medium disabled:cursor-not-allowed disabled:opacity-50'`.
- `skeleton.tsx`: `'rounded-md bg-secondary motion-safe:animate-pulse'`.
- `dialog.tsx` overlay: `'fixed inset-0 z-50 bg-background/80 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0'`; content: replace `bg-card p-6 shadow-xl duration-200 … sm:rounded-2xl` with `border-line-strong bg-popover p-6 shadow-2xl duration-dialog … sm:rounded-lg`; title: `text-h2 font-semibold`; description: `text-small text-muted-foreground`.
- `sheet.tsx` overlay as dialog; content: `bg-popover border-line-strong` with `duration-panel`.

- [ ] **Step 10: Run the primitive tests and the suite**

Run: `pnpm --dir frontend exec vitest run tests/ui-primitives.test.tsx` → PASS (6 tests).
Run: `pnpm test` → PASS.

---

### Task 6: One way to raise a toast

**Files:**
- Create: `frontend/lib/notify.ts`
- Create: `frontend/tests/notify.test.tsx`
- Modify: `frontend/app/providers.tsx`

**Interfaces:**
- Consumes: `Toaster` (Task 4), `ApiError` from `@/lib/api/client`.
- Produces: `notify.success(message: string, options?: NotifyOptions)`, `notify.info(message, options?)`, `notify.failure(error: unknown, fallback?: string)`, `type NotifyOptions = { description?: string; action?: { label: string; onClick: () => void } }`.

- [ ] **Step 1: Write the failing test**

`frontend/tests/notify.test.tsx`:

```tsx
import { act, render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'

import { Toaster } from '@/components/ui/sonner'
import { ApiError } from '@/lib/api/client'
import { notify } from '@/lib/notify'

describe('notify', () => {
  test('shows a confirmation with its action', async () => {
    render(<Toaster />)

    act(() => {
      notify.success('Export ready', { action: { label: 'Download', onClick: () => undefined } })
    })

    expect(await screen.findByText('Export ready')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument()
  })

  test('says what failed in the backend’s words and keeps the reference for support', async () => {
    render(<Toaster />)
    const error = new ApiError({
      status: 409,
      code: 'EDIT_REVISION_CONFLICT',
      message: 'This clip changed since you opened it.',
      requestId: 'request-77',
    })

    act(() => {
      notify.failure(error)
    })

    expect(await screen.findByText('This clip changed since you opened it.')).toBeInTheDocument()
    expect(screen.getByText('Ref request-77')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pnpm --dir frontend exec vitest run tests/notify.test.tsx`
Expected: FAIL — `Cannot find module '@/lib/notify'`.

- [ ] **Step 3: Implement `frontend/lib/notify.ts`**

```ts
import { toast } from 'sonner'

import { ApiError } from '@/lib/api/client'

export interface NotifyOptions {
  description?: string
  action?: { label: string; onClick: () => void }
}

const FALLBACK = 'Something went wrong. Try again.'

/**
 * The one way product code raises a toast.
 *
 * Failures show the backend's public message and keep the request reference in the
 * description, so a member can quote it without the sentence being about identifiers.
 */
export const notify = {
  success(message: string, options: NotifyOptions = {}): void {
    toast.success(message, options)
  },
  info(message: string, options: NotifyOptions = {}): void {
    toast(message, options)
  },
  failure(error: unknown, fallback: string = FALLBACK): void {
    if (error instanceof ApiError) {
      toast.error(error.message, {
        description: error.requestId === null ? undefined : `Ref ${error.requestId}`,
      })
      return
    }
    toast.error(fallback)
  },
}
```

- [ ] **Step 4: Mount the toaster in `frontend/app/providers.tsx`**

Add `import { Toaster } from '@/components/ui/sonner'` and render it after `{children}` inside `QueryClientProvider`:

```tsx
  return (
    <QueryClientProvider client={queryClient}>
      {children}
      <Toaster />
    </QueryClientProvider>
  )
```

- [ ] **Step 5: Run the test**

Run: `pnpm --dir frontend exec vitest run tests/notify.test.tsx` → PASS.

---

### Task 7: Move every native control onto the primitives

**Files (modify each):**
`features/assets/AssetBrowser.tsx`, `features/broll/BrollSuggestionCard.tsx`, `features/broll/CoverageControl.tsx`, `features/campaigns/CampaignPanel.tsx`, `features/clips/ClipBrowser.tsx`, `features/clips/ClipCard.tsx`, `features/clips/ClipList.tsx`, `features/clips/VariantLab.tsx`, `features/editor/AudioPanel.tsx`, `features/editor/CaptionsPanel.tsx`, `features/editor/ExportDialog.tsx`, `features/editor/KaraokePanel.tsx`, `features/editor/MotionPanel.tsx`, `features/editor/SourceMonitor.tsx`, `features/editor/TextPanel.tsx`, `features/editor/Timeline.tsx`, `features/editor/TimelineToolbar.tsx`, `features/publishing/DestinationPanel.tsx`, `features/publishing/PublicationComposer.tsx`, `features/search/GlobalSearch.tsx`, `features/settings/GeneralSettings.tsx`, `features/team/TeamSettings.tsx`, `features/templates/TemplateLibrary.tsx`, `features/uploads/YouTubeConnectionDialog.tsx`, `features/workspaces/workspace-switcher.tsx`, `components/field.tsx`.

**Interfaces:**
- Consumes: `Select`, `Checkbox`, `Radio`, `Slider` (Task 5).
- Produces: no native `<select>` or `<input type="checkbox|radio|range|color">` outside `components/ui/`. Labels, `aria-label`s, `id`s, `name`s, `value`s, and handlers are unchanged, so every existing test keeps passing.

- [ ] **Step 1: List the occurrences**

Run from `frontend/`:

```bash
grep -rn '<select\|type="checkbox"\|type="radio"\|type="range"\|type="color"' app components features --include='*.tsx' | grep -v components/ui
```

Expected: 17 files with `<select>` and 12 files with checkbox, radio, or range inputs (the list above). Keep the output; each line is ticked off in Step 3.

- [ ] **Step 2: Establish the safety net**

Run: `pnpm test`
Expected: PASS. This is the behaviour the migration must keep.

- [ ] **Step 3: Replace each occurrence mechanically**

For every `<select …>…</select>`: import `Select` from `@/components/ui/select`, rename the element to `Select`, keep every attribute, drop any `className` that only styled the control (`selectClassName`, `rounded border px-1 py-0.5`, `h-8 rounded-lg border bg-card px-2`), and keep layout-only classes (widths such as `w-56`) by moving them to `wrapperClassName`. Example from `features/clips/ClipList.tsx`:

```tsx
<Select
  aria-label="Filter by category"
  value={category}
  onChange={(event) => setCategory(event.target.value)}
  controlSize="sm"
>
  <option value="all">Every category</option>
  {Object.values(ClipCategory).map((value) => (
    <option key={value} value={value}>
      {CATEGORY_LABELS[value] ?? value}
    </option>
  ))}
</Select>
```

For every `<input type="checkbox" …/>`: import `Checkbox` from `@/components/ui/checkbox` and write `<Checkbox …/>` with the same attributes minus `type`. Example from `features/editor/TimelineToolbar.tsx`:

```tsx
<label className="flex items-center gap-1.5 text-caption text-muted-foreground">
  <Checkbox
    aria-label="Snap to edges"
    checked={snapping}
    onChange={(event) => onSnapping(event.currentTarget.checked)}
  />
  Snap
</label>
```

For every `<input type="radio" …/>`: `Radio` from `@/components/ui/radio`, same rule. In `features/editor/ExportDialog.tsx` the radio keeps `className="mt-1"`.

For every `<input type="range" …/>`: `Slider` from `@/components/ui/slider`, same rule (`Timeline.tsx` "Scrub the clip", `SourceMonitor.tsx` "Source position").

In `components/field.tsx`, delete `selectClassName` and restyle `inputClassName` to:

```ts
export const inputClassName =
  'h-9 w-full rounded-md border border-input bg-secondary px-3 text-small text-foreground placeholder:text-subtle-foreground disabled:opacity-60'
```

and restyle `Field`'s label to `block text-small font-medium` and help text to `text-caption text-muted-foreground`.

- [ ] **Step 4: Prove nothing is left**

Run the Step 1 grep again.
Expected: no output.

- [ ] **Step 5: Run the suite and typecheck**

Run: `pnpm typecheck && pnpm test`
Expected: PASS with the same test count as Step 2.

---

### Task 8: Lint rule that keeps native controls inside `components/ui`

**Files:**
- Modify: `frontend/eslint.config.mjs`

- [ ] **Step 1: Write a probe that must fail lint once the rule exists**

Create `frontend/features/lint-probe.tsx`:

```tsx
export function LintProbe() {
  return (
    <>
      <select aria-label="Probe" />
      <input type="checkbox" aria-label="Probe box" />
    </>
  )
}
```

- [ ] **Step 2: Run lint and observe that today it passes (the rule is missing)**

Run: `pnpm lint`
Expected: PASS — this is the failing condition for the rule.

- [ ] **Step 3: Add the rule**

Append a config object to the array in `frontend/eslint.config.mjs`:

```js
  {
    files: ['app/**/*.tsx', 'components/**/*.tsx', 'features/**/*.tsx'],
    ignores: ['components/ui/**'],
    rules: {
      // Native controls wear Signal's skin only through components/ui.
      'no-restricted-syntax': [
        'error',
        {
          selector: "JSXOpeningElement[name.name='select']",
          message: 'Use Select from @/components/ui/select.',
        },
        {
          selector:
            "JSXOpeningElement[name.name='input'] > JSXAttribute[name.name='type'][value.value=/^(checkbox|radio|range|color)$/]",
          message: 'Use Checkbox, Radio, or Slider from @/components/ui.',
        },
      ],
    },
  },
```

- [ ] **Step 4: Run lint and observe the probe fail**

Run: `pnpm lint`
Expected: FAIL with exactly two errors in `features/lint-probe.tsx`: "Use Select from @/components/ui/select." and "Use Checkbox, Radio, or Slider from @/components/ui."

- [ ] **Step 5: Delete the probe and confirm lint is green**

Run: `rm frontend/features/lint-probe.tsx && pnpm lint`
Expected: PASS.

---

### Task 9: Rebuild the shared page components

**Files:**
- Modify: `frontend/components/page-header.tsx`, `frontend/components/status-badge.tsx`, `frontend/components/empty-state.tsx`, `frontend/components/error-notice.tsx`, `frontend/components/loading-state.tsx`
- Move: `frontend/components/item-menu.tsx` → `frontend/components/ui/item-menu.tsx` (update the import in `features/projects/projects-panel.tsx`)
- Create: `frontend/tests/shared-components.test.tsx`
- Modify: `frontend/tests/smoke.test.tsx` only if an assertion depends on removed wording

**Interfaces:**
- Produces:
  - `PageHeader` (unchanged props: `title`, `description?`, `crumbs?`, `actions?`, `meta?`) — title rendered `h1.font-display.text-h1`.
  - `Section` (unchanged props).
  - `StatusBadge` props: `tone?: StatusTone`, `children`, `className?`, `appearance?: 'plain' | 'overlay'`.
  - `StatusDot` props: `tone: StatusTone`, `className?` — decorative, `aria-hidden`.
  - `EmptyState` (unchanged props; `icon` renders bare).
  - `ErrorNotice` props: `error: unknown`, `onRetry?: () => void`.
  - `LoadingState` (unchanged props).
  - `ItemMenu` (unchanged props) at `@/components/ui/item-menu`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/shared-components.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Film } from 'lucide-react'
import { describe, expect, test, vi } from 'vitest'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { ApiError } from '@/lib/api/client'

vi.mock('@/lib/notify', () => ({ notify: { success: vi.fn(), info: vi.fn(), failure: vi.fn() } }))

import { notify } from '@/lib/notify'

describe('shared page components', () => {
  test('a page title is the one level-one heading, set in display type', () => {
    render(<PageHeader title="Projects" description="Every video you brought in." />)

    const heading = screen.getByRole('heading', { level: 1, name: 'Projects' })
    expect(heading).toHaveClass('font-display', 'text-h1')
  })

  test('a status is words with a decorative dot, never colour alone', () => {
    render(<StatusBadge tone="success">Ready to review</StatusBadge>)

    const status = screen.getByText('Ready to review')
    expect(status).toBeInTheDocument()
    expect(status.querySelector('[aria-hidden="true"]')).not.toBeNull()
  })

  test('an empty state is a left-aligned invitation with no tinted icon square', () => {
    const { container } = render(
      <EmptyState icon={Film} title="Drop a long video to start" description="Clipah finds the moments." />,
    )

    expect(screen.getByText('Drop a long video to start')).toBeInTheDocument()
    expect(container.firstElementChild).not.toHaveClass('text-center')
    expect(container.querySelector('.rounded-full')).toBeNull()
  })

  test('an error keeps the reference out of the sentence and copies it on request', async () => {
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    const error = new ApiError({
      status: 503,
      code: 'SERVICE_UNAVAILABLE',
      message: 'A required service is unavailable.',
      requestId: 'request-1234',
    })
    const onRetry = vi.fn()

    render(<ErrorNotice error={error} onRetry={onRetry} />)

    expect(screen.getByRole('alert')).toHaveTextContent('A required service is unavailable.')
    expect(screen.getByText('Ref request-1234')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Copy details' }))
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(writeText).toHaveBeenCalledWith('SERVICE_UNAVAILABLE request-1234')
    expect(notify.success).toHaveBeenCalledWith('Details copied')
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/shared-components.test.tsx`
Expected: FAIL — heading classes, `text-center`, the tinted icon span, and the "Copy details" button.

- [ ] **Step 3: Rebuild `PageHeader` and `Section`**

In `frontend/components/page-header.tsx` keep the structure and props; change classes:

- header: `flex flex-col gap-4 pb-6 sm:flex-row sm:items-end sm:justify-between`
- breadcrumb list: `flex flex-wrap items-center gap-1 text-caption font-medium uppercase tracking-wide text-subtle-foreground` and link hover `hover:text-foreground`
- title: `<h1 className="font-display truncate text-h1">`
- description: `max-w-2xl text-small text-muted-foreground`
- `Section` heading: `text-title` and description `text-small text-muted-foreground`.

- [ ] **Step 4: Rebuild `StatusBadge` and add `StatusDot`**

`frontend/components/status-badge.tsx`:

```tsx
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** What a status means for the creator, which decides how loudly it is shown. */
export type StatusTone = 'neutral' | 'progress' | 'success' | 'attention' | 'danger' | 'accent'

const DOTS: Record<StatusTone, string> = {
  neutral: 'bg-subtle-foreground',
  progress: 'bg-muted-foreground motion-safe:animate-pulse',
  success: 'bg-primary',
  attention: 'bg-warning',
  danger: 'bg-destructive',
  accent: 'bg-primary',
}

/** The decorative dot that helps a creator scan a grid; the words beside it carry the meaning. */
export function StatusDot({ tone, className }: { tone: StatusTone; className?: string }) {
  return <span aria-hidden="true" className={cn('size-1.5 shrink-0 rounded-full', DOTS[tone], className)} />
}

/**
 * A short, readable state label with its dot.
 *
 * `overlay` sets the label on a solid graphite plate so it stays legible over a picture.
 */
export function StatusBadge({
  tone = 'neutral',
  appearance = 'plain',
  children,
  className,
}: {
  tone?: StatusTone
  appearance?: 'plain' | 'overlay'
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 text-caption font-medium',
        tone === 'danger' ? 'text-destructive' : tone === 'attention' ? 'text-warning' : 'text-muted-foreground',
        appearance === 'overlay' && 'rounded-sm bg-background/85 px-1.5 py-0.5 text-foreground',
        className,
      )}
    >
      <StatusDot tone={tone} />
      {children}
    </span>
  )
}
```

- [ ] **Step 5: Rebuild `EmptyState`**

```tsx
import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

/**
 * Say what belongs here and how to fill it, as an invitation rather than an apology.
 *
 * Left-aligned and as wide as its region, so an empty grid still reads as the grid it will
 * become. An icon, when given, is drawn bare beside the title.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  compact = false,
}: {
  icon?: LucideIcon
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  compact?: boolean
}) {
  return (
    <div
      className={`flex flex-col items-start rounded-lg border border-dashed border-line-strong bg-card/40 ${
        compact ? 'gap-2 px-4 py-5' : 'gap-3 px-6 py-8'
      }`}
    >
      <p className="flex items-center gap-2 text-title">
        {Icon === undefined ? null : (
          <Icon aria-hidden="true" strokeWidth={1.75} className="size-5 text-muted-foreground" />
        )}
        {title}
      </p>
      {description === undefined ? null : (
        <p className="max-w-xl text-small text-muted-foreground">{description}</p>
      )}
      {action === undefined ? null : <div className="pt-1">{action}</div>}
    </div>
  )
}
```

- [ ] **Step 6: Rebuild `ErrorNotice`**

```tsx
'use client'

import { AlertCircle } from 'lucide-react'

import { ApiError } from '@/lib/api/client'
import { notify } from '@/lib/notify'

/**
 * A failed request, said plainly, with its support reference one click away.
 *
 * The public message is the sentence; the code and request identifier sit in small mono
 * type and are copied together, which is what support needs to find the log entry.
 * Everything is rendered as React children, so a message containing markup stays text.
 */
export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof ApiError ? error.message : 'Something went wrong. Try again.'
  const reference =
    error instanceof ApiError && error.requestId ? `${error.code} ${error.requestId}` : null

  async function copy(): Promise<void> {
    if (reference === null) return
    try {
      await navigator.clipboard.writeText(reference)
      notify.success('Details copied')
    } catch {
      notify.info(reference)
    }
  }

  return (
    <div role="alert" className="flex gap-3 rounded-lg border border-destructive/40 bg-destructive-soft p-4">
      <AlertCircle aria-hidden="true" strokeWidth={1.75} className="mt-0.5 size-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1 space-y-2">
        <p className="text-small font-medium text-foreground">{message}</p>
        <div className="flex flex-wrap items-center gap-3">
          {onRetry === undefined ? null : (
            <button type="button" onClick={onRetry} className="text-small font-semibold text-primary hover:underline">
              Try again
            </button>
          )}
          {reference === null ? null : (
            <>
              <button
                type="button"
                onClick={() => void copy()}
                className="text-small font-medium text-muted-foreground hover:text-foreground"
              >
                Copy details
              </button>
              <p className="font-mono text-caption text-subtle-foreground">
                Ref {error instanceof ApiError ? error.requestId : ''}
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
```

Check that `tests/smoke.test.tsx` "shows the public message and the request identifier" still passes: it matches the identifier with a regular expression, which `Ref 018f3d1c-…` satisfies. Search for other assertions on the old wording: `grep -rn "Request ID" frontend/tests frontend/e2e` — update any match to `Ref <id>`.

- [ ] **Step 7: Restyle `LoadingState` and move `ItemMenu`**

In `loading-state.tsx` replace `surface` card skeletons with `rounded-lg border bg-card`, `bg-muted animate-pulse` with `bg-secondary motion-safe:animate-pulse`, and the inline label class with `text-small text-muted-foreground`.

Move the file: `git mv frontend/components/item-menu.tsx frontend/components/ui/item-menu.tsx`, update its trigger class to `flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground`, its panel to `absolute right-0 top-9 z-30 min-w-44 rounded-lg border border-line-strong bg-popover p-1 shadow-xl`, its items to `block w-full rounded-md px-3 py-2 text-left text-small hover:bg-secondary disabled:opacity-50` with `text-destructive` for destructive items, and change the import in `features/projects/projects-panel.tsx` to `@/components/ui/item-menu`.

- [ ] **Step 8: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/shared-components.test.tsx` → PASS.
Run: `pnpm test` → PASS. If a feature test asserted a removed class or the old "Request ID:" text, update that assertion to the new visible text only.

---

### Task 10: The shell — rail, top bar, account menu, phone tabs

**Files:**
- Create: `frontend/components/shell/navigation.ts`, `frontend/components/shell/wordmark.tsx`, `frontend/components/shell/rail.tsx`, `frontend/components/shell/top-bar.tsx`, `frontend/components/shell/account-menu.tsx`, `frontend/components/shell/phone-tabs.tsx`
- Modify: `frontend/components/dashboard-shell.tsx` (compose the pieces), `frontend/components/dashboard-frame.tsx`, `frontend/features/projects/new-project.tsx` (`NewProjectButton` appearance), `frontend/features/workspaces/workspace-switcher.tsx`
- Create: `frontend/tests/shell.test.tsx`
- Modify: `frontend/tests/projects.test.tsx` ("marks the navigation entry", "keeps its navigation reachable"), `frontend/tests/smoke.test.tsx` ("dashboard shell")

**Interfaces:**
- Produces:
  - `navigation.ts`: `PRIMARY_NAVIGATION`, `LIBRARY_NAVIGATION`, `SETTINGS_ENTRY` (`{ href: string; label: string; icon: LucideIcon }`), `isCurrent(pathname: string | null, href: string): boolean`, `RAIL_PINNED_KEY = 'clipah.rail.pinned'`, `readRailPinned(): boolean`, `writeRailPinned(pinned: boolean): void`.
  - `DashboardShell` props: `user`, `workspaceSwitcher`, `jobCenter`, `newProject?`, `activeJobCount?`, `onSignOut?`, `onOpenCommandPalette?: () => void`, `onDropFile?: (file: File) => void`, `children`.
  - `NewProjectButton` props gain `appearance?: 'button' | 'rail'`.

- [ ] **Step 1: Write the failing shell tests**

`frontend/tests/shell.test.tsx`:

```tsx
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { DashboardShell } from '@/components/dashboard-shell'
import { RAIL_PINNED_KEY } from '@/components/shell/navigation'

import { renderWithApi } from './support/api'
import { currentUser } from './support/fixtures'

const pathname = vi.hoisted(() => ({ current: '/dashboard/clips' }))
vi.mock('next/navigation', () => ({ usePathname: () => pathname.current }))

beforeEach(() => {
  pathname.current = '/dashboard/clips'
  window.localStorage.clear()
})

function shell(props: Partial<Parameters<typeof DashboardShell>[0]> = {}) {
  return renderWithApi(
    <DashboardShell user={currentUser()} workspaceSwitcher={<p>Workspace</p>} jobCenter={<p>Jobs</p>} {...props}>
      <p>Body</p>
    </DashboardShell>,
  )
}

describe('the Signal shell', () => {
  test('the rail names every destination and marks the current one', () => {
    shell()

    const rail = screen.getByRole('navigation', { name: 'Workspace' })
    for (const name of ['Home', 'Projects', 'Clips', 'Publishing', 'Settings']) {
      expect(within(rail).getByRole('link', { name })).toBeInTheDocument()
    }
    expect(within(rail).getByRole('link', { name: 'Clips' })).toHaveAttribute('aria-current', 'page')
  })

  test('the library opens from the rail', async () => {
    const user = userEvent.setup()
    shell()
    const rail = screen.getByRole('navigation', { name: 'Workspace' })

    await user.click(within(rail).getByRole('button', { name: 'Library' }))

    expect(within(rail).getByRole('link', { name: 'Assets' })).toHaveAttribute('href', '/dashboard/assets')
    expect(within(rail).getByRole('link', { name: 'Templates' })).toBeVisible()
    expect(within(rail).getByRole('link', { name: 'Brand kits' })).toBeVisible()
  })

  test('pinning the rail is remembered for this browser', async () => {
    const user = userEvent.setup()
    shell()

    await user.click(screen.getByRole('button', { name: 'Pin navigation' }))

    expect(screen.getByRole('button', { name: 'Collapse navigation' })).toHaveAttribute('aria-pressed', 'true')
    expect(window.localStorage.getItem(RAIL_PINNED_KEY)).toBe('true')
  })

  test('a broken storage does not break the rail', async () => {
    const user = userEvent.setup()
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    shell()

    await user.click(screen.getByRole('button', { name: 'Pin navigation' }))

    expect(screen.getByRole('button', { name: 'Collapse navigation' })).toBeInTheDocument()
  })

  test('the top bar opens the command palette from its search trigger', async () => {
    const user = userEvent.setup()
    const onOpenCommandPalette = vi.fn()
    shell({ onOpenCommandPalette })

    await user.click(screen.getByRole('button', { name: /search or jump to/i }))

    expect(onOpenCommandPalette).toHaveBeenCalledTimes(1)
  })

  test('without a palette the top bar keeps the plain search form', () => {
    shell()

    expect(screen.getByRole('search')).toHaveAttribute('action', '/dashboard/search')
  })

  test('phones get a labelled tab bar and the full navigation behind one control', async () => {
    const user = userEvent.setup()
    shell()

    const tabs = screen.getByRole('navigation', { name: 'Primary' })
    expect(within(tabs).getByRole('link', { name: 'Projects' })).toHaveAttribute('href', '/dashboard/projects')

    const toggle = within(tabs).getByRole('button', { name: 'Navigation' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })
})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/shell.test.tsx`
Expected: FAIL — `Cannot find module '@/components/shell/navigation'`.

- [ ] **Step 3: Write `frontend/components/shell/navigation.ts`**

```ts
import {
  Clapperboard,
  FolderOpen,
  Home,
  Image as ImageIcon,
  LayoutTemplate,
  Palette,
  Send,
  Settings,
  type LucideIcon,
} from 'lucide-react'

/** One destination in the Workspace navigation. */
export interface NavigationEntry {
  href: string
  label: string
  icon: LucideIcon
}

export const PRIMARY_NAVIGATION: NavigationEntry[] = [
  { href: '/dashboard', label: 'Home', icon: Home },
  { href: '/dashboard/projects', label: 'Projects', icon: FolderOpen },
  { href: '/dashboard/clips', label: 'Clips', icon: Clapperboard },
  { href: '/dashboard/publishing', label: 'Publishing', icon: Send },
]

export const LIBRARY_NAVIGATION: NavigationEntry[] = [
  { href: '/dashboard/assets', label: 'Assets', icon: ImageIcon },
  { href: '/dashboard/templates', label: 'Templates', icon: LayoutTemplate },
  { href: '/dashboard/brand-kits', label: 'Brand kits', icon: Palette },
]

export const SETTINGS_ENTRY: NavigationEntry = {
  href: '/dashboard/settings',
  label: 'Settings',
  icon: Settings,
}

// Team and Connections live inside Settings, but their established URLs stay reachable.
const SETTINGS_ROUTES = ['/dashboard/settings', '/dashboard/team']

/** The entry for the area being viewed, not every entry the route happens to start with. */
export function isCurrent(pathname: string | null, href: string): boolean {
  if (pathname === null) return false
  if (href === '/dashboard') return pathname === '/dashboard'
  if (href === SETTINGS_ENTRY.href) {
    return SETTINGS_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`))
  }
  return pathname === href || pathname.startsWith(`${href}/`)
}

export const RAIL_PINNED_KEY = 'clipah.rail.pinned'

/** Whether this browser asked for the wide rail; storage that refuses to answer means no. */
export function readRailPinned(): boolean {
  try {
    return window.localStorage.getItem(RAIL_PINNED_KEY) === 'true'
  } catch {
    return false
  }
}

/** Remember the rail width for this browser, and carry on if storage is unavailable. */
export function writeRailPinned(pinned: boolean): void {
  try {
    window.localStorage.setItem(RAIL_PINNED_KEY, String(pinned))
  } catch {
    // A private window or blocked storage only costs the member their preference.
  }
}
```

- [ ] **Step 4: Write `wordmark.tsx`, `rail.tsx`, `account-menu.tsx`, `phone-tabs.tsx`, `top-bar.tsx`**

`frontend/components/shell/wordmark.tsx`:

```tsx
/** The Clipah wordmark in condensed display type, with a lime signal bar under the first letter. */
export function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <span className="font-display inline-flex items-end text-title leading-none tracking-tight">
      <span className="border-b-2 border-primary pb-0.5">C</span>
      {compact ? <span className="sr-only">lipah</span> : <span className="pb-0.5">LIPAH</span>}
    </span>
  )
}
```

`frontend/components/shell/rail.tsx`:

```tsx
'use client'

import { Library, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import Link from 'next/link'
import { useEffect, useId, useState, type ReactNode } from 'react'

import { IconButton } from '@/components/ui/icon-button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

import {
  LIBRARY_NAVIGATION,
  PRIMARY_NAVIGATION,
  SETTINGS_ENTRY,
  isCurrent,
  readRailPinned,
  writeRailPinned,
  type NavigationEntry,
} from './navigation'
import { Wordmark } from './wordmark'

/**
 * The Workspace navigation: a 64 px icon rail, or 220 px with labels once pinned.
 *
 * On phones the same element opens as an overlay from the tab bar's Navigation control,
 * always at full width, so there is one navigation landmark rather than two.
 */
export function Rail({
  pathname,
  newProject,
  open,
  onClose,
}: {
  pathname: string | null
  newProject?: ReactNode
  /** Whether the phone overlay is showing. */
  open: boolean
  onClose: () => void
}) {
  const [pinned, setPinned] = useState(false)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const libraryId = useId()
  const expanded = pinned || open

  useEffect(() => {
    setPinned(readRailPinned())
  }, [])

  function togglePinned(): void {
    const next = !pinned
    setPinned(next)
    writeRailPinned(next)
  }

  return (
    <TooltipProvider delayDuration={200}>
      <nav
        id="workspace-navigation"
        aria-label="Workspace"
        className={cn(
          'fixed inset-y-0 left-0 z-40 shrink-0 flex-col border-r bg-card py-3 transition-[width] duration-panel ease-signal md:sticky md:top-0 md:flex md:h-screen',
          expanded ? 'w-[220px] px-3' : 'w-16 items-center px-2',
          open ? 'flex shadow-2xl' : 'hidden',
        )}
      >
        <div className={cn('flex h-10 items-center pb-3', expanded ? 'justify-between px-1' : 'justify-center')}>
          <Link href="/dashboard" aria-label="Clipah home" onClick={onClose}>
            <Wordmark compact={!expanded} />
          </Link>
        </div>
        {newProject === undefined ? null : <div className={cn('pb-3', expanded ? '' : 'flex justify-center')}>{newProject}</div>}
        <ul className="flex w-full flex-col gap-0.5">
          {PRIMARY_NAVIGATION.map((entry) => (
            <RailLink key={entry.href} entry={entry} pathname={pathname} expanded={expanded} onNavigate={onClose} />
          ))}
          <li>
            <button
              type="button"
              aria-expanded={libraryOpen}
              aria-controls={libraryId}
              onClick={() => setLibraryOpen((current) => !current)}
              className={railItemClass(false, expanded)}
            >
              <Library aria-hidden="true" strokeWidth={1.75} className="size-5 shrink-0" />
              <span className={expanded ? '' : 'sr-only'}>Library</span>
            </button>
            <ul id={libraryId} hidden={!libraryOpen} className={cn('mt-0.5 flex flex-col gap-0.5', expanded ? 'pl-3' : '')}>
              {LIBRARY_NAVIGATION.map((entry) => (
                <RailLink key={entry.href} entry={entry} pathname={pathname} expanded={expanded} onNavigate={onClose} />
              ))}
            </ul>
          </li>
        </ul>
        <ul className="mt-auto flex w-full flex-col gap-0.5 pt-3">
          <RailLink entry={SETTINGS_ENTRY} pathname={pathname} expanded={expanded} onNavigate={onClose} />
          <li className={cn('hidden md:flex', expanded ? 'justify-end' : 'justify-center')}>
            <IconButton
              label={pinned ? 'Collapse navigation' : 'Pin navigation'}
              aria-pressed={pinned}
              icon={pinned ? <PanelLeftClose strokeWidth={1.75} /> : <PanelLeftOpen strokeWidth={1.75} />}
              onClick={togglePinned}
              tooltipSide="right"
            />
          </li>
        </ul>
      </nav>
    </TooltipProvider>
  )
}

function RailLink({
  entry,
  pathname,
  expanded,
  onNavigate,
}: {
  entry: NavigationEntry
  pathname: string | null
  expanded: boolean
  onNavigate: () => void
}) {
  const current = isCurrent(pathname, entry.href)
  const Icon = entry.icon
  const link = (
    <Link
      href={entry.href}
      aria-current={current ? 'page' : undefined}
      onClick={onNavigate}
      className={railItemClass(current, expanded)}
    >
      <Icon aria-hidden="true" strokeWidth={1.75} className="size-5 shrink-0" />
      <span className={expanded ? '' : 'sr-only'}>{entry.label}</span>
    </Link>
  )
  return (
    <li>
      {expanded ? (
        link
      ) : (
        <Tooltip>
          <TooltipTrigger asChild>{link}</TooltipTrigger>
          <TooltipContent side="right">{entry.label}</TooltipContent>
        </Tooltip>
      )}
    </li>
  )
}

function railItemClass(current: boolean, expanded: boolean): string {
  return cn(
    'relative flex h-10 items-center gap-3 rounded-md text-small font-medium transition-colors duration-fast ease-signal',
    expanded ? 'w-full px-3' : 'w-10 justify-center',
    current
      ? 'bg-secondary text-foreground before:absolute before:left-0 before:top-2 before:h-6 before:w-0.5 before:bg-primary'
      : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
  )
}
```

`frontend/components/shell/account-menu.tsx` — move `UserMenu` and `useOutsideClose` out of the current `dashboard-shell.tsx` unchanged in behaviour; restyle the trigger avatar to `flex size-7 items-center justify-center rounded-full bg-secondary text-caption font-semibold text-foreground`, the name to `hidden max-w-32 truncate text-small font-medium lg:inline`, the panel to `absolute right-0 top-11 z-30 w-56 rounded-lg border border-line-strong bg-popover p-1 shadow-xl`, and items to `block w-full rounded-md px-3 py-2 text-left text-small hover:bg-secondary`. Export `AccountMenu` and `useOutsideClose`.

`frontend/components/shell/phone-tabs.tsx`:

```tsx
'use client'

import { Menu } from 'lucide-react'
import Link from 'next/link'

import { cn } from '@/lib/utils'

import { PRIMARY_NAVIGATION, isCurrent } from './navigation'

/** Phone navigation: the four journey destinations, and the full rail behind one control. */
export function PhoneTabs({
  pathname,
  navigationOpen,
  onToggleNavigation,
}: {
  pathname: string | null
  navigationOpen: boolean
  onToggleNavigation: () => void
}) {
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-30 flex h-14 items-stretch border-t bg-card pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      {PRIMARY_NAVIGATION.map((entry) => {
        const Icon = entry.icon
        const current = isCurrent(pathname, entry.href)
        return (
          <Link
            key={entry.href}
            href={entry.href}
            aria-current={current ? 'page' : undefined}
            className={cn(
              'flex flex-1 flex-col items-center justify-center gap-0.5 text-caption font-medium',
              current ? 'text-primary' : 'text-muted-foreground',
            )}
          >
            <Icon aria-hidden="true" strokeWidth={1.75} className="size-5" />
            {entry.label}
          </Link>
        )
      })}
      <button
        type="button"
        aria-label="Navigation"
        aria-expanded={navigationOpen}
        aria-controls="workspace-navigation"
        onClick={onToggleNavigation}
        className="flex flex-1 flex-col items-center justify-center gap-0.5 text-caption font-medium text-muted-foreground"
      >
        <Menu aria-hidden="true" strokeWidth={1.75} className="size-5" />
        More
      </button>
    </nav>
  )
}
```

`frontend/components/shell/top-bar.tsx`:

```tsx
'use client'

import { Search } from 'lucide-react'
import { useId, type ReactNode } from 'react'

/** What every page needs from anywhere: search or jump, the Workspace, work, and the member. */
export function TopBar({
  workspaceSwitcher,
  onOpenCommandPalette,
  activity,
  account,
}: {
  workspaceSwitcher: ReactNode
  onOpenCommandPalette?: () => void
  activity: ReactNode
  account: ReactNode
}) {
  return (
    <header className="sticky top-0 z-20 flex h-[52px] items-center gap-2 border-b bg-background/95 px-4 sm:gap-3 sm:px-6">
      <div className="min-w-0">{workspaceSwitcher}</div>
      {onOpenCommandPalette === undefined ? (
        <SearchForm />
      ) : (
        <button
          type="button"
          onClick={onOpenCommandPalette}
          className="hidden h-9 max-w-md flex-1 items-center gap-2 rounded-md border border-border bg-card px-3 text-small text-subtle-foreground transition-colors duration-fast ease-signal hover:border-line-strong hover:text-muted-foreground sm:flex"
        >
          <Search aria-hidden="true" strokeWidth={1.75} className="size-4" />
          <span className="flex-1 text-left">Search or jump to…</span>
          <kbd className="font-mono text-caption">⌘K</kbd>
        </button>
      )}
      <div className="ml-auto flex items-center gap-2">
        {activity}
        {account}
      </div>
    </header>
  )
}

/** The plain GET search form, for shells rendered without a command palette. */
function SearchForm() {
  const fieldId = useId()
  return (
    <form role="search" action="/dashboard/search" method="get" className="hidden max-w-md flex-1 sm:block">
      <label htmlFor={fieldId} className="sr-only">
        Search this workspace
      </label>
      <input
        id={fieldId}
        type="search"
        name="q"
        placeholder="Search projects, transcripts, clips…"
        className="h-9 w-full rounded-md border border-input bg-secondary px-3 text-small placeholder:text-subtle-foreground"
      />
    </form>
  )
}
```

- [ ] **Step 5: Compose `frontend/components/dashboard-shell.tsx`**

Replace the file's body (the old `NavigationLink`, `GlobalSearchField`, `ActivityIndicator`, `UserMenu`, `useOutsideClose`, `isCurrent`, and navigation constants move out as above; `ActivityIndicator` becomes `RenderQueue` in Task 12 — until then keep it in this file unchanged):

```tsx
'use client'

import { usePathname } from 'next/navigation'
import { useEffect, useState, type ReactNode } from 'react'

import { AccountMenu } from '@/components/shell/account-menu'
import { PhoneTabs } from '@/components/shell/phone-tabs'
import { Rail } from '@/components/shell/rail'
import { TopBar } from '@/components/shell/top-bar'

/** The signed-in User, as much of it as the shell needs to show. */
export interface ShellUser {
  displayName: string | null
  email?: string
}

/**
 * The frame every authenticated page renders inside.
 *
 * A narrow rail holds the journey — Home, Projects, Clips, Publishing — with the library and
 * Settings; the top bar holds what is needed from anywhere. Names come from the API, so they
 * are rendered as text and never as markup.
 */
export function DashboardShell({
  user,
  workspaceSwitcher,
  jobCenter,
  newProject,
  activeJobCount = 0,
  onSignOut,
  onOpenCommandPalette,
  onDropFile,
  children,
}: {
  user: ShellUser
  workspaceSwitcher: ReactNode
  jobCenter: ReactNode
  newProject?: ReactNode
  activeJobCount?: number
  onSignOut?: () => void
  onOpenCommandPalette?: () => void
  onDropFile?: (file: File) => void
  children: ReactNode
}) {
  const pathname = usePathname()
  const [navigationOpen, setNavigationOpen] = useState(false)

  useEffect(() => {
    setNavigationOpen(false)
  }, [pathname])

  return (
    <div className="flex min-h-screen bg-background">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-popover focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <Rail pathname={pathname} newProject={newProject} open={navigationOpen} onClose={() => setNavigationOpen(false)} />
      {navigationOpen ? (
        <div aria-hidden="true" className="fixed inset-0 z-30 bg-background/70 md:hidden" onClick={() => setNavigationOpen(false)} />
      ) : null}
      <div className="flex min-w-0 flex-1 flex-col pb-14 md:pb-0">
        <TopBar
          workspaceSwitcher={workspaceSwitcher}
          onOpenCommandPalette={onOpenCommandPalette}
          activity={<ActivityIndicator count={activeJobCount}>{jobCenter}</ActivityIndicator>}
          account={<AccountMenu user={user} onSignOut={onSignOut} />}
        />
        <main id="main-content" className="mx-auto w-full max-w-studio flex-1 px-4 py-6 sm:px-6 lg:py-8">
          {children}
        </main>
      </div>
      <PhoneTabs
        pathname={pathname}
        navigationOpen={navigationOpen}
        onToggleNavigation={() => setNavigationOpen((open) => !open)}
      />
    </div>
  )
}
```

(`onDropFile` is wired in Task 13; keep the prop in the signature now so Task 13 only adds behaviour.)

- [ ] **Step 6: `NewProjectButton` rail appearance and the switcher**

In `features/projects/new-project.tsx`, add `appearance?: 'button' | 'rail'` to `NewProjectButton`. When `appearance === 'rail'` return:

```tsx
<IconButton
  label="New project"
  variant="secondary"
  icon={<Plus strokeWidth={2} className="text-primary" />}
  onClick={() => (shared === null ? setOpen(true) : shared.open())}
  tooltipSide="right"
/>
```

followed by the same fallback dialog as the button branch. In `components/dashboard-frame.tsx` pass `newProject={<NewProjectButton appearance="rail" />}`.

In `features/workspaces/workspace-switcher.tsx` keep the native `Select` from Task 7 and set `controlSize="sm"`, `wrapperClassName="max-w-44 sm:max-w-56"`, and `className="font-medium"`; delete the separate `ChevronsUpDown` icon (the primitive draws one).

- [ ] **Step 7: Update the two existing shell tests**

In `frontend/tests/projects.test.tsx`:

- "marks the navigation entry for the route being viewed": scope queries with `const rail = screen.getByRole('navigation', { name: 'Workspace' })` and use `within(rail).getByRole('link', { name: 'Projects' })` / `'Home'`.
- "keeps its navigation reachable on a narrow screen behind one labelled control": find the toggle with `within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: 'Navigation' })`, keep the `aria-expanded` assertions, and assert `screen.getByRole('navigation', { name: 'Workspace' })` is in the document.

In `frontend/tests/smoke.test.tsx` "renders the workspace navigation…": scope the Projects link to the `Workspace` navigation the same way.

- [ ] **Step 8: Run the shell tests and the suite**

Run: `pnpm --dir frontend exec vitest run tests/shell.test.tsx tests/projects.test.tsx tests/smoke.test.tsx` → PASS.
Run: `pnpm test` → PASS.

---

### Task 11: Command palette

**Files:**
- Create: `frontend/components/shell/command-palette.tsx`
- Modify: `frontend/components/ui/command.tsx` (Signal classes), `frontend/components/dashboard-frame.tsx`
- Create: `frontend/tests/command-palette.test.tsx`

**Interfaces:**
- Consumes: `searchApiV1SearchGet(params: SearchApiV1SearchGetParams, options?)` from `@/lib/api/generated/search/search`, `useWorkspaceScope()`, `useRouter()`, `useNewProject()` (Task 13 — until then the palette's "New project" action calls the `onNewProject` prop).
- Produces: `CommandPalette` props `{ open: boolean; onOpenChange: (open: boolean) => void; onNewProject?: () => void }`; `useCommandPaletteShortcut(onOpen: () => void): void` (registers `⌘K` / `Ctrl K`).

- [ ] **Step 1: Write the failing tests**

`frontend/tests/command-palette.test.tsx`:

```tsx
import { act, fireEvent, renderHook, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { CommandPalette, useCommandPaletteShortcut } from '@/components/shell/command-palette'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi } from './support/api'
import { currentUser, workspace } from './support/fixtures'

const push = vi.hoisted(() => vi.fn())
vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
}))

beforeEach(() => {
  push.mockReset()
  window.sessionStorage.clear()
})

function palette(onOpenChange = vi.fn(), onNewProject = vi.fn()) {
  renderWithApi(
    <WorkspaceProvider>
      <CommandPalette open onOpenChange={onOpenChange} onNewProject={onNewProject} />
    </WorkspaceProvider>,
  )
  return { onOpenChange, onNewProject }
}

describe('the command palette', () => {
  test('jumps to any destination by name', async () => {
    const user = userEvent.setup()
    stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
    })
    const { onOpenChange } = palette()

    await user.type(await screen.findByRole('combobox'), 'publi')
    await user.keyboard('{Enter}')

    expect(push).toHaveBeenCalledWith('/dashboard/publishing')
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  test('starts a new project', async () => {
    const user = userEvent.setup()
    stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
    })
    const { onNewProject } = palette()

    await user.click(await screen.findByRole('option', { name: 'New project' }))

    expect(onNewProject).toHaveBeenCalledTimes(1)
  })

  test('searches the active workspace and opens a result at its deep link', async () => {
    const user = userEvent.setup()
    const api = stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
      'GET /api/v1/search': {
        body: {
          results: [
            {
              id: 'result-1',
              type: 'clip',
              entityId: 'clip-1',
              projectId: 'project-1',
              projectName: 'Episode 12',
              title: 'The surprising opening',
              deepLink: '/dashboard/clips/clip-1',
              fragments: [],
              score: 1,
              speaker: null,
              startMs: null,
              endMs: null,
              language: 'en',
              topics: [],
              tags: [],
              exportState: 'not_exported',
              createdAt: '2026-02-01T00:00:00+00:00',
            },
          ],
          nextCursor: null,
        },
      },
    })
    palette()

    await user.type(await screen.findByRole('combobox'), 'surprising')
    await user.click(await screen.findByRole('option', { name: /the surprising opening/i }))

    const search = api.calls.find((call) => call.path === '/api/v1/search')
    expect(search?.params.get('q')).toBe('surprising')
    expect(search?.params.get('workspace_id')).toBe(workspace().id)
    expect(push).toHaveBeenCalledWith('/dashboard/clips/clip-1')
  })

  test('⌘K and Ctrl K open it from anywhere', () => {
    const onOpen = vi.fn()
    renderHook(() => useCommandPaletteShortcut(onOpen))

    act(() => {
      fireEvent.keyDown(window, { key: 'k', metaKey: true })
      fireEvent.keyDown(window, { key: 'K', ctrlKey: true })
    })

    expect(onOpen).toHaveBeenCalledTimes(2)
  })
})
```

Before running, confirm the `SearchResultResponse` fields against `frontend/lib/api/generated/model/searchResultResponse.ts` and the `ExportState` values in `exportState.ts`; adjust the fixture's `exportState` literal and nullable fields to the generated types so typecheck passes.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/command-palette.test.tsx`
Expected: FAIL — `Cannot find module '@/components/shell/command-palette'`.

- [ ] **Step 3: Implement `frontend/components/shell/command-palette.tsx`**

```tsx
'use client'

import { useQuery } from '@tanstack/react-query'
import { FilePlus2, Search } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { SearchPageResponse } from '@/lib/api/generated/model'
import { searchApiV1SearchGet } from '@/lib/api/generated/search/search'

import { LIBRARY_NAVIGATION, PRIMARY_NAVIGATION, SETTINGS_ENTRY } from './navigation'

const DESTINATIONS = [...PRIMARY_NAVIGATION, ...LIBRARY_NAVIGATION, SETTINGS_ENTRY]
const SEARCH_DELAY_MS = 200

/**
 * Go anywhere, start anything, or find a moment by what was said — from the keyboard.
 *
 * Destinations filter locally; workspace results come from the same search the Search page
 * uses, asked only after typing pauses, and open at the deep link the backend returned.
 */
export function CommandPalette({
  open,
  onOpenChange,
  onNewProject,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onNewProject?: () => void
}) {
  const router = useRouter()
  const { active } = useWorkspaceScope()
  const [query, setQuery] = useState('')
  const question = useDebounced(query.trim(), SEARCH_DELAY_MS)

  const results = useQuery<SearchPageResponse, ApiError>({
    queryKey: ['/api/v1/search', active.id, 'palette', question],
    queryFn: ({ signal }) =>
      searchApiV1SearchGet({ q: question, workspace_id: active.id, limit: 8 }, { signal }),
    enabled: open && question.length >= 2,
    retry: false,
    staleTime: 30_000,
  })

  function go(href: string): void {
    onOpenChange(false)
    setQuery('')
    router.push(href)
  }

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput value={query} onValueChange={setQuery} placeholder="Search or jump to…" />
      <CommandList>
        <CommandEmpty>Nothing matches that yet.</CommandEmpty>
        <CommandGroup heading="Actions">
          <CommandItem
            value="New project"
            onSelect={() => {
              onOpenChange(false)
              onNewProject?.()
            }}
          >
            <FilePlus2 aria-hidden="true" strokeWidth={1.75} />
            New project
          </CommandItem>
        </CommandGroup>
        <CommandGroup heading="Go to">
          {DESTINATIONS.map((entry) => {
            const Icon = entry.icon
            return (
              <CommandItem key={entry.href} value={entry.label} onSelect={() => go(entry.href)}>
                <Icon aria-hidden="true" strokeWidth={1.75} />
                {entry.label}
              </CommandItem>
            )
          })}
        </CommandGroup>
        {results.data === undefined || results.data.results.length === 0 ? null : (
          <CommandGroup heading="In this workspace">
            {results.data.results.map((result) => (
              <CommandItem
                key={result.id}
                value={`${result.title} ${result.projectName} ${result.id}`}
                onSelect={() => go(result.deepLink)}
              >
                <Search aria-hidden="true" strokeWidth={1.75} />
                <span className="min-w-0 flex-1 truncate">{result.title}</span>
                <span className="truncate text-caption text-subtle-foreground">{result.projectName}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  )
}

/** Open the palette with ⌘K on macOS and Ctrl K elsewhere. */
export function useCommandPaletteShortcut(onOpen: () => void): void {
  const latest = useRef(onOpen)
  latest.current = onOpen
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        latest.current()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])
}

function useDebounced(value: string, delayMs: number): string {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delayMs)
    return () => window.clearTimeout(timer)
  }, [value, delayMs])
  return settled
}
```

cmdk filters items by `value`; the search group's `value` includes the result title, so typed text matches it. If cmdk's filter hides server results whose title does not contain the typed words, pass `shouldFilter={false}` on the inner `Command` for the results group by moving results into their own `CommandList` section, and filter destinations manually with `entry.label.toLowerCase().includes(query.toLowerCase())`.

- [ ] **Step 4: Restyle `components/ui/command.tsx`**

- `Command`: `flex h-full w-full flex-col overflow-hidden rounded-lg bg-popover text-popover-foreground`
- `CommandDialog` content: add `max-w-xl border-line-strong` and keep `p-0`
- `CommandInput` wrapper: `flex items-center border-b border-border px-3`; input: `flex h-12 w-full bg-transparent text-body outline-none placeholder:text-subtle-foreground`
- `CommandItem`: `relative flex cursor-default select-none items-center gap-3 rounded-md px-3 py-2 text-small outline-none data-[selected=true]:bg-secondary data-[selected=true]:text-foreground [&_svg]:size-4 [&_svg]:text-muted-foreground`
- group heading: `px-3 pb-1 pt-3 text-caption font-medium uppercase tracking-wide text-subtle-foreground`

- [ ] **Step 5: Wire it into `components/dashboard-frame.tsx`**

Inside `SignedInShell`, add `const [paletteOpen, setPaletteOpen] = useState(false)`, call `useCommandPaletteShortcut(() => setPaletteOpen(true))`, pass `onOpenCommandPalette={() => setPaletteOpen(true)}` to `DashboardShell`, and render after the shell:

```tsx
<CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} onNewProject={openNewProject} />
```

where `openNewProject` comes from `useNewProject()` (Task 13). Until Task 13 lands, pass `undefined`.

- [ ] **Step 6: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/command-palette.test.tsx` → PASS (4 tests).
Run: `pnpm test` → PASS.

---

### Task 12: Render queue

**Files:**
- Create: `frontend/components/shell/render-queue.tsx`
- Modify: `frontend/components/dashboard-shell.tsx` (replace `ActivityIndicator`), `frontend/features/jobs/job-center.tsx` (Signal layout, Cancel)
- Modify: `frontend/tests/jobs.test.tsx`

**Interfaces:**
- Consumes: `cancelApiV1JobsJobIdCancelPost(jobId, { workspace_id })`, `notify`.
- Produces: `RenderQueue` props `{ count: number; children: ReactNode }`. `JobCenter` keeps `onActiveCountChange` and adds a Cancel control for running jobs.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/tests/jobs.test.tsx` (reuse its existing `FakeEventSource`, `stubApi`, and fixture helpers):

```tsx
test('the render queue toggle says how much is running and docks the queue', async () => {
  const user = userEvent.setup()
  renderWithApi(<RenderQueue count={2}><p>Queue body</p></RenderQueue>)

  const toggle = screen.getByRole('button', { name: 'Activity, 2 running' })
  expect(toggle).toHaveTextContent('2 running')
  expect(screen.getByText('Queue body')).not.toBeVisible()

  await user.click(toggle)

  expect(toggle).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByText('Queue body')).toBeVisible()
})

test('a running job can be stopped from the queue', async () => {
  const user = userEvent.setup()
  const api = stubApi({
    'GET /api/v1/me': { body: currentUser() },
    'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
    [`POST /api/v1/jobs/${JOB_ID}/cancel`]: { status: 202, body: {} },
  })
  renderWithApi(<WorkspaceProvider><JobCenter /></WorkspaceProvider>)
  await announce('started', { jobId: JOB_ID, projectId: PROJECT_ID, kind: 'transcribe', status: 'running', stage: 'transcribing', progress: 0 })

  await user.click(await screen.findByRole('button', { name: 'Stop Transcribing' }))

  expect(api.calls.some((call) => call.method === 'POST' && call.path === `/api/v1/jobs/${JOB_ID}/cancel`)).toBe(true)
})
```

`announce`, `JOB_ID`, and `PROJECT_ID` are whatever the file's existing helpers are called; if the file emits events inline, extract the existing inline emission into a local `announce(type, payload)` helper first, without changing existing tests.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/jobs.test.tsx`
Expected: FAIL — `RenderQueue` is missing and there is no Stop button.

- [ ] **Step 3: Implement `frontend/components/shell/render-queue.tsx`**

```tsx
'use client'

import { Activity } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

import { useOutsideClose } from './account-menu'

/**
 * Running work, one click away from every page, docked at the bottom right.
 *
 * The panel is hidden rather than unmounted when closed, so the job center inside it keeps
 * its one live connection and its history while the member moves around.
 */
export function RenderQueue({ count, children }: { count: number; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const container = useOutsideClose(open, () => setOpen(false))
  const panelId = useId()

  return (
    <div ref={container}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={count === 0 ? 'Activity' : `Activity, ${count} running`}
        onClick={() => setOpen((current) => !current)}
        className={cn(
          'flex h-9 items-center gap-2 rounded-md border px-2.5 text-small font-medium transition-colors duration-fast ease-signal',
          count === 0 ? 'border-border text-muted-foreground hover:text-foreground' : 'border-line-strong text-foreground',
        )}
      >
        <Activity aria-hidden="true" strokeWidth={1.75} className={cn('size-4', count > 0 && 'text-primary motion-safe:animate-pulse')} />
        {count === 0 ? null : <span className="font-mono text-caption tabular">{count} running</span>}
      </button>
      <div
        id={panelId}
        hidden={!open}
        className="fixed bottom-16 right-4 z-40 max-h-[70vh] w-[min(360px,calc(100vw-2rem))] overflow-y-auto rounded-lg border border-line-strong bg-popover p-4 shadow-2xl md:bottom-4"
      >
        {children}
      </div>
    </div>
  )
}
```

In `components/dashboard-shell.tsx` replace `ActivityIndicator` with `RenderQueue` and delete the old component.

- [ ] **Step 4: Add Stop and Signal styling to `features/jobs/job-center.tsx`**

Inside `JobCenter`, add:

```tsx
async function stop(job: AnnouncedJob): Promise<void> {
  try {
    await cancelApiV1JobsJobIdCancelPost(job.jobId, { workspace_id: active.id })
  } catch (error) {
    notify.failure(error)
  }
}
```

Render each job as:

```tsx
<li key={job.jobId} className="space-y-2 rounded-md border border-border bg-card px-3 py-2.5">
  <div className="flex items-center justify-between gap-2">
    <p className="text-small font-medium">{stageLabel(job)}</p>
    <StatusBadge tone={STATUS_TONES[job.status] ?? 'neutral'}>{statusLabel(job.status)}</StatusBadge>
  </div>
  <div className="flex items-center gap-3 text-caption">
    {job.projectId === null ? null : (
      <Link href={`/dashboard/projects/${job.projectId}`} className="font-medium text-primary hover:underline">
        Open project
      </Link>
    )}
    {TERMINAL_STATUSES.has(job.status) || job.status === 'cancel_requested' ? null : (
      <button
        type="button"
        onClick={() => void stop(job)}
        aria-label={`Stop ${stageLabel(job)}`}
        className="font-medium text-muted-foreground hover:text-destructive"
      >
        Stop
      </button>
    )}
  </div>
</li>
```

Change the heading to `<h2 className="text-title">Activity</h2>` and the counter to `font-mono text-caption text-subtle-foreground`.

- [ ] **Step 5: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/jobs.test.tsx` → PASS.
Run: `pnpm test` → PASS.

---

### Task 13: Drop a video anywhere to start a project

**Files:**
- Create: `frontend/components/shell/drop-target.tsx`
- Modify: `frontend/features/projects/new-project.tsx` (`useNewProject`, `open(file?)`, `initialFile`), `frontend/components/dashboard-shell.tsx`, `frontend/components/dashboard-frame.tsx`
- Modify: `frontend/tests/projects.test.tsx` (new tests in "starting a project")

**Interfaces:**
- Produces: `useNewProject(): { open: (file?: File) => void } | null`; `NewProjectDialog` props gain `initialFile?: File | null`; `DropTarget` props `{ onFile: (file: File) => void; onRefused: () => void }`.

- [ ] **Step 1: Write the failing tests**

Add to the "starting a project" block in `frontend/tests/projects.test.tsx`:

```tsx
test('a video dropped anywhere opens New project with the file and its suggested name', async () => {
  stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace({ role: 'owner' })] } },
  })
  renderWithApi(
    <WorkspaceProvider>
      <NewProjectProvider>
        <DashboardShell user={currentUser()} workspaceSwitcher={null} jobCenter={null} onDropFile={(file) => opener.current?.open(file)}>
          <OpenerProbe />
        </DashboardShell>
      </NewProjectProvider>
    </WorkspaceProvider>,
  )
  const file = new File(['x'], 'Episode_42_interview.mp4', { type: 'video/mp4' })

  fireEvent.dragEnter(window, { dataTransfer: { types: ['Files'], files: [file] } })
  expect(await screen.findByText('Drop to start a project')).toBeInTheDocument()
  fireEvent.drop(window, { dataTransfer: { types: ['Files'], files: [file] } })

  expect(await screen.findByRole('dialog', { name: 'New project' })).toBeInTheDocument()
  expect(screen.getByDisplayValue('Episode 42 interview')).toBeInTheDocument()
  expect(screen.getByText('Episode_42_interview.mp4')).toBeInTheDocument()
})

test('a dropped file that is not a video is refused without opening anything', async () => {
  const onDropFile = vi.fn()
  renderWithApi(
    <DashboardShell user={currentUser()} workspaceSwitcher={null} jobCenter={null} onDropFile={onDropFile}>
      <p>Body</p>
    </DashboardShell>,
  )
  const file = new File(['x'], 'notes.pdf', { type: 'application/pdf' })

  fireEvent.dragEnter(window, { dataTransfer: { types: ['Files'], files: [file] } })
  fireEvent.drop(window, { dataTransfer: { types: ['Files'], files: [file] } })

  expect(onDropFile).not.toHaveBeenCalled()
  expect(screen.queryByText('Drop to start a project')).not.toBeInTheDocument()
})
```

with, at the top of the file:

```tsx
const opener = { current: null as ReturnType<typeof useNewProject> }
function OpenerProbe() {
  opener.current = useNewProject()
  return null
}
```

and imports `fireEvent` from `@testing-library/react`, `NewProjectProvider`, `useNewProject` from `@/features/projects/new-project`.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/projects.test.tsx`
Expected: FAIL — `useNewProject` is not exported and nothing listens for drops.

- [ ] **Step 3: Implement `frontend/components/shell/drop-target.tsx`**

```tsx
'use client'

import { Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

/**
 * A full-window target for a video dragged in from the desktop.
 *
 * It only appears while files are being dragged, takes the first file, and hands over
 * only videos; anything else is refused where it was dropped.
 */
export function DropTarget({ onFile, onRefused }: { onFile: (file: File) => void; onRefused: () => void }) {
  const [active, setActive] = useState(false)
  const depth = useRef(0)
  const latest = useRef({ onFile, onRefused })
  latest.current = { onFile, onRefused }

  useEffect(() => {
    const carriesFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files')
    function onEnter(event: DragEvent): void {
      if (!carriesFiles(event)) return
      depth.current += 1
      setActive(true)
    }
    function onOver(event: DragEvent): void {
      if (carriesFiles(event)) event.preventDefault()
    }
    function onLeave(): void {
      depth.current = Math.max(0, depth.current - 1)
      if (depth.current === 0) setActive(false)
    }
    function onDrop(event: DragEvent): void {
      if (!carriesFiles(event)) return
      event.preventDefault()
      depth.current = 0
      setActive(false)
      const file = event.dataTransfer?.files[0]
      if (file === undefined) return
      if (file.type.startsWith('video/')) latest.current.onFile(file)
      else latest.current.onRefused()
    }
    window.addEventListener('dragenter', onEnter)
    window.addEventListener('dragover', onOver)
    window.addEventListener('dragleave', onLeave)
    window.addEventListener('drop', onDrop)
    return () => {
      window.removeEventListener('dragenter', onEnter)
      window.removeEventListener('dragover', onOver)
      window.removeEventListener('dragleave', onLeave)
      window.removeEventListener('drop', onDrop)
    }
  }, [])

  if (!active) return null
  return (
    <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-background/90 p-6">
      <div className="flex w-full max-w-3xl flex-col items-start gap-3 rounded-lg border-2 border-dashed border-primary p-10">
        <Upload aria-hidden="true" strokeWidth={1.75} className="size-8 text-primary" />
        <p className="font-display text-display">Drop to start a project</p>
        <p className="text-small text-muted-foreground">One long video: MP4, MOV, or WebM up to 2 GB.</p>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Open New project with a file**

In `features/projects/new-project.tsx`:

```tsx
interface NewProjectControl {
  open: (file?: File) => void
}

/** Open New project from anywhere inside the Workspace frame, optionally with a file. */
export function useNewProject(): NewProjectControl | null {
  return useContext(NewProjectContext)
}

export function NewProjectProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [initialFile, setInitialFile] = useState<File | null>(null)
  const control = useMemo<NewProjectControl>(
    () => ({
      open: (file?: File) => {
        setInitialFile(file ?? null)
        setOpen(true)
      },
    }),
    [],
  )
  return (
    <NewProjectContext.Provider value={control}>
      {children}
      <NewProjectDialog
        open={open}
        initialFile={initialFile}
        onOpenChange={(next) => {
          setOpen(next)
          if (!next) setInitialFile(null)
        }}
      />
    </NewProjectContext.Provider>
  )
}
```

Add `initialFile?: File | null` to `NewProjectDialog`'s props and, inside it:

```tsx
useEffect(() => {
  if (open && initialFile != null) {
    setSource('upload')
    pickFile(initialFile)
  }
  // Only a newly offered file is picked; edits after that belong to the member.
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [open, initialFile])
```

- [ ] **Step 5: Render the target in the shell and wire the frame**

In `components/dashboard-shell.tsx` render, just before the closing `</div>`:

```tsx
{onDropFile === undefined ? null : (
  <DropTarget onFile={onDropFile} onRefused={() => notify.info('That file isn’t a video. Drop an MP4, MOV, or WebM.')} />
)}
```

In `components/dashboard-frame.tsx`, read `const newProject = useNewProject()` and `const { active } = useWorkspaceScope()` in `SignedInShell`; pass `onDropFile={newProject !== null && mayWriteProjects(active.role) ? (file) => newProject.open(file) : undefined}` to `DashboardShell` and `onNewProject={() => newProject?.open()}` to `CommandPalette`.

- [ ] **Step 6: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/projects.test.tsx` → PASS.
Run: `pnpm test` → PASS.

---

### Task 14: Gates, screenshots, and handover

**Files:**
- Modify: `PROGRESS.md` (append a "Signal Studio redesign — Plan 1, foundation" entry under the post-rebuild extension)

- [ ] **Step 1: Run all four frontend gates from the repository root**

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

Expected: all PASS. Record the test count.

- [ ] **Step 2: Capture foundation screenshots and check overflow**

Rebuild and restart the Compose `frontend` service so it serves this build (`docker compose -f infra/compose.yaml up -d --build frontend`), then from `frontend/`:

```bash
CLIPAH_CAPTURE_SCREENS=foundation CLIPAH_E2E_BASE_URL=http://localhost:3000 pnpm exec playwright test e2e/design-screens.spec.ts --project=chromium
```

Expected: PASS, 54 images in `docs/design/signal/foundation/`, and no route overflows sideways. Open three of them (home, project, editor at 1440) and confirm: graphite surfaces, lime primary buttons, Archivo text, the icon rail.

- [ ] **Step 3: Run the existing browser suite in both engines**

From `frontend/`, against the same stack: `CLIPAH_E2E_BASE_URL=http://localhost:3000 pnpm exec playwright test --project=chromium --project=webkit`
Expected: the same pass/skip counts as the last recorded run in `PROGRESS.md`, except scenarios whose selectors changed in Tasks 10–13; fix those selectors (scope navigation links to the `Workspace` landmark) and re-run until they match.

- [ ] **Step 4: Record progress**

Append to `PROGRESS.md`: what landed (tokens, fonts, primitives, lint rule, shared components, toasts, shell, palette, render queue, drop target), the deliberate spec refinements (native Select/Checkbox/Radio/Slider, ItemMenu kept, `--text-muted #8F8F98`, `--signal #FF5A50`, `--control-border`), gate output with counts, the screenshot folders, and the owner commit message `feat: add signal studio foundation`. Do not run `git commit`.
