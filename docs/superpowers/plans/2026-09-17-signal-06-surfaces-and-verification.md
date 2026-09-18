# Remaining Surfaces and Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (the repository owner requires inline execution without subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the redesign — export, publishing, library, settings, and public pages — rewrite the copy in plain creator language, and prove the whole product against the spec's acceptance checks.

**Architecture:** Screens are rebuilt on the primitives and media components from Plans 1–5; behaviour and API calls are unchanged except for export toasts. Two tests guard the result permanently: the design-rules test (banned patterns, now with an empty allowlist) and a copy-rules test (banned phrases). Verification is a checklist of real runs, not assertions about intent.

**Tech Stack:** Next.js 15, React 19, Tailwind CSS 3, Radix Dialog/Sheet, sonner, Vitest + vitest-axe, Playwright (Chromium and WebKit), FFmpeg (for the marketing loop).

**Spec:** `redesign-plan-v2.md` → Screens (Export, Publishing, Library, Settings, Public pages), Copy guide, Verification and Acceptance, Constraints. Plan index: `docs/superpowers/plans/2026-09-17-signal-studio-redesign.md`.

## Global Constraints

- Everything in the plan index's "Global constraints" applies.
- Depends on Plans 1–5.
- Publishing rules are untouched: explicit destination selection, consent requirements, review eligibility, capability gates, recent-auth for publishing, and independent retry and cancel. Every existing `publication-composer` and `publication-history` test keeps passing with at most query-location changes.
- Publications carry no link to their clip, so queue entries show destination and time, not a poster (spec, Publishing).
- Public pages must not show gradients or stock imagery; product imagery is captured from the real product.
- Owner commit message for this plan: `feat: finish the signal studio redesign`.

## File map

| File | Responsibility |
| --- | --- |
| `frontend/features/editor/ExportDialog.tsx`, `frontend/features/exports/export-list.tsx` | Format cards with posters; queued and ready toasts; poster rows |
| `frontend/features/publishing/{PublicationHistory,PublicationComposer,NewPublication}.tsx`, `frontend/app/dashboard/publishing/page.tsx` | Queue timeline and three-column composer |
| `frontend/features/editor/LookSample.tsx` | The caption look drawn from a style, shared by the editor and the library |
| `frontend/features/assets/AssetBrowser.tsx`, `frontend/features/templates/TemplateLibrary.tsx`, `frontend/features/brand-kits/BrandKitEditor.tsx` | Library screens |
| `frontend/features/settings/{SettingsNav,GeneralSettings}.tsx`, `frontend/app/dashboard/{settings,team}/page.tsx`, `frontend/app/dashboard/settings/connections/page.tsx` | Settings with a left sub-navigation |
| `frontend/components/public-frame.tsx`, `frontend/components/marketing-frame.tsx`, `frontend/app/{page,signin/page,demo/page}.tsx`, `frontend/features/team/InviteAcceptance.tsx`, `frontend/public/marketing/*` | Public pages |
| `frontend/e2e/marketing-capture.spec.ts` | Opt-in capture of the product loop and stills |
| `frontend/tests/copy-rules.test.ts`, `frontend/tests/support/axe.ts` | Permanent copy guard; one axe assertion used by every rebuilt screen's suite |

---

### Task 1: Export dialog and export lists

**Files:**
- Modify: `frontend/features/editor/ExportDialog.tsx`, `frontend/features/exports/export-list.tsx`, `frontend/features/editor/EditorScreen.tsx` (pass `projectId` and the source range)
- Modify: `frontend/lib/notify.ts` (toast `id` and a second action)
- Modify: `frontend/tests/creator-studio.test.tsx`, `frontend/tests/notify.test.tsx`
- Create: `frontend/tests/export-toasts.test.tsx`

**Interfaces:**
- Consumes: `notify` and `NotifyOptions` (Plan 1 Task 6), `Poster` (Plan 3 Task 3), `useSession()` from `@/features/auth/session`, `publishHref(entry, renderId)` from `export-list.tsx`.
- Produces: `NotifyOptions` gains `id?: string` (sonner replaces a toast with the same id instead of stacking a second) and `secondaryAction?: { label: string; onClick: () => void }` (rendered through sonner's `cancel` button). `ExportDialog` props gain `projectId: string` and `sourceRange: { inMs: number; outMs: number }`. `useExports` raises `notify.success('Export ready', { id: 'export-ready-<exportId>', action: Download, secondaryAction: Publish when the member may publish })` once for each export that turns `ready` while it is being watched; `ExportDialog` raises `notify.info('Export queued', …)` after a render is accepted.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/export-toasts.test.tsx`:

```tsx
import { act, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { Toaster } from '@/components/ui/sonner'
import { ExportList } from '@/features/exports/export-list'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { ApiWrapper, stubApi } from './support/api'
import { capabilities, currentUser, workspace } from './support/fixtures'

const EDIT_ID = '77777777-7777-4777-8777-777777777777'

function row(status: string) {
  return {
    id: '88888888-0000-4000-8000-000000000001',
    jobId: '88888888-0000-4000-8000-000000000002',
    status,
    errorCode: null,
    preset: '1080x1920',
    projectId: '44444444-4444-4444-8444-444444444444',
    projectName: 'Episode 12',
    candidateId: '55555555-5555-4555-8555-555555555551',
    editId: EDIT_ID,
    revisionId: '88888888-0000-4000-8000-000000000003',
    revision: 2,
    renderId: status === 'ready' ? '99999999-0000-4000-8000-000000000001' : null,
    durationMs: 30_000,
    sizeBytes: status === 'ready' ? 4_096_000 : null,
    createdAt: '2026-02-01T00:00:00+00:00',
    completedAt: status === 'ready' ? '2026-02-01T00:01:00+00:00' : null,
  }
}

beforeEach(() => {
  window.sessionStorage.clear()
})

describe('export toasts', () => {
  test('an export that finishes while it is watched says so with Download', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let status = 'rendering'
    stubApi({
      'GET /api/v1/me': { body: currentUser({ capabilities: capabilities({ socialPublishing: true }) }) },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
      'GET /api/v1/exports': () => ({ body: { exports: [row(status)], nextCursor: null } }),
    })
    render(
      <ApiWrapper>
        <WorkspaceProvider>
          <ExportList editId={EDIT_ID} />
        </WorkspaceProvider>
        <Toaster />
      </ApiWrapper>,
    )
    expect(await screen.findByText('Rendering')).toBeInTheDocument()

    status = 'ready'
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_100)
    })

    await waitFor(() => expect(screen.getByText('Export ready')).toBeInTheDocument())
    const toast = screen.getByText('Export ready').closest('[data-sonner-toast]') as HTMLElement
    expect(within(toast).getByRole('button', { name: 'Download' })).toBeInTheDocument()
    expect(within(toast).getByRole('button', { name: 'Publish' })).toBeInTheDocument()
    vi.useRealTimers()
  })

  test('an export already finished when the list opens raises nothing', async () => {
    stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
      'GET /api/v1/exports': { body: { exports: [row('ready')], nextCursor: null } },
    })
    render(
      <ApiWrapper>
        <WorkspaceProvider>
          <ExportList editId={EDIT_ID} />
        </WorkspaceProvider>
        <Toaster />
      </ApiWrapper>,
    )

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(screen.queryByText('Export ready')).not.toBeInTheDocument()
  })
})
```

In `frontend/tests/creator-studio.test.tsx`, pass `projectId={PROJECT_ID}` and `sourceRange={{ inMs: 1_000, outMs: 31_000 }}` to every `ExportDialog` render (declare `const PROJECT_ID = '44444444-4444-4444-8444-444444444444'` beside the file's other identifiers if it has none), and in the test that renders `ExportDialog` add after the existing assertions:

```tsx
    // Every format is shown as the clip itself, cropped to that shape.
    expect(screen.getAllByTestId('poster')).toHaveLength(4)
```

The storyboard read is unstubbed, so each poster renders its designed frame — the assertion is about one poster per format, not about sprites.

In `frontend/tests/notify.test.tsx`, add:

```tsx
  test('a repeated id replaces the toast, and a second action is offered', async () => {
    render(<Toaster />)
    act(() => {
      notify.success('Export ready', { id: 'export-ready-1', secondaryAction: { label: 'Publish', onClick: () => undefined } })
      notify.success('Export ready', { id: 'export-ready-1', secondaryAction: { label: 'Publish', onClick: () => undefined } })
    })

    expect(await screen.findAllByText('Export ready')).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Publish' })).toBeInTheDocument()
  })
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/export-toasts.test.tsx tests/creator-studio.test.tsx tests/notify.test.tsx`
Expected: FAIL — no toast; the dialog has no posters and no `projectId` prop; `id` and `secondaryAction` are unknown options.

- [ ] **Step 3: Implement the toasts**

In `frontend/lib/notify.ts`:

```ts
export interface NotifyOptions {
  description?: string
  action?: { label: string; onClick: () => void }
  secondaryAction?: { label: string; onClick: () => void }
  /** A toast with the same id replaces the one already showing. */
  id?: string
}

function toSonner({ secondaryAction, ...options }: NotifyOptions) {
  return secondaryAction === undefined ? options : { ...options, cancel: secondaryAction }
}
```

and pass `toSonner(options)` instead of `options` in `success` and `info`.

In `features/exports/export-list.tsx`, add `import { useEffect, useRef } from 'react'` and `import { notify } from '@/lib/notify'`, then rewrite `useExports`:

```tsx
export function useExports(scope: { editId?: string; projectId?: string }) {
  const { active } = useWorkspaceScope()
  const session = useSession()
  const query = useQuery<ExportPageResponse, ApiError>({
    // queryKey, queryFn, retry, and refetchInterval exactly as before
  })

  // Only a change seen while watching is news; exports already finished on arrival are not.
  const watched = useRef<Map<string, string>>(new Map())
  const mayPublish = session.data?.capabilities.socialPublishing === true
  useEffect(() => {
    for (const entry of query.data?.exports ?? []) {
      const before = watched.current.get(entry.id)
      watched.current.set(entry.id, entry.status)
      if (before === undefined || !IN_PROGRESS.has(before) || entry.status !== 'ready' || entry.renderId === null) continue
      const renderId = entry.renderId
      notify.success('Export ready', {
        id: `export-ready-${entry.id}`,
        description: `${PRESET_LABELS[entry.preset]?.name ?? entry.preset} · Revision ${entry.revision}`,
        action: {
          label: 'Download',
          onClick: () => {
            downloadApiV1RendersRenderIdDownloadUrlGet(renderId, { workspace_id: active.id }).then(
              (signed) => window.location.assign(signed.url),
              (error: unknown) => notify.failure(error),
            )
          },
        },
        ...(mayPublish
          ? { secondaryAction: { label: 'Publish', onClick: () => window.location.assign(publishHref(entry, renderId)) } }
          : {}),
      })
    }
  }, [active.id, mayPublish, query.data])

  return query
}
```

In `ExportDialog.tsx`, after `createApiV1EditsEditIdRendersPost` resolves: `notify.info('Export queued', { description: 'It keeps going if you close this window. Follow it in Activity.' })`.

- [ ] **Step 4: Rebuild the format cards and rows**

In `ExportDialog.tsx`, map each preset to its aspect and render the card as:

```tsx
const ASPECT_CLASS: Record<RenderPreset, string> = {
  '1080x1920': 'aspect-[9/16] w-14',
  '1080x1350': 'aspect-[4/5] w-16',
  '1080x1080': 'aspect-square w-16',
  '1920x1080': 'aspect-video w-24',
}
```

```tsx
<label key={entry} className={cn('flex cursor-pointer items-center gap-3 rounded-lg border p-3 transition-colors duration-fast ease-signal', preset === entry ? 'border-primary' : 'border-border hover:border-line-strong')}>
  <Radio name="export-preset" value={entry} checked={preset === entry} onChange={() => setPreset(entry)} />
  <span className={cn('relative shrink-0 overflow-hidden rounded-sm bg-stage', ASPECT_CLASS[entry])}>
    <Poster projectId={projectId} startMs={sourceRange.inMs} endMs={sourceRange.outMs} aspect={entry === '1920x1080' ? 'video' : 'portrait'} />
  </span>
  <span className="min-w-0">
    <span className="block text-small font-semibold">{label?.name} <span className="font-mono text-caption text-muted-foreground">{label?.ratio}</span></span>
    <span className="block text-caption text-muted-foreground">{label?.use}</span>
  </span>
</label>
```

`Poster` cover-crops to its box for 4:5 and 1:1 as well, because `SpriteFrame` compares the tile shape with the box it fills; pass `aspect="portrait"` only to choose the scrub geometry, which is shape-independent.

In `export-list.tsx` `ExportRow`, replace the tinted `FileVideo` square with `<span className="relative aspect-video w-20 shrink-0 overflow-hidden rounded-sm"><Poster projectId={entry.projectId} /></span>`, move buttons to `Button` (`Download` secondary, `Publish` primary sm), and restyle the list to `divide-y divide-border rounded-lg border`.

In `EditorScreen.tsx`, pass `projectId={edit.projectId}` and `sourceRange={composition.sourceRange}` to `ExportDialog`.

- [ ] **Step 5: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/export-toasts.test.tsx tests/creator-studio.test.tsx` → PASS.

---

### Task 2: Publishing

**Files:**
- Modify: `frontend/app/dashboard/publishing/page.tsx`, `frontend/features/publishing/PublicationHistory.tsx`, `frontend/features/publishing/PublicationComposer.tsx`, `frontend/features/publishing/NewPublication.tsx`
- Modify: `frontend/tests/publication-history.test.tsx`
- Create: `frontend/tests/publishing-queue.test.tsx`

**Interfaces:**
- Produces: `scheduleBucket(scheduledFor: string, now: Date, timeZone: string): 'today' | 'tomorrow' | 'later'` in `features/publishing/schedule.ts`; the list view renders Needs attention first, then `Scheduled` split into `Today`, `Tomorrow`, `Later` sub-lists, then In progress, Published, Cancelled.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/publishing-queue.test.tsx`:

```tsx
import { describe, expect, test } from 'vitest'

import { scheduleBucket } from '@/features/publishing/schedule'

describe('the scheduled timeline', () => {
  const now = new Date('2026-09-17T10:00:00+07:00')

  test('buckets by the calendar day in the zone the member chose', () => {
    expect(scheduleBucket('2026-09-17T22:00:00+07:00', now, 'Asia/Jakarta')).toBe('today')
    expect(scheduleBucket('2026-09-18T00:30:00+07:00', now, 'Asia/Jakarta')).toBe('tomorrow')
    expect(scheduleBucket('2026-09-19T09:00:00+07:00', now, 'Asia/Jakarta')).toBe('later')
  })

  test('the same instant can be tomorrow in one zone and today in another', () => {
    expect(scheduleBucket('2026-09-17T18:30:00+00:00', now, 'Asia/Jakarta')).toBe('tomorrow')
    expect(scheduleBucket('2026-09-17T18:30:00+00:00', now, 'UTC')).toBe('today')
  })

  test('tomorrow is the next calendar day even when the clocks change overnight', () => {
    // New York moves to daylight time at 02:00 on 8 March 2026; that night is 23 hours long.
    const lateEvening = new Date('2026-03-07T23:30:00-05:00')
    expect(scheduleBucket('2026-03-08T12:00:00-04:00', lateEvening, 'America/New_York')).toBe('tomorrow')
    expect(scheduleBucket('2026-03-09T00:30:00-04:00', lateEvening, 'America/New_York')).toBe('later')
  })
})
```

In `frontend/tests/publication-history.test.tsx`, add inside `describe('history', …)` (the file's `stub` and `openHistory` helpers render the Workspace-wide view with these publications):

```tsx
  test('scheduled work is grouped into today, tomorrow, and later', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-09-11T10:00:00+07:00'))
    stub([
      publication({ id: '99999999-9999-4999-8999-99999999a001', scheduledFor: '2026-09-11T13:00:00+00:00' }),
      publication({ id: '99999999-9999-4999-8999-99999999a002', scheduledFor: '2026-09-12T02:00:00+00:00' }),
    ])
    openHistory()

    const scheduled = await screen.findByRole('region', { name: 'Scheduled' })
    expect(within(scheduled).getByRole('list', { name: 'Today' })).toBeInTheDocument()
    expect(within(scheduled).getByRole('list', { name: 'Tomorrow' })).toBeInTheDocument()
    expect(within(scheduled).queryByRole('list', { name: 'Later' })).not.toBeInTheDocument()
    vi.useRealTimers()
  })
```

The fixture's zone is `Asia/Jakarta` (UTC+7): 13:00 UTC is 20:00 on the 11th, and 02:00 UTC on the 12th is 09:00 the next morning. Import `vi` and `within` if the file does not already.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/publishing-queue.test.tsx tests/publication-history.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement the buckets and the queue**

Add to `features/publishing/schedule.ts`:

```ts
/** Which day a scheduled instant falls on, as the member's chosen zone counts days. */
export function scheduleBucket(scheduledFor: string, now: Date, timeZone: string): 'today' | 'tomorrow' | 'later' {
  const target = calendarDay(new Date(scheduledFor), timeZone)
  const today = calendarDay(now, timeZone)
  if (target === today) return 'today'
  if (target === nextCalendarDay(today)) return 'tomorrow'
  return 'later'
}

/** `YYYY-MM-DD` of an instant in a zone (`en-CA` formats dates in that order). */
function calendarDay(instant: Date, timeZone: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(instant)
}

/** The day after a `YYYY-MM-DD` date, counted on the calendar rather than in hours. */
function nextCalendarDay(day: string): string {
  const [year = 0, month = 1, date = 1] = day.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, date + 1)).toISOString().slice(0, 10)
}
```

Adding 24 hours to `now` would be wrong on the night the clocks change, which is what the third test pins.

In `PublicationHistory.tsx`, reorder `GROUPS` to `attention`, `scheduled`, `progress`, `published`, `cancelled`. Each group already renders `<section aria-labelledby=…>` (a named region) around `<ul aria-label={group.label}>`; existing tests read those lists by name, so keep them. For the `scheduled` group only, the outer `ul aria-label="Scheduled"` holds one `li` per non-empty bucket, in the order Today, Tomorrow, Later, each with a `text-caption` uppercase heading and a nested `ul aria-label="Today"` (or `Tomorrow`, `Later`) of rows, bucketed with `scheduleBucket(item.scheduledFor ?? item.createdAt ?? new Date().toISOString(), new Date(), item.displayTimezone)`. Restyle: group heading `text-title` with a mono count; rows `divide-y divide-border`; the List/Calendar switch becomes `SegmentedControl label="View"`; each row leads with the account's avatar (`account.avatarUrl` as a 28 px rounded image when present, otherwise the provider initial in mono on `bg-secondary`), then account name and provider, then the scheduled time in mono.

In `app/dashboard/publishing/page.tsx`, change the description to `Schedule clips to your connected accounts.` and the action to `<Button asChild><Link href="/dashboard/publishing/new"><Plus aria-hidden="true" strokeWidth={1.75} />New publication</Link></Button>`.

- [ ] **Step 4: Lay the composer out in three columns**

In `NewPublication.tsx` when an export is chosen, render:

```tsx
<div className="grid gap-6 xl:grid-cols-[280px_minmax(0,1fr)_minmax(0,1.2fr)]">
  <ChosenExport artifact={chosen} onChange={() => setChosen(null)} />
  <PublicationComposer … />
</div>
```

and in `PublicationComposer.tsx` split its render into two wrappers so they occupy the second and third columns via `xl:contents`: the first wrapper holds the Destinations fieldset; the second holds the destination panels, the timing fieldset, the Review button, and the result and dialogs. `ChosenExport` shows the export's video inside a phone frame: `relative mx-auto aspect-[9/16] w-full max-w-[260px] overflow-hidden rounded-[28px] border-[6px] border-secondary bg-stage`, playing the render's signed download URL on demand (the existing preview control), with preset, revision, and duration beneath in mono. Destination choices show each account's avatar (`avatarUrl`, or the provider initial when it is null) beside the account name. Restyle fieldsets to `rounded-lg border p-4` with `legend` `text-title`; the confirmation `dialog` region becomes `rounded-lg border border-primary bg-card p-5`. Do not change any label, button text, or order of API calls, and keep the legacy query-string entry (`?editId=…&revision=…&renderArtifactId=…&durationMs=…`, produced by `publishHref`) preselecting the export.

- [ ] **Step 5: Run the publishing suites**

Run: `pnpm --dir frontend exec vitest run tests/publishing-queue.test.tsx tests/publication-history.test.tsx tests/publication-composer.test.tsx tests/creator-studio.test.tsx` → PASS.

---

### Task 3: Library

**Files:**
- Create: `frontend/features/editor/LookSample.tsx`
- Modify: `frontend/features/editor/LookCard.tsx` (uses `LookSample`), `frontend/features/templates/TemplateLibrary.tsx`, `frontend/features/assets/AssetBrowser.tsx`, `frontend/features/brand-kits/BrandKitEditor.tsx`
- Modify: `frontend/tests/design-rules.test.ts` (remove the `AssetBrowser` and `TemplateLibrary` allowlist entries), `frontend/tests/brand-campaign.test.tsx`, `frontend/tests/creator-studio.test.tsx`

**Interfaces:**
- Consumes: `captionFontStack`, `CAPTION_FONT_FAMILIES` (Plan 5 Task 3), `LookCard` (Plan 5 Task 8), `Poster`, `DesignedFrame` (Plan 3 Task 3), `formatClock` (Plan 3 Task 1), `SwatchPicker` (Plan 5 Task 2), `previewAssetApiV1AssetsAssetIdPreviewUrlGet` (already used by `AssetBrowser`).
- Produces: `LookSample({ captionStyle: CaptionStyle; captionMode?: CaptionMode })` with both types from `@/lib/api/generated/model` — the 9:16 graphite frame with the sample caption, moved out of `LookCard`. The editor's `TemplateDefinition` and the workspace `WorkspaceTemplateDefinition` both carry these generated types, so neither caller casts.

- [ ] **Step 1: Update the tests first**

- `tests/brand-campaign.test.tsx` "publishing a kit…" and the look tests: the "Publish a look" form now opens from a `Publish a look` button into a dialog; click it before filling the form, and query the form inside `screen.getByRole('dialog', { name: 'Publish a look' })`.
- `tests/creator-studio.test.tsx` "lists member media with its origin and signs a preview only when asked": the preview now opens in a sheet; after clicking `Preview B-roll from Episode 12`, query within `screen.getByRole('dialog', { name: /b-roll from episode 12/i })`.
- `tests/design-rules.test.ts`: delete the two library entries from `PENDING_REDESIGN`.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/brand-campaign.test.tsx tests/creator-studio.test.tsx tests/design-rules.test.ts`
Expected: FAIL — no dialog, no sheet, and the two files still hold gradients.

- [ ] **Step 3: Implement**

`frontend/features/editor/LookSample.tsx`:

```tsx
import type { CaptionMode, CaptionStyle } from '@/lib/api/generated/model'

import { captionFontStack } from './caption-fonts'

/** A caption look drawn on a small graphite 9:16 frame, the way a clip would carry it. */
export function LookSample({ captionStyle: style, captionMode = 'karaoke' }: { captionStyle: CaptionStyle; captionMode?: CaptionMode }) {
  return (
    <span aria-hidden="true" className="relative flex aspect-[9/16] items-end justify-center overflow-hidden rounded-sm bg-stage p-2">
      {captionMode === 'off' ? (
        <span className="mb-3 font-mono text-[11px] text-subtle-foreground">No captions</span>
      ) : (
        <span
          data-testid="look-sample"
          style={{
            fontFamily: captionFontStack(style.fontFamily),
            fontWeight: style.weight,
            fontStyle: style.italic ? 'italic' : 'normal',
            color: style.color,
            textAlign: style.align,
            letterSpacing: `${style.letterSpacing / 4}px`,
            backgroundColor: style.backgroundEnabled ? style.backgroundColor : undefined,
          }}
          className="px-1 text-[15px] leading-tight"
        >
          Say it <span style={{ color: captionMode === 'karaoke' ? style.highlightColor : style.color }}>loud</span>
        </span>
      )}
    </span>
  )
}
```

`LookCard` replaces its inline `aspect-[9/16]` frame with `<LookSample captionStyle={template.captionStyle} captionMode={template.captionMode} />`; its `look-sample` test id, font stack, and letter spacing are unchanged, so `tests/style-panel.test.tsx` keeps passing.

`TemplateLibrary.tsx`: delete `LookPreview`; each look renders as a card `rounded-lg border bg-card p-2` with `<LookSample captionStyle={template.definition.captionStyle} captionMode={template.definition.captionMode} />`, the name, `v{version}` in mono, and the existing archive control; the grid is `grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5`. Move the "Publish a look" form into a `Dialog` titled `Publish a look`, opened by a `Button` of the same name in the page header's actions. Keep "Show archived looks" as a `Switch` with that label.

`AssetBrowser.tsx`: the list stays `ul aria-label="Assets"`, now `grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4`. A `source` asset's tile uses `<Poster projectId={asset.projectId} durationMs={asset.durationMs ?? undefined} />`; every other kind uses `<DesignedFrame durationMs={asset.durationMs ?? undefined} />` with the kind label, because storyboards exist only for sources in version 1 (record this in `PROGRESS.md` as the one tile type without real frames). Under each tile: kind label, project name, and `formatClock(durationMs)` in mono. The existing preview button (`Preview <kind> from <project>`) opens a `Sheet` (`side="right"`) titled `<kind> from <project>`, holding the signed preview player (unchanged query), metadata in mono (`contentType`, `width × height`, size), and the provenance block. The Project and Type selects stay. Remove every gradient class.

`BrandKitEditor.tsx`: palette swatches grow to `size-10 rounded-sm border border-input` with the hex in mono under each; the logo slot shows the logo when `definition.logoAssetId` is not null (an `img` from `previewAssetApiV1AssetsAssetIdPreviewUrlGet(logoAssetId, { workspace_id })`, `h-12 w-auto object-contain` on `bg-stage`) and a `text-caption text-subtle-foreground` "No logo" slot otherwise; fonts are specimens (`text-h2` sample "Aa Bb 123") with the family name in mono, drawn in the caption face when the kit names one:

```tsx
function isCaptionFont(family: string): family is CaptionFontFamily {
  return (CAPTION_FONT_FAMILIES as readonly string[]).includes(family)
}

// in the kit card, replacing the inline `${family}, ui-sans-serif` style
<p className="text-h2" style={isCaptionFont(family) ? { fontFamily: captionFontStack(family) } : undefined}>Aa Bb 123</p>
```

The version appears as mono `v{version}` beside the name with `Updated <formatInstant(updatedAt)>` under it — a kit read carries only its current version, so there is no version list to show; state this in `PROGRESS.md` against the spec's "version list". The edit form uses `Input`, `SwatchPicker` for each colour, and `Button`.

- [ ] **Step 4: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/brand-campaign.test.tsx tests/creator-studio.test.tsx tests/design-rules.test.ts tests/style-panel.test.tsx` → PASS.

---

### Task 4: Settings

**Files:**
- Modify: `frontend/features/settings/SettingsNav.tsx`, `frontend/features/settings/GeneralSettings.tsx`, `frontend/app/dashboard/settings/page.tsx`, `frontend/app/dashboard/team/page.tsx`, `frontend/app/dashboard/settings/connections/page.tsx`
- Create: `frontend/tests/settings-layout.test.tsx`

**Interfaces:**
- Produces: `SettingsLayout({ description: string; children: ReactNode })` in `SettingsNav.tsx` (replaces `SettingsHeader`) — `PageHeader` plus a two-column layout with `nav "Settings sections"` linking General, Members, Connections, Sessions (`/dashboard/settings#sessions`), Usage (`/dashboard/settings#usage`); `GeneralSettings` sections carry `id="usage"` and `id="sessions"`; usage entries render as meters (`role="meter"` with `aria-valuenow`, `aria-valuemax`, and the existing group label).

- [ ] **Step 1: Write the failing test**

`frontend/tests/settings-layout.test.tsx`:

```tsx
import { screen, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'

import { SettingsLayout } from '@/features/settings/SettingsNav'

import { renderWithApi } from './support/api'

vi.mock('next/navigation', () => ({ usePathname: () => '/dashboard/team' }))

describe('Settings layout', () => {
  test('a side navigation reaches every settings section and marks the current page', () => {
    renderWithApi(
      <SettingsLayout description="Your workspace, its people, and where you are signed in.">
        <p>Members body</p>
      </SettingsLayout>,
    )

    const nav = screen.getByRole('navigation', { name: 'Settings sections' })
    expect(within(nav).getAllByRole('link').map((link) => [link.textContent, link.getAttribute('href')])).toEqual([
      ['General', '/dashboard/settings'],
      ['Members', '/dashboard/team'],
      ['Connections', '/dashboard/settings/connections'],
      ['Sessions', '/dashboard/settings#sessions'],
      ['Usage', '/dashboard/settings#usage'],
    ])
    expect(within(nav).getByRole('link', { name: 'Members' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('Members body')).toBeInTheDocument()
  })
})
```

Add to `frontend/tests/creator-studio.test.tsx` "shows monthly usage…": `expect(screen.getByRole('meter', { name: 'Analyses' })).toHaveAttribute('aria-valuenow', '4')`.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/settings-layout.test.tsx tests/creator-studio.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

`SettingsNav.tsx`:

```tsx
'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import type { ReactNode } from 'react'

import { PageHeader } from '@/components/page-header'
import { cn } from '@/lib/utils'

const SECTIONS = [
  { href: '/dashboard/settings', label: 'General' },
  { href: '/dashboard/team', label: 'Members' },
  { href: '/dashboard/settings/connections', label: 'Connections' },
  { href: '/dashboard/settings#sessions', label: 'Sessions' },
  { href: '/dashboard/settings#usage', label: 'Usage' },
] as const

/** Every Settings page: one title, a side list of sections, and the page's own content. */
export function SettingsLayout({ description, children }: { description: string; children: ReactNode }) {
  const pathname = usePathname()
  return (
    <div className="space-y-2">
      <PageHeader title="Settings" description={description} />
      <div className="grid gap-8 md:grid-cols-[200px_minmax(0,1fr)]">
        <nav aria-label="Settings sections">
          <ul className="flex gap-1 overflow-x-auto md:flex-col">
            {SECTIONS.map((section) => {
              const current = !section.href.includes('#') && pathname === section.href
              return (
                <li key={section.href}>
                  <Link
                    href={section.href}
                    aria-current={current ? 'page' : undefined}
                    className={cn(
                      'block whitespace-nowrap rounded-md px-3 py-2 text-small font-medium transition-colors duration-fast ease-signal',
                      current ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
                    )}
                  >
                    {section.label}
                  </Link>
                </li>
              )
            })}
          </ul>
        </nav>
        <div className="min-w-0 space-y-10">{children}</div>
      </div>
    </div>
  )
}
```

Replace `SettingsHeader` usage in the three settings routes with `SettingsLayout` wrapping their content. In `GeneralSettings.tsx`, give the usage `Section` `id="usage"` and the sessions `Section` `id="sessions"` (add an `id` prop to `Section` in `components/page-header.tsx`, applied to its `section`), and render each usage entry as:

```tsx
<li key={entry.resource} role="group" aria-label={USAGE_LABELS[entry.resource]} className="space-y-2 rounded-lg border bg-card p-4">
  <div className="flex items-baseline justify-between gap-2">
    <span className="text-small font-medium">{USAGE_LABELS[entry.resource]}</span>
    <span className="font-mono text-caption tabular text-muted-foreground">{formatConsumed(entry.consumed)} of {entry.limit}</span>
  </div>
  <div role="meter" aria-label={USAGE_LABELS[entry.resource]} aria-valuemin={0} aria-valuemax={entry.limit} aria-valuenow={entry.consumed} className="h-1.5 overflow-hidden rounded-full bg-secondary">
    <div className={cn('h-full', entry.consumed >= entry.limit ? 'bg-destructive' : entry.consumed / entry.limit >= 0.8 ? 'bg-warning' : 'bg-foreground')} style={{ width: `${Math.min(100, (entry.consumed / Math.max(1, entry.limit)) * 100)}%` }} />
  </div>
</li>
```

The `li` keeps `role="group"` and its `aria-label`, so the existing `getByRole('group', { name: 'Analyses' })` query with text `4 of 30` still holds beside the new meter query. Delete the old bar markup the meter replaces.

- [ ] **Step 4: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/settings-layout.test.tsx tests/creator-studio.test.tsx tests/team-settings.test.tsx tests/social-connections.test.tsx` → PASS.

---

### Task 5: Public pages and the product loop

**Files:**
- Create: `frontend/components/marketing-frame.tsx`, `frontend/e2e/marketing-capture.spec.ts`, `frontend/public/marketing/` (captured files)
- Modify: `frontend/components/public-frame.tsx`, `frontend/app/page.tsx`, `frontend/app/signin/page.tsx`, `frontend/app/demo/page.tsx`, `frontend/features/team/InviteAcceptance.tsx`
- Modify: `frontend/tests/smoke.test.tsx`, `frontend/tests/dashboard.test.tsx` (demo), `frontend/tests/design-rules.test.ts` (empty the allowlist)

**Interfaces:**
- Produces: `MarketingFrame({ still: string; video?: { webm: string; mp4: string }; alt: string; className?: string })` — plays a muted looping video when given and motion is allowed, otherwise shows the still.

- [ ] **Step 1: Update the tests**

In `tests/smoke.test.tsx` "landing page": expect `getByRole('heading', { level: 1 })` to have text `Long video in. Clips worth posting out.`, the `Get started` link to point at `/signin`, and `getByRole('link', { name: /see an example/i })` at `/demo`; keep `getByRole('link', { name: /sign in/i })`.

In `tests/dashboard.test.tsx` "the public demo…": keep "calls no private endpoint" and the `Example content` label; replace any gradient-class assertion with `expect(container.querySelector('[class*="gradient"]')).toBeNull()`.

In `tests/design-rules.test.ts`: set `PENDING_REDESIGN` to `{}`, and change "every pending file still needs its redesign" to assert `Object.keys(PENDING_REDESIGN)` is empty.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/smoke.test.tsx tests/dashboard.test.tsx tests/design-rules.test.ts`
Expected: FAIL.

- [ ] **Step 3: Capture the product imagery**

`frontend/e2e/marketing-capture.spec.ts`:

```ts
import { expect, test } from '@playwright/test'

/**
 * Opt-in: records the product loop and stills for the public pages from a real account.
 *
 * `CLIPAH_MARKETING_STORAGE_STATE` is a Playwright storage state saved after signing in by
 * hand (`pnpm exec playwright codegen --save-storage=…`), and `CLIPAH_MARKETING_PROJECT_ID`
 * names a Project with real media and moments. Nothing runs without both.
 */
const STATE = process.env.CLIPAH_MARKETING_STORAGE_STATE
const PROJECT = process.env.CLIPAH_MARKETING_PROJECT_ID

test.skip(STATE === undefined || PROJECT === undefined, 'marketing capture is opt-in')
test.use({ storageState: STATE, viewport: { width: 1440, height: 900 }, video: { mode: 'on', size: { width: 1440, height: 900 } } })

test('record review mode and the editor', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto(`/dashboard/projects/${PROJECT}/review`)
  await expect(page.getByRole('region', { name: 'Moment' })).toBeVisible()
  await page.waitForTimeout(1_500)
  await page.screenshot({ path: 'public/marketing/review.png' })
  await page.keyboard.press('j')
  await page.waitForTimeout(1_500)
  await page.keyboard.press('j')
  await page.waitForTimeout(1_500)
  await page.keyboard.press('e')
  await expect(page).toHaveURL(/\/editor\//)
  await expect(page.getByRole('region', { name: /^timeline$/i })).toBeVisible()
  await page.waitForTimeout(1_500)
  await page.screenshot({ path: 'public/marketing/edit.png' })
  await page.keyboard.press(' ')
  await page.waitForTimeout(3_000)
  await page.goto('/dashboard/publishing')
  await page.waitForTimeout(1_500)
  await page.screenshot({ path: 'public/marketing/publish.png' })
})
```

Ask the owner to create the storage state (it is their account) and name the Project, then run from `frontend/`:

```bash
CLIPAH_MARKETING_STORAGE_STATE=../.auth/owner.json CLIPAH_MARKETING_PROJECT_ID=<project uuid> CLIPAH_E2E_BASE_URL=http://localhost:3000 pnpm exec playwright test e2e/marketing-capture.spec.ts --project=chromium
```

Convert the recorded video (Playwright writes it under `test-results/`) with the pinned FFmpeg in the media image, trimming to the first 12 seconds and scaling to 1280 px wide:

```bash
docker compose -f infra/compose.yaml run --rm --no-deps -v "$PWD/frontend:/work" worker-render sh -c '
  cd /work && SRC=$(ls -t test-results/*/video.webm | head -1) &&
  ffmpeg -y -v error -i "$SRC" -t 12 -vf scale=1280:-2 -an -c:v libvpx-vp9 -b:v 900k public/marketing/studio-loop.webm &&
  ffmpeg -y -v error -i "$SRC" -t 12 -vf scale=1280:-2 -an -c:v libx264 -pix_fmt yuv420p -crf 28 -movflags +faststart public/marketing/studio-loop.mp4'
ls -l frontend/public/marketing
```

Expected: two stills, two videos, total under 4 MB. If the total exceeds 4 MB, lower `-b:v`/raise `-crf` and re-encode. `.auth/` must be in `.gitignore` (add it) — a session must never be committed. If the owner declines the capture, commit no media and let `MarketingFrame` render the still captured by `design-screens.spec.ts` (`docs/design/signal/after/review-1440.png` copied to `public/marketing/review.png`) with no video.

- [ ] **Step 4: Implement `MarketingFrame` and the pages**

`frontend/components/marketing-frame.tsx`:

```tsx
'use client'

import { useReducedMotion } from '@/features/media/use-reduced-motion'
import { cn } from '@/lib/utils'

/** Real product footage: a muted loop when motion is welcome, otherwise the still. */
export function MarketingFrame({
  still,
  video,
  alt,
  className,
}: {
  still: string
  video?: { webm: string; mp4: string }
  alt: string
  className?: string
}) {
  const reduced = useReducedMotion()
  return (
    <div className={cn('overflow-hidden rounded-lg border border-line-strong bg-stage', className)}>
      {video === undefined || reduced ? (
        // Static marketing stills are served from /public and need no optimizer.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={still} alt={alt} className="block w-full" />
      ) : (
        <video autoPlay muted loop playsInline poster={still} aria-label={alt} className="block w-full">
          <source src={video.webm} type="video/webm" />
          <source src={video.mp4} type="video/mp4" />
        </video>
      )}
    </div>
  )
}
```

`frontend/app/page.tsx`:

```tsx
import Link from 'next/link'

import { MarketingFrame } from '@/components/marketing-frame'
import { PublicFrame } from '@/components/public-frame'

const FRAMES = [
  { still: '/marketing/review.png', title: 'Review', text: 'Every suggested moment, ranked, with the reason it works and the words around it.' },
  { still: '/marketing/edit.png', title: 'Edit', text: 'Captions you edit as text, a real timeline, and the crop on the picture itself.' },
  { still: '/marketing/publish.png', title: 'Publish', text: 'Download the file, or schedule it to YouTube Shorts, Instagram Reels, or TikTok.' },
] as const

/** The public landing page: what a creator gets, shown with the product itself. */
export default function LandingPage() {
  return (
    <PublicFrame>
      <main className="mx-auto w-full max-w-studio px-4 sm:px-6">
        <section className="grid items-center gap-10 py-12 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:py-20">
          <div className="space-y-6">
            <h1 className="font-display text-display text-balance lg:text-hero">Long video in. Clips worth posting out.</h1>
            <p className="max-w-xl text-body text-muted-foreground">
              Drop in a podcast, interview, or stream. Clipah transcribes it, finds the moments worth sharing, and gives you a studio to finish and publish them.
            </p>
            <div className="flex flex-wrap items-center gap-4">
              <Link href="/signin" className="inline-flex h-11 items-center rounded-md bg-primary px-5 text-body font-semibold text-primary-foreground hover:bg-primary-hover">
                Get started
              </Link>
              <Link href="/demo" className="text-small font-semibold text-muted-foreground hover:text-foreground">
                See an example
              </Link>
            </div>
          </div>
          <MarketingFrame still="/marketing/review.png" video={{ webm: '/marketing/studio-loop.webm', mp4: '/marketing/studio-loop.mp4' }} alt="Clipah's review mode and editor in use" />
        </section>
        <section aria-label="What you do in Clipah" className="space-y-16 py-12">
          {FRAMES.map((frame, index) => (
            <div key={frame.title} className="grid items-center gap-8 lg:grid-cols-2">
              <div className={index % 2 === 1 ? 'lg:order-2' : undefined}>
                <h2 className="font-display text-h1">{frame.title}</h2>
                <p className="mt-3 max-w-md text-body text-muted-foreground">{frame.text}</p>
              </div>
              <MarketingFrame still={frame.still} alt={`Clipah ${frame.title.toLowerCase()} screen`} />
            </div>
          ))}
        </section>
      </main>
    </PublicFrame>
  )
}
```

If Step 3 produced no video, drop the `video` prop.

`frontend/app/signin/page.tsx` becomes a split layout: `PublicFrame signInLink={false}` containing `grid min-h-[calc(100vh-8rem)] lg:grid-cols-[3fr_2fr]`; left `MarketingFrame still="/marketing/edit.png"` (hidden below `lg`); right a column (not a floating card) with `h1 font-display text-h1` "Sign in to Clipah", the sentence "New here? Signing in creates your studio, ready for your first video.", the Google button following Google's branding (white `#FFFFFF` background, `#1F1F1F` text, the Google "G" mark SVG from Google's brand guidelines embedded inline as a React SVG element, "Sign in with Google"), and "Not ready yet? See an example". `InviteAcceptance` uses the same split with `MarketingFrame still="/marketing/review.png"`.

`frontend/app/demo/page.tsx`: keep `DEMO_CANDIDATES` without the `gradient` field; render the review-mode layout statically — a list of the three example moments on the left, a `DesignedFrame` 9:16 stage in the centre with the hook in caption type over it, and the reason and score bars on the right — plus the `Example content — not a real project` label as plain `text-caption font-medium uppercase tracking-wide text-warning` text (no pill). No API call.

`frontend/components/public-frame.tsx`: header wordmark becomes the `Wordmark` component; `Sign in` link `rounded-md border border-line-strong px-3 py-2 text-small font-semibold`; footer `text-caption text-subtle-foreground`; max width `max-w-studio`.

- [ ] **Step 5: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/smoke.test.tsx tests/dashboard.test.tsx tests/design-rules.test.ts tests/team-settings.test.tsx` → PASS.

---

### Task 6: Copy pass

**Files:**
- Create: `frontend/tests/copy-rules.test.ts`
- Modify: every file the test reports

- [ ] **Step 1: Write the failing copy guard**

`frontend/tests/copy-rules.test.ts`:

```ts
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'

import { describe, expect, test } from 'vitest'

const ROOT = resolve(__dirname, '..')

/** Phrases that describe the system to members, or apologise, instead of saying what to do. */
const BANNED: Array<[string, RegExp]> = [
  ['internal architecture', /a batch never hides a failure|so it opens only for a signed-in member|writes its type into a clip and records the version/i],
  ['millisecond labels', /\(ms\)/],
  ['"successfully"', /\bsuccessfully\b/i],
  ['"Please"', /['">]\s*Please\b/],
  ['"Something went wrong. Please try again."', /Something went wrong\. Please try again\./],
  ['"No preview yet"', /No preview yet/],
]

function uiFiles(): string[] {
  const files: string[] = []
  const walk = (directory: string) => {
    for (const entry of readdirSync(directory)) {
      const path = join(directory, entry)
      const rel = relative(ROOT, path)
      if (rel.startsWith('lib/api/generated')) continue
      if (statSync(path).isDirectory()) walk(path)
      else if (entry.endsWith('.tsx')) files.push(rel)
    }
  }
  for (const directory of ['app', 'components', 'features']) walk(join(ROOT, directory))
  return files
}

describe('copy rules', () => {
  test.each(BANNED)('no screen uses %s', (_name, pattern) => {
    expect(uiFiles().filter((file) => pattern.test(readFileSync(join(ROOT, file), 'utf8')))).toEqual([])
  })
})
```

The API client's own fallback message lives in `lib/`, which is not scanned; the test-support stub's message stays as the backend's wording. Change the UI's fallback in `ErrorNotice` (Plan 1 already uses "Something went wrong. Try again.") and any other component-level copy the test reports.

- [ ] **Step 2: Run it and fix every hit**

Run: `pnpm --dir frontend exec vitest run tests/copy-rules.test.ts`
Expected: FAIL with a list of files. For each hit, rewrite the sentence following the copy guide (outcome first, verb first, no filler) and update any test that asserted the old sentence to the new one. Do not add exemptions.

Also apply these replacements the guide names explicitly:

| Where | Before | After |
| --- | --- | --- |
| `features/auth/require-session.tsx` | This area belongs to a Workspace, so it opens only for a signed-in member. | Sign in to open your studio. |
| `features/templates/TemplateLibrary.tsx` description | Reusable looks for captions and drawn text. Applying one writes its type into a clip and records the version it came from. | Caption looks you can reuse on any clip. |
| `features/clips/ClipBrowser.tsx` description | Every moment from your projects — suggested, being edited, or ready to share. | Every moment from your projects, from suggestion to finished file. |
| `app/dashboard/search/page.tsx` description | Find a project, a moment in a transcript, a clip, or the copy written for it. Transcript results open the video at that moment. | Find anything by what was said. Transcript results open the video at that moment. |

- [ ] **Step 3: Run the whole suite**

Run: `pnpm test` → PASS.

---

### Task 7: Verification and records

**Files:**
- Create: `frontend/tests/support/axe.ts`
- Modify: the suites listed in Step 1, `frontend/tests/accessibility-quality.test.tsx`
- Modify: `frontend/e2e/design-screens.spec.ts` (optional LCP measurement)
- Modify: `PROGRESS.md`, `plan.md` (Section 14)

- [ ] **Step 1: Axe checks of rebuilt screens**

Each rebuilt screen already has a suite that renders it with realistic stubs, so the check goes there instead of into a second copy of those stubs. Create `frontend/tests/support/axe.ts`:

```ts
import { axe } from 'vitest-axe'
import { expect } from 'vitest'

/**
 * Assert a rendered screen has no axe violations.
 *
 * Colour contrast is off because jsdom computes no styles; `tests/design-tokens.test.ts`
 * proves contrast from the tokens instead.
 */
export async function expectAccessible(container: Element): Promise<void> {
  const results = await axe(container, { rules: { 'color-contrast': { enabled: false } } })
  expect(results.violations.map((violation) => `${violation.id}: ${violation.nodes[0]?.target.join(' ')}`)).toEqual([])
}
```

Then add `await expectAccessible(container)` (taking `container` from the test's `render`/`renderWithApi` result) to the first test in each suite below that renders the whole screen, immediately after that test's first `findBy…` of the landmark named here:

| Suite | Screen | Assert after |
| --- | --- | --- |
| `tests/dashboard.test.tsx` | Home (`WorkspaceOverview`) with projects | the Continue editing region |
| `tests/dashboard.test.tsx` | Demo page | the `Example content` label |
| `tests/projects.test.tsx` | Projects grid | the project list |
| `tests/projects.test.tsx` | Project page, ready | the Moments tab panel |
| `tests/review-mode.test.tsx` | Review mode | region `Moment` |
| `tests/editor-basic.test.tsx` | Editor | region `Stage` |
| `tests/publication-history.test.tsx` | Publishing queue | list `Scheduled` |
| `tests/publication-composer.test.tsx` | Composer | the Destinations group |
| `tests/creator-studio.test.tsx` | Settings general page and Assets | group `Analyses`; list `Assets` |
| `tests/brand-campaign.test.tsx` | Looks and Brand kits | list `Looks`; list `Brand kits` |
| `tests/smoke.test.tsx` | Landing page | the level-1 heading |

Replace the import in `tests/accessibility-quality.test.tsx` with the helper too, so there is one way to run axe.

Run: `pnpm --dir frontend exec vitest run tests/dashboard.test.tsx tests/projects.test.tsx tests/review-mode.test.tsx tests/editor-basic.test.tsx tests/publication-history.test.tsx tests/publication-composer.test.tsx tests/creator-studio.test.tsx tests/brand-campaign.test.tsx tests/smoke.test.tsx tests/accessibility-quality.test.tsx`. Fix every violation in the component (labels, roles, heading order, duplicate landmarks), never by disabling a rule.

- [ ] **Step 2: Run every gate**

From the repository root: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`.
From `backend/` against the disposable database: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest -q --cov=clipah --cov-fail-under=90`.
Expected: all PASS; record counts and coverage.

- [ ] **Step 3: Responsive, browser, and performance runs**

Rebuild all services from the working tree, then from `frontend/`:

```bash
CLIPAH_CAPTURE_SCREENS=after CLIPAH_E2E_BASE_URL=http://localhost:3000 pnpm exec playwright test e2e/design-screens.spec.ts --project=chromium
CLIPAH_E2E_BASE_URL=http://localhost:3000 pnpm exec playwright test --project=chromium --project=webkit
```

Expected: 57 screenshots (19 routes × 3 widths, including review) with no sideways overflow; the browser suite passes in both engines with collaboration off, and the collaboration specs pass with `CLIPAH_COLLABORATION_ENABLED=true`, matching the split recorded in `PROGRESS.md` for the previous redesign. Report fixture-backed results separately from any live-provider run.

Measure Home's largest contentful paint on the production build: add to `design-screens.spec.ts`, behind `CLIPAH_MEASURE_LCP=1`, a step that loads `/dashboard` at 1440 px and reads

```ts
const lcp = await page.evaluate(() => new Promise<number>((resolve) => {
  new PerformanceObserver((list) => resolve(list.getEntries().at(-1)?.startTime ?? 0)).observe({ type: 'largest-contentful-paint', buffered: true })
}))
expect(lcp).toBeLessThan(2_500)
```

and run it once. Check one real storyboard sheet's size in MinIO (`docker compose -f infra/compose.yaml exec minio mc ls --recursive local/clipah-local | grep storyboard-v1`) is under 300 KB.

- [ ] **Step 4: Walk the acceptance checklist by hand**

On the running stack with a real Project (the owner's, with permission), in Chromium at 1440 px and at 390 px, confirm and record each line of `redesign-plan-v2.md` → Verification and Acceptance: identity (no banned pattern on any route; both fonts listed by `document.fonts`), media (real frames, hover scrub with no per-frame request), core journey (create → import → stages → review with keys → caption edit → restyle → crop → export → download, with no identifier typed and no millisecond read), editor invariants, backend invariants (a failed preview job leaves transcription and analysis untouched), limits (20 page loads in a minute without a rate-limit error), accessibility (keyboard-only pass through review mode and the timeline; reduced motion stops scrubbing and the landing loop), and responsive behaviour.

- [ ] **Step 5: Record the work**

Add Section 14 to `plan.md`, "Post-Rebuild Extension — Signal Studio Redesign": the identity, the backend additions (migration `0023`, `PREVIEW_MEDIA`, storyboard and waveform v1, storyboard/waveform/transcript reads, `order=recent` and `editUpdatedAt`, backfill, read limit 300), the new route, and the refinements recorded in Plans 1–6. Append "Signal Studio redesign — Plan 6, surfaces and verification" to `PROGRESS.md` with gate output, screenshot folders, browser-suite counts per engine, LCP, the acceptance checklist results, anything not verified and why, and the owner commit message `feat: finish the signal studio redesign`. Then add the spec's closing entry, "Post-rebuild extension — Signal Studio redesign", linking the six plan entries and listing the known limits carried by design: asset tiles other than sources show designed frames (storyboards exist only for sources); publishing queue entries have no poster (publications carry no clip link); brand kits show only their current version (the read carries no history). If the owner prefers one commit for the whole redesign, the spec's message is `feat: signal studio redesign`. Do not run `git commit`.
