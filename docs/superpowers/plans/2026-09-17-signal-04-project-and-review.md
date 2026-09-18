# Project Page and Review Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (the repository owner requires inline execution without subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a ready Project read as its moments — a poster grid beside the source and its transcript — and add a keyboard-driven review mode for deciding which moments to edit.

**Architecture:** Transcript text work is pure (`lib/media/transcript.ts`) and read through one hook. Moment presentation is one card (`MomentCard`) and one side sheet (`WhyThisMoment`), reused by the Project page and review mode. The Project page composes an upload/processing region, a moments grid, and a sticky source column; review mode is a new route that reads the same candidates query, so switching between them costs no extra request.

**Tech Stack:** Next.js 15 App Router, React 19, TanStack Query 5, Radix Dialog (Sheet), Tailwind CSS 3, Vitest + Testing Library, Playwright.

**Spec:** `redesign-plan-v2.md` → Screens → Project, Review mode. Plan index: `docs/superpowers/plans/2026-09-17-signal-studio-redesign.md`.

## Global Constraints

- Everything in the plan index's "Global constraints" applies.
- Depends on Plans 1–3: primitives, `Poster`, `StageBar`, `url-tabs`, `formatClock`, and the Plan 2 transcript read (`transcriptApiV1ProjectsProjectIdTranscriptGet`, `TranscriptResponse`, `TranscriptWordResponse`).
- The candidate list keeps Task 20's guarantees: the whole exposed set is read before any sort or filter is offered, sorting and filtering happen over that complete set, a 404 reads as "No clips to review yet", and every model-written field renders as text.
- Review mode adds no saved state. Keys: `Space` play or pause, `J` next moment, `K` previous moment, `E` or `Enter` edit this clip, `Esc` back to the Project, `?` shortcut sheet. No key fires while focus is in a text field.
- Preview playback keeps Task 20's rule: a fresh signed proxy URL per open, playing exactly the candidate range and stopping at its end (unless loop is on).
- Owner commit message for this plan: `feat: add moments-first project page and review mode`.

## File map

| File | Responsibility |
| --- | --- |
| `frontend/lib/media/transcript.ts` | Join words, segment by sentence and speaker, window around a range |
| `frontend/features/media/use-transcript.ts` | One Project's transcript read |
| `frontend/features/clips/ScoreBars.tsx` | The seven dimensions as labelled bars |
| `frontend/features/clips/WhyThisMoment.tsx` | Side sheet: reason, payoff, excerpt, bars, dependencies, opportunities, tags, look choice |
| `frontend/features/clips/MomentCard.tsx` | Poster, rank, condensed score, hook, warnings, Edit clip, Preview, Show in source |
| `frontend/features/clips/ClipList.tsx` | Uses `MomentCard`; exposes selection |
| `frontend/features/clips/use-open-edit.ts` | The one "open this candidate's Edit" mutation |
| `frontend/features/clips/use-project-candidates.ts` | The whole exposed candidate set of one Project, read once and shared |
| `frontend/features/projects/source-column.tsx` | Sticky source player and transcript with moment ranges |
| `frontend/features/projects/project-detail.tsx` | Header action, processing region, moments layout, URL tabs |
| `frontend/features/review/ReviewMode.tsx`, `frontend/features/review/use-review-keys.ts` | Review mode and its keyboard map |
| `frontend/app/dashboard/projects/[projectId]/review/page.tsx` | The route |

---

### Task 1: Transcript helpers and the transcript read

**Files:**
- Create: `frontend/lib/media/transcript.ts`, `frontend/features/media/use-transcript.ts`
- Create: `frontend/tests/transcript.test.ts`

**Interfaces:**
- Produces:
  - `joinWords(words: readonly TranscriptWordResponse[]): string`.
  - `interface TranscriptSegment { startMs: number; endMs: number; speaker: string; text: string }`; `transcriptSegments(words): TranscriptSegment[]` (break at sentence-ending punctuation or a speaker change).
  - `overlaps(segment: { startMs: number; endMs: number }, startMs: number, endMs: number): boolean`.
  - `transcriptWindow(words, startMs: number, endMs: number, contextMs?: number): { before: string; inside: string; after: string }` (default context 10,000 ms).
  - `useTranscript(projectId: string, options: { enabled: boolean }): UseQueryResult<TranscriptResponse, ApiError>`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/transcript.test.ts`:

```ts
import { describe, expect, test } from 'vitest'

import type { TranscriptWordResponse } from '@/lib/api/generated/model'
import { joinWords, overlaps, transcriptSegments, transcriptWindow } from '@/lib/media/transcript'

function word(id: number, text: string, startMs: number, punctuation = '', speaker = 'SPEAKER_00'): TranscriptWordResponse {
  return { id: `w${id}`, text, punctuation, startMs, endMs: startMs + 400, speaker }
}

const words = [
  word(1, 'So', 0),
  word(2, 'here', 500, ','),
  word(3, 'it', 1_000),
  word(4, 'is', 1_500, '.'),
  word(5, 'Really', 2_000, '?', 'SPEAKER_01'),
  word(6, 'Yes', 12_500, '.', 'SPEAKER_00'),
  word(7, 'Later', 30_000),
]

describe('transcript text', () => {
  test('joins words with their own punctuation and nothing invented', () => {
    expect(joinWords(words.slice(0, 4))).toBe('So here, it is.')
  })

  test('breaks segments at sentence ends and at speaker changes', () => {
    expect(transcriptSegments(words).map((segment) => [segment.speaker, segment.text, segment.startMs, segment.endMs])).toEqual([
      ['SPEAKER_00', 'So here, it is.', 0, 1_900],
      ['SPEAKER_01', 'Really?', 2_000, 2_400],
      ['SPEAKER_00', 'Yes.', 12_500, 12_900],
      ['SPEAKER_00', 'Later', 30_000, 30_400],
    ])
  })

  test('a range overlaps a segment it touches on either side', () => {
    expect(overlaps({ startMs: 0, endMs: 1_900 }, 1_800, 5_000)).toBe(true)
    expect(overlaps({ startMs: 0, endMs: 1_900 }, 1_900, 5_000)).toBe(false)
  })

  test('a window splits what was said before, inside, and after a range', () => {
    expect(transcriptWindow(words, 1_000, 2_400, 11_000)).toEqual({
      before: 'So here,',
      inside: 'it is. Really?',
      after: 'Yes.',
    })
  })
})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/transcript.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

`frontend/lib/media/transcript.ts`:

```ts
import type { TranscriptWordResponse } from '@/lib/api/generated/model'

type Word = Pick<TranscriptWordResponse, 'text' | 'punctuation' | 'startMs' | 'endMs' | 'speaker'>

/** One run of speech that reads as a line: one speaker, up to a sentence end. */
export interface TranscriptSegment {
  startMs: number
  endMs: number
  speaker: string
  text: string
}

const SENTENCE_END = /[.!?…]["'”’)\]]*$/

/** Words as they were said, each with the punctuation transcription recorded. */
export function joinWords(words: readonly Word[]): string {
  return words.map((entry) => `${entry.text}${entry.punctuation}`).join(' ')
}

/** Group words into sentence-sized lines, breaking at a sentence end or a new speaker. */
export function transcriptSegments(words: readonly Word[]): TranscriptSegment[] {
  const segments: TranscriptSegment[] = []
  let current: Word[] = []
  const flush = () => {
    const first = current[0]
    const last = current.at(-1)
    if (first !== undefined && last !== undefined) {
      segments.push({ startMs: first.startMs, endMs: last.endMs, speaker: first.speaker, text: joinWords(current) })
    }
    current = []
  }
  for (const entry of words) {
    if (current[0] !== undefined && current[0].speaker !== entry.speaker) flush()
    current.push(entry)
    if (SENTENCE_END.test(entry.punctuation)) flush()
  }
  flush()
  return segments
}

/** Whether a stretch of speech shares any time with a range. */
export function overlaps(segment: { startMs: number; endMs: number }, startMs: number, endMs: number): boolean {
  return segment.startMs < endMs && segment.endMs > startMs
}

/** What was said shortly before, during, and shortly after a range. */
export function transcriptWindow(
  words: readonly Word[],
  startMs: number,
  endMs: number,
  contextMs = 10_000,
): { before: string; inside: string; after: string } {
  const inside = words.filter((entry) => overlaps(entry, startMs, endMs))
  const before = words.filter((entry) => entry.endMs <= startMs && startMs - entry.startMs <= contextMs)
  const after = words.filter((entry) => entry.startMs >= endMs && entry.endMs - endMs <= contextMs)
  return { before: joinWords(before), inside: joinWords(inside), after: joinWords(after) }
}
```

Check the window test: range 1,000–2,400 → inside words 3, 4, 5 (`it is. Really?`); before: words that end by 1,000 and start within 11 s → 1, 2 (`So here,`); after: words that start at or after 2,400 and end within 11 s of it → word 6 (`Yes.`), not word 7.

`frontend/features/media/use-transcript.ts`:

```ts
'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { TranscriptResponse } from '@/lib/api/generated/model'
import { transcriptApiV1ProjectsProjectIdTranscriptGet } from '@/lib/api/generated/studio/studio'

/** One Project's transcript; it never changes after analysis, so it is read once per visit. */
export function useTranscript(
  projectId: string,
  { enabled }: { enabled: boolean },
): UseQueryResult<TranscriptResponse, ApiError> {
  const { active } = useWorkspaceScope()
  return useQuery<TranscriptResponse, ApiError>({
    queryKey: ['/api/v1/projects/transcript', active.id, projectId],
    queryFn: ({ signal }) =>
      transcriptApiV1ProjectsProjectIdTranscriptGet(projectId, { workspace_id: active.id }, { signal }),
    enabled,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  })
}
```

- [ ] **Step 4: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/transcript.test.ts` → PASS.

---

### Task 2: `MomentCard`, `WhyThisMoment`, and the moments list

**Files:**
- Create: `frontend/features/clips/ScoreBars.tsx`, `frontend/features/clips/WhyThisMoment.tsx`, `frontend/features/clips/MomentCard.tsx`, `frontend/features/clips/use-open-edit.ts`, `frontend/features/clips/use-project-candidates.ts`
- Modify: `frontend/features/clips/ClipList.tsx`
- Delete: `frontend/features/clips/ClipCard.tsx` after moving `formatDuration` callers to `formatClock` (`grep -rn "formatDuration" frontend/app frontend/features frontend/components`)
- Modify: `frontend/tests/clips.test.tsx`, `frontend/tests/editor-basic.test.tsx` ("opening the editor from a clip" renders `MomentCard`)

**Interfaces:**
- Produces:
  - `useOpenEdit(candidate: Pick<CandidateResponse, 'id' | 'projectId'>): { open: (look?: { templateId: string | null; brandKitId: string | null }) => void; isPending: boolean; error: ApiError | null }` — creates or reuses the Edit and routes to `/editor/<id>`.
  - `ScoreBars({ breakdown: CandidateResponse['scoreBreakdown'] })`.
  - `WhyThisMoment({ candidate: CandidateResponse; open: boolean; onOpenChange: (open: boolean) => void })`.
  - `MomentCard({ candidate: CandidateResponse; selected?: boolean; onSelect?: () => void })`.
  - `ClipList` props: `{ projectId: string; selectedId?: string | null; onSelect?: (candidate: CandidateResponse) => void }`.
  - `useProjectCandidates(projectId: string, options?: { enabled?: boolean }): { candidates: CandidateResponse[]; complete: boolean; query: UseInfiniteQueryResult<InfiniteData<CandidatePageResponse>, ApiError> }` — reads every page (Task 20's "whole set before any control") under the key `['/api/v1/projects/candidates', workspaceId, projectId]`, so `ClipList`, the Project page, and review mode share one cache entry.

- [ ] **Step 1: Rewrite the affected list tests**

In `frontend/tests/clips.test.tsx`:

- "shows the score, the hook, the payoff, and why the clip was chosen": the card still shows hook (heading level 3), reason, and `91`; the payoff moves into the sheet. Replace the payoff assertion with:

```tsx
    await userEvent.click(within(clip).getByRole('button', { name: 'Why this moment' }))
    expect(await screen.findByRole('dialog', { name: 'Why this moment' })).toHaveTextContent('The useful resolution')
```

- "explains the score through every dimension the analysis reported": open the sheet the same way, then query dimensions `within(await screen.findByRole('dialog', { name: 'Why this moment' }))`.
- "shows the category, the tags, and the length": the card shows category and `0:30`/`1:30`; tags move into the sheet — open it and assert `creator` inside the dialog.
- Add:

```tsx
  test('draws each moment as a poster and lets the page select it', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    signedInApi([candidate()])
    renderWithApi(
      <WorkspaceProvider>
        <ClipList projectId={PROJECT_ID} onSelect={onSelect} />
      </WorkspaceProvider>,
    )

    const clip = await firstClip()
    expect(within(clip).getByTestId('poster')).toBeInTheDocument()
    await user.click(within(clip).getByRole('button', { name: 'Show in source' }))

    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: candidate().id }))
  })

  test('marks a context warning on the card itself', async () => {
    signedInApi([candidate({ contextWarnings: ['Needs a source overlay.'] })])
    renderClips()

    const clip = await firstClip()
    expect(within(clip).getByRole('group', { name: /context warnings/i })).toHaveTextContent('Needs a source overlay.')
  })
```

`userEvent` is already imported; the clip-preview tests keep their `/preview/` button queries.

In `frontend/tests/editor-basic.test.tsx`, "opening the editor from a clip creates one Edit and goes to it" imports the deleted `ClipCard`. Change the import to `import { MomentCard } from '@/features/clips/MomentCard'` and render:

```tsx
    renderWithApi(
      <WorkspaceProvider>
        <ul>
          <MomentCard candidate={candidate()} />
        </ul>
      </WorkspaceProvider>,
    )
```

The rest of that test is unchanged: `Edit clip` still creates one Edit and routes to `/editor/<id>?workspace_id=<workspace>`.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/clips.test.tsx`
Expected: FAIL — no "Why this moment" button, no poster, no "Show in source".

- [ ] **Step 3: Implement `use-open-edit.ts`**

```ts
'use client'

import { useMutation } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { createApiV1ProjectsProjectIdCandidatesCandidateIdEditsPost } from '@/lib/api/generated/edits/edits'
import type { CandidateResponse, EditResponse } from '@/lib/api/generated/model'

type Look = { templateId: string | null; brandKitId: string | null }

/**
 * Open one candidate in the editor.
 *
 * One clip has one Edit: the backend converges a repeated request on the Edit it already
 * created, so a second press opens the same work rather than a rival copy.
 */
export function useOpenEdit(candidate: Pick<CandidateResponse, 'id' | 'projectId'>) {
  const { active } = useWorkspaceScope()
  const router = useRouter()
  const mutation = useMutation<EditResponse, ApiError, Look>({
    mutationFn: (look) =>
      createApiV1ProjectsProjectIdCandidatesCandidateIdEditsPost(candidate.projectId, candidate.id, look, {
        workspace_id: active.id,
      }),
    onSuccess: (created) => router.push(`/editor/${created.id}?workspace_id=${active.id}`),
  })
  return {
    open: (look: Look = { templateId: null, brandKitId: null }) => mutation.mutate(look),
    isPending: mutation.isPending,
    error: mutation.error,
  }
}
```

- [ ] **Step 4: Implement `use-project-candidates.ts`**

Move the query and the page-following effect out of `ClipList.tsx` unchanged:

```ts
'use client'

import { useInfiniteQuery, type InfiniteData, type UseInfiniteQueryResult } from '@tanstack/react-query'
import { useEffect, useMemo } from 'react'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { listCollectionApiV1ProjectsProjectIdCandidatesGet } from '@/lib/api/generated/candidates/candidates'
import type { CandidatePageResponse, CandidateResponse } from '@/lib/api/generated/model'

const PAGE_SIZE = 100

/**
 * The whole exposed candidate set of one Project.
 *
 * Every page is read before `complete` turns true, because sorting or filtering half a list
 * would be an order the browser invented. The set is bounded by the ranking policy.
 */
export function useProjectCandidates(
  projectId: string,
  { enabled = true }: { enabled?: boolean } = {},
): {
  candidates: CandidateResponse[]
  complete: boolean
  query: UseInfiniteQueryResult<InfiniteData<CandidatePageResponse>, ApiError>
} {
  const { active } = useWorkspaceScope()
  const query = useInfiniteQuery<CandidatePageResponse, ApiError, InfiniteData<CandidatePageResponse>, readonly unknown[], string | null>({
    queryKey: ['/api/v1/projects/candidates', active.id, projectId],
    queryFn: ({ pageParam, signal }) =>
      listCollectionApiV1ProjectsProjectIdCandidatesGet(
        projectId,
        { workspace_id: active.id, limit: PAGE_SIZE, ...(pageParam === null ? {} : { cursor: pageParam }) },
        { signal },
      ),
    initialPageParam: null,
    getNextPageParam: (page) => page.nextCursor,
    enabled,
    retry: false,
  })
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage) void fetchNextPage()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])
  const candidates = useMemo(() => query.data?.pages.flatMap((page) => page.candidates) ?? [], [query.data])
  return { candidates, complete: query.isSuccess && !hasNextPage, query }
}
```

`ClipList` then uses `const { candidates: listed, query: clips } = useProjectCandidates(projectId)` and keeps its loading (`clips.isPending || clips.hasNextPage`), 404, error, and empty branches as they are. If the generic parameters above do not match the installed TanStack Query types, drop the explicit generics and annotate `pageParam` in the `queryFn` instead.

- [ ] **Step 5: Implement `ScoreBars.tsx`**

```tsx
import type { CandidateResponse } from '@/lib/api/generated/model'

const DIMENSIONS = [
  ['Hook', 'hook'],
  ['Payoff', 'payoff'],
  ['Narrative completeness', 'narrativeCompleteness'],
  ['Context safety', 'contextSafety'],
  ['Platform fit', 'platformFit'],
  ['Transcript confidence', 'transcriptConfidence'],
  ['Visual opportunity', 'visualOpportunity'],
] as const

/** The seven dimensions behind a score, as labelled bars with their numbers. */
export function ScoreBars({ breakdown }: { breakdown: CandidateResponse['scoreBreakdown'] }) {
  return (
    <dl aria-label="Score breakdown" className="space-y-2">
      {DIMENSIONS.map(([label, key]) => {
        const value = Math.round(breakdown[key] * 100)
        return (
          <div key={key} className="grid grid-cols-[minmax(0,1fr)_2.5rem] items-center gap-x-3 gap-y-1">
            <dt className="text-caption text-muted-foreground">{label}</dt>
            <dd className="text-right font-mono text-caption tabular">{value}</dd>
            <span aria-hidden="true" className="col-span-2 block h-1 overflow-hidden rounded-full bg-secondary">
              <span className="block h-full bg-foreground" style={{ width: `${value}%` }} />
            </span>
          </div>
        )
      })}
    </dl>
  )
}
```

- [ ] **Step 6: Implement `WhyThisMoment.tsx`**

Move `LookSelection` out of the old `ClipCard.tsx` into this file unchanged in behaviour (its two native selects are already `Select` primitives from Plan 1), then:

```tsx
'use client'

import { useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { CandidateResponse } from '@/lib/api/generated/model'

import { ScoreBars } from './ScoreBars'
import { useOpenEdit } from './use-open-edit'

/**
 * Everything a reviewer needs to disagree with the analysis, one click behind the card.
 *
 * Every field here came from a language model reading someone else's words, so all of it
 * renders as text.
 */
export function WhyThisMoment({
  candidate,
  open,
  onOpenChange,
}: {
  candidate: CandidateResponse
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { active } = useWorkspaceScope()
  const [templateId, setTemplateId] = useState('')
  const [brandKitId, setBrandKitId] = useState('')
  const edit = useOpenEdit(candidate)

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Why this moment</SheetTitle>
          <SheetDescription>{candidate.hook}</SheetDescription>
        </SheetHeader>
        <div className="mt-6 space-y-6">
          <section className="space-y-2">
            <h3 className="text-caption font-medium uppercase tracking-wide text-subtle-foreground">Why it was chosen</h3>
            <p className="text-small">{candidate.reason}</p>
          </section>
          <section className="space-y-2">
            <h3 className="text-caption font-medium uppercase tracking-wide text-subtle-foreground">How it lands</h3>
            <p className="text-small">{candidate.payoff}</p>
          </section>
          <blockquote className="border-l-2 border-line-strong pl-3 text-small text-muted-foreground">
            {candidate.transcriptExcerpt}
          </blockquote>
          <section className="space-y-3">
            <h3 className="text-caption font-medium uppercase tracking-wide text-subtle-foreground">Score</h3>
            <ScoreBars breakdown={candidate.scoreBreakdown} />
          </section>
          {candidate.contextDependencies.length === 0 ? null : (
            <ul aria-label="Context this clip depends on" className="space-y-1 text-small text-muted-foreground">
              {candidate.contextDependencies.map((dependency) => (
                <li key={dependency}>{dependency}</li>
              ))}
            </ul>
          )}
          {candidate.visualOpportunities.length === 0 ? null : (
            <ul aria-label="Visual opportunities" className="space-y-1 text-small text-muted-foreground">
              {candidate.visualOpportunities.map((opportunity) => (
                <li key={opportunity}>{opportunity}</li>
              ))}
            </ul>
          )}
          {candidate.tags.length === 0 ? null : (
            <ul aria-label="Tags" className="flex flex-wrap gap-1.5">
              {candidate.tags.map((tag) => (
                <li key={tag} className="rounded-sm border border-line-strong px-1.5 py-0.5 font-mono text-caption">
                  {tag}
                </li>
              ))}
            </ul>
          )}
          <section className="space-y-3">
            <h3 className="text-caption font-medium uppercase tracking-wide text-subtle-foreground">Open with a look</h3>
            <div className="flex flex-wrap items-center gap-3">
              <LookSelection
                workspaceId={active.id}
                templateId={templateId}
                brandKitId={brandKitId}
                onTemplate={setTemplateId}
                onBrandKit={setBrandKitId}
              />
            </div>
            <Button
              loading={edit.isPending}
              onClick={() =>
                edit.open({ templateId: templateId === '' ? null : templateId, brandKitId: brandKitId === '' ? null : brandKitId })
              }
            >
              Edit clip
            </Button>
            {edit.error === null ? null : <ErrorNotice error={edit.error} />}
          </section>
        </div>
      </SheetContent>
    </Sheet>
  )
}
```

- [ ] **Step 7: Implement `MomentCard.tsx`**

```tsx
'use client'

import { AlertTriangle, Crosshair } from 'lucide-react'
import Link from 'next/link'
import { useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { Poster } from '@/components/media/poster'
import { Button } from '@/components/ui/button'
import { IconButton } from '@/components/ui/icon-button'
import type { CandidateResponse } from '@/lib/api/generated/model'
import { formatClock } from '@/lib/media/time'
import { cn } from '@/lib/utils'

import { ClipPreview } from './ClipPreview'
import { useOpenEdit } from './use-open-edit'
import { WhyThisMoment } from './WhyThisMoment'

const CATEGORY_LABELS: Record<string, string> = {
  story: 'Story',
  insight: 'Insight',
  how_to: 'How-to',
  opinion: 'Opinion',
  question_answer: 'Question and answer',
  humour: 'Humour',
  data: 'Data',
  announcement: 'Announcement',
}

/**
 * One proposed moment: its picture, its rank and score, its hook, and the warnings against it.
 *
 * The reasons and every score dimension sit one click away in "Why this moment"; context
 * warnings never do, because a reviewer must not be able to miss them.
 */
export function MomentCard({
  candidate,
  selected = false,
  onSelect,
}: {
  candidate: CandidateResponse
  selected?: boolean
  onSelect?: () => void
}) {
  const [previewing, setPreviewing] = useState(false)
  const [explaining, setExplaining] = useState(false)
  const edit = useOpenEdit(candidate)

  return (
    <li
      aria-current={selected ? 'true' : undefined}
      className={cn(
        'flex flex-col overflow-hidden rounded-lg border bg-card transition-colors duration-fast ease-signal',
        selected ? 'border-primary' : 'hover:border-line-strong',
      )}
    >
      <div className="relative aspect-[9/16] overflow-hidden bg-stage">
        {previewing ? (
          <ClipPreview projectId={candidate.projectId} startMs={candidate.startMs} endMs={candidate.endMs} />
        ) : (
          <Poster
            projectId={candidate.projectId}
            startMs={candidate.startMs}
            endMs={candidate.endMs}
            aspect="portrait"
            rank={candidate.rank}
            durationMs={candidate.durationMs}
          />
        )}
        {onSelect === undefined ? null : (
          <IconButton
            label="Show in source"
            icon={<Crosshair strokeWidth={1.75} />}
            variant="secondary"
            size="sm"
            onClick={onSelect}
            className="absolute bottom-2 right-2 z-10"
          />
        )}
      </div>
      <div className="flex flex-1 flex-col gap-3 p-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <p className="text-caption text-muted-foreground">
              {CATEGORY_LABELS[candidate.category] ?? candidate.category} ·{' '}
              <span className="font-mono tabular">{formatClock(candidate.durationMs)}</span>
            </p>
            <h3 className="text-small font-semibold leading-snug">{candidate.hook}</h3>
          </div>
          <p aria-label="Score" className="font-display shrink-0 text-h2 tabular">
            {Math.round(candidate.score * 100)}
          </p>
        </div>
        <p className="line-clamp-2 text-caption text-muted-foreground">{candidate.reason}</p>
        {candidate.contextWarnings.length === 0 ? null : (
          <div role="group" aria-label="Context warnings" className="flex gap-2 rounded-md bg-warning-soft p-2 text-caption text-warning">
            <AlertTriangle aria-hidden="true" strokeWidth={1.75} className="mt-0.5 size-3.5 shrink-0" />
            <ul className="space-y-0.5">
              {candidate.contextWarnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-auto flex flex-wrap items-center gap-2">
          <Button size="sm" loading={edit.isPending} onClick={() => edit.open()}>
            Edit clip
          </Button>
          <Button size="sm" variant="secondary" aria-expanded={previewing} onClick={() => setPreviewing((current) => !current)}>
            {previewing ? 'Hide preview' : 'Preview clip'}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setExplaining(true)}>
            Why this moment
          </Button>
        </div>
        {edit.error === null ? null : <ErrorNotice error={edit.error} />}
        <Link href={`/dashboard/clips/${candidate.id}`} className="text-caption font-medium text-muted-foreground hover:text-foreground">
          Open clip page
        </Link>
      </div>
      <WhyThisMoment candidate={candidate} open={explaining} onOpenChange={setExplaining} />
    </li>
  )
}
```

Several cards each have a filled lime "Edit clip" — on the Project page these are the grid's per-item actions, not the view's primary action; the page header's action stays the only large lime button. Keep the card buttons at `size="sm"`.

In `ClipPreview.tsx`, change the video class to `absolute inset-0 size-full bg-stage object-contain` so the preview fills the card's 9:16 box.

- [ ] **Step 8: Move `ClipList` onto `MomentCard`**

In `ClipList.tsx`: accept `selectedId` and `onSelect`; render

```tsx
<ul aria-label="Ranked clips" className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-4">
  {shown.map((clip) => (
    <MomentCard
      key={clip.id}
      candidate={clip}
      selected={clip.id === selectedId}
      onSelect={onSelect === undefined ? undefined : () => onSelect(clip)}
    />
  ))}
</ul>
```

Keep the three `Select`s (`Sort clips`, `Filter by category`, `Maximum length`) at `controlSize="sm"`; change the count sentence to `text-small text-muted-foreground`. Move `formatDuration` imports elsewhere to `formatClock`, then delete `ClipCard.tsx`.

- [ ] **Step 9: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/clips.test.tsx` → PASS.
Run: `pnpm typecheck` → PASS.

---

### Task 3: The Project page

**Files:**
- Create: `frontend/features/projects/source-column.tsx`
- Modify: `frontend/features/projects/project-detail.tsx`, `frontend/features/projects/status-labels.ts` (remove `projectNextStep` if nothing else uses it)
- Modify: `frontend/features/uploads/UploadPanel.tsx` (expose the file input id for the header action)
- Modify: `frontend/tests/creator-studio.test.tsx` ("names the next step…")
- Create: `frontend/tests/project-page.test.tsx`

**Interfaces:**
- Produces:
  - `SourceColumn({ projectId: string; status: string; candidates: CandidateResponse[]; selected: CandidateResponse | null })`.
  - `UploadPanel` gains `fileInputId?: string` (applied to its file input).
  - Project page primary action: `Add media` (created, failed), none while processing, `Review moments` link (ready) with secondary `Continue editing` and `Open exports` when they apply.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/project-page.test.tsx`:

```tsx
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { ProjectDetail } from '@/features/projects/project-detail'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi } from './support/api'
import { FakeEventSource } from './support/events'
import { candidate, currentUser, project, workspace } from './support/fixtures'

const PROJECT_ID = project().id
const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const PROJECT = `GET /api/v1/projects/${PROJECT_ID}`
const CANDIDATES = `GET /api/v1/projects/${PROJECT_ID}/candidates`
const TRANSCRIPT = `GET /api/v1/projects/${PROJECT_ID}/transcript`
const PROXY = `GET /api/v1/projects/${PROJECT_ID}/proxy`
const CLIPS = 'GET /api/v1/clips'

vi.mock('next/navigation', () => ({
  usePathname: () => `/dashboard/projects/${PROJECT_ID}`,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

beforeEach(() => {
  window.sessionStorage.clear()
  window.history.replaceState(null, '', `/dashboard/projects/${PROJECT_ID}`)
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})

function readyApi() {
  return stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [PROJECT]: { body: project({ status: 'ready' }) },
    [CANDIDATES]: {
      body: {
        candidates: [
          candidate({ startMs: 1_000, endMs: 3_000, durationMs: 2_000 }),
          candidate({ id: '55555555-5555-4555-8555-555555555552', rank: 2, hook: 'Second', startMs: 10_000, endMs: 12_000, durationMs: 2_000 }),
        ],
        nextCursor: null,
      },
    },
    [TRANSCRIPT]: {
      body: {
        language: 'en',
        durationMs: 20_000,
        words: [
          { id: 'w1', text: 'Opening', punctuation: '.', startMs: 1_000, endMs: 1_500, speaker: 'SPEAKER_00' },
          { id: 'w2', text: 'Middle', punctuation: '.', startMs: 5_000, endMs: 5_500, speaker: 'SPEAKER_00' },
          { id: 'w3', text: 'Second', punctuation: '.', startMs: 10_500, endMs: 11_000, speaker: 'SPEAKER_01' },
        ],
      },
    },
    [PROXY]: { body: { url: 'https://objects.test/proxy.mp4', expiresAt: '2026-02-01T00:05:00+00:00', contentType: 'video/mp4', durationMs: 20_000, width: 1280, height: 720 } },
    [CLIPS]: { body: { clips: [], nextCursor: null } },
  })
}

function renderProject() {
  renderWithApi(
    <WorkspaceProvider>
      <ProjectDetail projectId={PROJECT_ID} />
    </WorkspaceProvider>,
  )
}

describe('the Project page', () => {
  test('a new project leads with adding media, with no next-step card', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECT]: { body: project({ status: 'created' }) },
    })
    renderProject()

    expect(await screen.findByRole('button', { name: 'Add media' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: /upload a video/i })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: /next step/i })).not.toBeInTheDocument()
  })

  test('a ready project is its moments beside the source and transcript', async () => {
    readyApi()
    renderProject()

    expect(await screen.findByRole('link', { name: 'Review moments' })).toHaveAttribute(
      'href',
      `/dashboard/projects/${PROJECT_ID}/review`,
    )
    expect(await screen.findByRole('list', { name: /ranked clips/i })).toBeInTheDocument()
    const transcript = await screen.findByRole('list', { name: 'Transcript' })
    expect(within(transcript).getAllByRole('listitem').map((line) => line.textContent)).toEqual([
      expect.stringContaining('Opening.'),
      expect.stringContaining('Middle.'),
      expect.stringContaining('Second.'),
    ])
  })

  test('showing a moment in source marks its lines and seeks the player to its start', async () => {
    const user = userEvent.setup()
    readyApi()
    renderProject()

    const list = await screen.findByRole('list', { name: /ranked clips/i })
    const second = within(list).getAllByRole('listitem').find((item) => item.textContent?.includes('Second'))!
    await user.click(within(second).getByRole('button', { name: 'Show in source' }))

    const transcript = screen.getByRole('list', { name: 'Transcript' })
    await waitFor(() =>
      expect(within(transcript).getByText('Second.').closest('li')).toHaveAttribute('aria-current', 'true'),
    )
    const video = screen.getByTestId('transcript-moment-video') as HTMLVideoElement
    expect(video.currentTime).toBe(10)
  })

  test('tabs keep Moments, Edits, Exports, and Activity in the address', async () => {
    const user = userEvent.setup()
    readyApi()
    renderProject()

    await user.click(await screen.findByRole('tab', { name: 'Activity' }))

    expect(window.location.search).toBe('?tab=activity')
  })
})
```

In `frontend/tests/creator-studio.test.tsx`, "names the next step from its durable state and opens its exports on request": replace the `region /next step/` assertion with `expect(await screen.findByRole('button', { name: 'Add media' })).toBeInTheDocument()`.

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/project-page.test.tsx tests/creator-studio.test.tsx`
Expected: FAIL — no `Add media` action, no transcript list, the next-step card still renders.

- [ ] **Step 3: Implement `source-column.tsx`**

```tsx
'use client'

import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useRef } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { DesignedFrame } from '@/components/media/poster'
import { useTranscript } from '@/features/media/use-transcript'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { CandidateResponse, ProxyPlaybackResponse } from '@/lib/api/generated/model'
import { showApiV1ProjectsProjectIdProxyGet } from '@/lib/api/generated/playback/playback'
import { overlaps, transcriptSegments } from '@/lib/media/transcript'
import { formatClock } from '@/lib/media/time'
import { cn } from '@/lib/utils'

/**
 * The source beside its moments: the proxy player and the transcript with every moment's
 * range marked. Choosing a moment highlights its lines and moves the player to its start;
 * choosing a line moves the player to that line.
 */
export function SourceColumn({
  projectId,
  status,
  candidates,
  selected,
}: {
  projectId: string
  status: string
  candidates: CandidateResponse[]
  selected: CandidateResponse | null
}) {
  const { active } = useWorkspaceScope()
  const video = useRef<HTMLVideoElement>(null)
  const ready = status === 'ready'
  const playback = useQuery<ProxyPlaybackResponse, ApiError>({
    queryKey: ['/api/v1/projects/proxy', active.id, projectId],
    queryFn: ({ signal }) => showApiV1ProjectsProjectIdProxyGet(projectId, { workspace_id: active.id }, { signal }),
    retry: false,
    gcTime: 0,
    staleTime: 0,
  })
  const transcript = useTranscript(projectId, { enabled: ready })
  const segments = useMemo(() => transcriptSegments(transcript.data?.words ?? []), [transcript.data])

  useEffect(() => {
    if (selected !== null && video.current !== null) {
      video.current.currentTime = selected.startMs / 1000
    }
  }, [selected])

  return (
    <aside aria-label="Source" className="space-y-4 lg:sticky lg:top-[68px]">
      <div className="relative aspect-video overflow-hidden rounded-lg bg-stage">
        {playback.data === undefined ? (
          <DesignedFrame />
        ) : (
          <video
            ref={video}
            data-testid="transcript-moment-video"
            src={playback.data.url}
            controls
            preload="metadata"
            onError={() => void playback.refetch()}
            className="absolute inset-0 size-full object-contain"
          />
        )}
      </div>
      {playback.isError && playback.error.status !== 404 ? <ErrorNotice error={playback.error} /> : null}
      {!ready ? null : transcript.isError ? (
        transcript.error.status === 404 ? null : <ErrorNotice error={transcript.error} />
      ) : (
        <ol aria-label="Transcript" className="max-h-[50vh] space-y-1 overflow-y-auto rounded-lg border bg-card p-2 lg:max-h-[calc(100vh-24rem)]">
          {segments.map((segment) => {
            const inSelected = selected !== null && overlaps(segment, selected.startMs, selected.endMs)
            const inAny = candidates.some((entry) => overlaps(segment, entry.startMs, entry.endMs))
            return (
              <li key={`${segment.startMs}-${segment.speaker}`} aria-current={inSelected ? 'true' : undefined}>
                <button
                  type="button"
                  onClick={() => {
                    if (video.current !== null) video.current.currentTime = segment.startMs / 1000
                  }}
                  className={cn(
                    'grid w-full grid-cols-[3rem_minmax(0,1fr)] gap-2 rounded-md px-2 py-1.5 text-left text-small transition-colors duration-fast ease-signal',
                    inSelected ? 'bg-primary-soft text-foreground' : inAny ? 'text-foreground hover:bg-secondary' : 'text-muted-foreground hover:bg-secondary',
                  )}
                >
                  <span className="font-mono text-caption text-subtle-foreground tabular">{formatClock(segment.startMs)}</span>
                  <span className={cn(inAny && !inSelected && 'underline decoration-line-strong underline-offset-4')}>{segment.text}</span>
                </button>
              </li>
            )
          })}
        </ol>
      )}
    </aside>
  )
}
```

Scroll the selected line into view when the selection changes: in the `selected` effect, also call `document.querySelector('[aria-label="Transcript"] [aria-current="true"]')?.scrollIntoView({ block: 'nearest' })` guarded by `typeof Element.prototype.scrollIntoView === 'function'` (jsdom lacks it).

- [ ] **Step 4: Restructure `project-detail.tsx`**

Replace `LoadedProject`'s render with:

```tsx
  const [tab, choose] = useUrlTab<TabId>(TABS.map((entry) => entry.id), 'moments')
  const [selected, setSelected] = useState<CandidateResponse | null>(null)
  const { candidates: moments } = useProjectCandidates(project.id, { enabled: project.status === 'ready' })
  const needsMedia = project.status === 'created' || project.status === 'failed'
  const processing = projectIsProcessing(project.status)
  const fileInputId = `project-${project.id}-file`

  return (
    <article className="space-y-8">
      <PageHeader
        title={project.name}
        crumbs={[{ href: '/dashboard/projects', label: 'Projects' }]}
        meta={<StatusBadge tone={projectStatusTone(project.status)}>{projectStatusLabel(project.status)}</StatusBadge>}
        actions={
          needsMedia ? (
            <Button size="lg" onClick={() => document.getElementById(fileInputId)?.click()}>
              Add media
            </Button>
          ) : project.status === 'ready' ? (
            <>
              <Button asChild size="lg">
                <Link href={`/dashboard/projects/${project.id}/review`}>Review moments</Link>
              </Button>
              <Button variant="secondary" size="lg" onClick={() => choose('exports')}>
                Open exports
              </Button>
            </>
          ) : undefined
        }
      />

      {needsMedia || processing ? (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
          <UploadPanel projectId={project.id} addMedia={needsMedia} onJob={onJob} fileInputId={fileInputId} />
          {processing ? <SourceColumn projectId={project.id} status={project.status} candidates={[]} selected={null} /> : null}
        </div>
      ) : (
        <UploadPanel projectId={project.id} addMedia={false} onJob={onJob} />
      )}

      <div>
        <TabList label="Project contents" tabs={TABS} active={tab} onChoose={choose} idPrefix="project" />
        <TabPanel idPrefix="project" id="moments" active={tab}>
          {project.status === 'ready' ? (
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px] xl:grid-cols-[minmax(0,1fr)_420px]">
              <ClipList projectId={project.id} selectedId={selected?.id ?? null} onSelect={setSelected} />
              <SourceColumn projectId={project.id} status={project.status} candidates={moments} selected={selected} />
            </div>
          ) : (
            <EmptyState
              compact
              icon={Clapperboard}
              title="Moments appear when processing finishes"
              description={processing ? 'Clipah is working on this video. Suggested moments will show up here.' : 'Add a video to this project to get suggested moments.'}
            />
          )}
        </TabPanel>
        <TabPanel idPrefix="project" id="edits" active={tab}><ProjectEdits projectId={project.id} /></TabPanel>
        <TabPanel idPrefix="project" id="exports" active={tab}><ExportList projectId={project.id} /></TabPanel>
        <TabPanel idPrefix="project" id="activity" active={tab}><ProjectActivity jobs={jobs} /></TabPanel>
      </div>
    </article>
  )
```

where `moments` comes from the shared hook, which reuses `ClipList`'s cache entry instead of asking again:

```tsx
const { candidates: moments } = useProjectCandidates(project.id, { enabled: project.status === 'ready' })
```

Import `useProjectCandidates`, `SourceColumn`, `Button`, `TabList`, `TabPanel`, and `useUrlTab`. Delete `NextStepCard`, the old `TabPanel`, `isTab`, the `useSearchParams` tab logic, and `SourcePreview` (its `?t=` deep-link behaviour moves into `SourceColumn`: read `?t=` from `window.location.search` once on mount and set `currentTime` on `loadedmetadata`; keep the `data-testid="transcript-moment-video"` the content-search browser test uses). If `projectNextStep` is unused afterwards (`grep -rn projectNextStep frontend/features frontend/app`), delete it from `status-labels.ts`.

In `UploadPanel.tsx`, add the optional `fileInputId` prop to both components and use `id={fileInputId ?? fieldId}` on the file input (the label's `htmlFor` must use the same value).

- [ ] **Step 5: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/project-page.test.tsx tests/creator-studio.test.tsx tests/content-search.test.tsx tests/uploads.test.tsx` → PASS.

---

### Task 4: Review mode

**Files:**
- Create: `frontend/features/review/use-review-keys.ts`, `frontend/features/review/ReviewMode.tsx`
- Create: `frontend/app/dashboard/projects/[projectId]/review/page.tsx`
- Create: `frontend/tests/review-mode.test.tsx`

**Interfaces:**
- Consumes: `useOpenEdit`, `useProjectCandidates`, `useTranscript`, `transcriptWindow`, `ScoreBars`, `Poster`, `formatClock`.
- Produces: `ReviewMode({ projectId: string })`; `useReviewKeys(handlers: { onPlayPause; onNext; onPrevious; onEdit; onExit; onHelp }): void`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/review-mode.test.tsx`:

```tsx
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { ReviewMode } from '@/features/review/ReviewMode'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi } from './support/api'
import { candidate, currentUser, project, workspace } from './support/fixtures'

const PROJECT_ID = project().id
const FIRST = candidate({ startMs: 12_000, endMs: 20_000, durationMs: 8_000, contextWarnings: ['Opens mid-thought.'] })
const SECOND = candidate({ id: '55555555-5555-4555-8555-555555555552', rank: 2, hook: 'Second moment', startMs: 30_000, endMs: 40_000, durationMs: 10_000, contextWarnings: [] })
const push = vi.hoisted(() => vi.fn())

vi.mock('next/navigation', () => ({
  usePathname: () => `/dashboard/projects/${PROJECT_ID}/review`,
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
}))

function reviewApi() {
  return stubApi({
    'GET /api/v1/me': { body: currentUser() },
    'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
    [`GET /api/v1/projects/${PROJECT_ID}/candidates`]: { body: { candidates: [SECOND, FIRST], nextCursor: null } },
    [`GET /api/v1/projects/${PROJECT_ID}/proxy`]: {
      body: { url: 'https://objects.test/proxy.mp4', expiresAt: '2026-02-01T00:05:00+00:00', contentType: 'video/mp4', durationMs: 60_000, width: 1280, height: 720 },
    },
    [`GET /api/v1/projects/${PROJECT_ID}/transcript`]: {
      body: {
        language: 'en',
        durationMs: 60_000,
        words: [
          { id: 'w1', text: 'Before', punctuation: '.', startMs: 8_000, endMs: 8_500, speaker: 'SPEAKER_00' },
          { id: 'w2', text: 'Inside', punctuation: '.', startMs: 13_000, endMs: 13_500, speaker: 'SPEAKER_00' },
          { id: 'w3', text: 'After', punctuation: '.', startMs: 22_000, endMs: 22_500, speaker: 'SPEAKER_00' },
        ],
      },
    },
    [`POST /api/v1/projects/${PROJECT_ID}/candidates/${FIRST.id}/edits`]: { status: 201, body: { id: 'edit-1' } },
  })
}

function renderReview() {
  renderWithApi(
    <WorkspaceProvider>
      <ReviewMode projectId={PROJECT_ID} />
    </WorkspaceProvider>,
  )
}

beforeEach(() => {
  push.mockReset()
  window.sessionStorage.clear()
  window.history.replaceState(null, '', `/dashboard/projects/${PROJECT_ID}/review`)
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
})

describe('review mode', () => {
  test('opens on the best-ranked moment with its hook, warnings, and transcript in context', async () => {
    reviewApi()
    renderReview()

    const detail = await screen.findByRole('region', { name: 'Moment' })
    expect(within(detail).getByRole('heading', { level: 1 })).toHaveTextContent(FIRST.hook)
    expect(within(detail).getByRole('group', { name: /context warnings/i })).toHaveTextContent('Opens mid-thought.')
    const excerpt = await within(detail).findByRole('group', { name: 'Transcript' })
    expect(within(excerpt).getByText('Before.')).toHaveClass('text-subtle-foreground')
    expect(within(excerpt).getByText('Inside.')).toHaveClass('text-foreground')
    expect(within(excerpt).getByText('After.')).toHaveClass('text-subtle-foreground')
  })

  test('opens the moment the address names', async () => {
    window.history.replaceState(null, '', `/dashboard/projects/${PROJECT_ID}/review?moment=${SECOND.id}`)
    reviewApi()
    renderReview()

    expect(await screen.findByRole('heading', { level: 1, name: SECOND.hook })).toBeInTheDocument()
  })

  test('J and K move between moments and keep the address in step', async () => {
    const user = userEvent.setup()
    reviewApi()
    renderReview()
    await screen.findByRole('heading', { level: 1, name: FIRST.hook })

    await user.keyboard('j')
    expect(await screen.findByRole('heading', { level: 1, name: SECOND.hook })).toBeInTheDocument()
    expect(window.location.search).toBe(`?moment=${SECOND.id}`)

    await user.keyboard('k')
    expect(await screen.findByRole('heading', { level: 1, name: FIRST.hook })).toBeInTheDocument()
  })

  test('plays exactly the moment and stops at its end', async () => {
    reviewApi()
    renderReview()

    const video = (await screen.findByTestId('review-video')) as HTMLVideoElement
    fireEvent.loadedMetadata(video)
    expect(video.currentTime).toBe(12)
    video.currentTime = 20
    fireEvent.timeUpdate(video)

    await waitFor(() => expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled())
    expect(video.currentTime).toBe(12)
  })

  test('E opens the moment in the editor', async () => {
    const user = userEvent.setup()
    const api = reviewApi()
    renderReview()
    await screen.findByRole('heading', { level: 1, name: FIRST.hook })

    await user.keyboard('e')

    await waitFor(() => expect(push).toHaveBeenCalledWith(expect.stringMatching(/^\/editor\/edit-1/)))
    expect(api.calls.filter((call) => call.method === 'POST')).toHaveLength(1)
  })

  test('Escape goes back to the project and ? lists every key', async () => {
    const user = userEvent.setup()
    reviewApi()
    renderReview()
    await screen.findByRole('heading', { level: 1, name: FIRST.hook })

    await user.keyboard('?')
    const help = await screen.findByRole('dialog', { name: 'Review shortcuts' })
    for (const key of ['Space', 'J', 'K', 'E', 'Esc']) {
      expect(within(help).getByText(key)).toBeInTheDocument()
    }
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.keyboard('{Escape}')
    expect(push).toHaveBeenCalledWith(`/dashboard/projects/${PROJECT_ID}`)
  })

  test('keys do nothing while a text field has focus', async () => {
    const user = userEvent.setup()
    reviewApi()
    renderReview()
    await screen.findByRole('heading', { level: 1, name: FIRST.hook })
    const field = document.createElement('input')
    document.body.append(field)
    field.focus()

    await user.keyboard('j')

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(FIRST.hook)
    field.remove()
  })

  test('phones get previous and next buttons instead of the list', async () => {
    const user = userEvent.setup()
    reviewApi()
    renderReview()
    await screen.findByRole('heading', { level: 1, name: FIRST.hook })

    await user.click(screen.getByRole('button', { name: 'Next moment' }))

    expect(await screen.findByRole('heading', { level: 1, name: SECOND.hook })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/review-mode.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement `use-review-keys.ts`**

```ts
'use client'

import { useEffect, useRef } from 'react'

export interface ReviewKeyHandlers {
  onPlayPause: () => void
  onNext: () => void
  onPrevious: () => void
  onEdit: () => void
  onExit: () => void
  onHelp: () => void
}

/** Review mode's keyboard map; inert while a member is typing or a dialog is open. */
export function useReviewKeys(handlers: ReviewKeyHandlers): void {
  const latest = useRef(handlers)
  latest.current = handlers

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (event.metaKey || event.ctrlKey || event.altKey || typing(event.target)) return
      if (document.querySelector('[role="dialog"]') !== null) return
      const key = event.key
      if (key === ' ') {
        event.preventDefault()
        latest.current.onPlayPause()
      } else if (key === 'j' || key === 'J') {
        latest.current.onNext()
      } else if (key === 'k' || key === 'K') {
        latest.current.onPrevious()
      } else if (key === 'e' || key === 'E' || key === 'Enter') {
        latest.current.onEdit()
      } else if (key === 'Escape') {
        latest.current.onExit()
      } else if (key === '?') {
        latest.current.onHelp()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])
}

function typing(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement)
  )
}
```

`Enter` on a focused button activates the button, not review's edit: the handler ignores `Enter` when `event.target` is a `button` or `a` — add `|| (key === 'Enter' && event.target instanceof HTMLElement && event.target.closest('button, a') !== null)` to the early return.

- [ ] **Step 4: Implement `ReviewMode.tsx`**

```tsx
'use client'

import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, ChevronLeft, ChevronRight, Keyboard, Repeat } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState, type SyntheticEvent } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { Poster } from '@/components/media/poster'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { IconButton } from '@/components/ui/icon-button'
import { Switch } from '@/components/ui/switch'
import { ScoreBars } from '@/features/clips/ScoreBars'
import { useOpenEdit } from '@/features/clips/use-open-edit'
import { useProjectCandidates } from '@/features/clips/use-project-candidates'
import { useTranscript } from '@/features/media/use-transcript'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { CandidateResponse, ProxyPlaybackResponse } from '@/lib/api/generated/model'
import { showApiV1ProjectsProjectIdProxyGet } from '@/lib/api/generated/playback/playback'
import { transcriptWindow } from '@/lib/media/transcript'
import { formatClock } from '@/lib/media/time'
import { cn } from '@/lib/utils'

import { useReviewKeys } from './use-review-keys'

const SHORTCUTS = [
  ['Space', 'Play or pause'],
  ['J', 'Next moment'],
  ['K', 'Previous moment'],
  ['E', 'Edit this clip'],
  ['Esc', 'Back to the project'],
  ['?', 'Show these shortcuts'],
] as const

/**
 * A focused theater for deciding which moments to edit.
 *
 * It reads the same candidates the Project page reads, plays exactly one moment at a time,
 * shows the words around it, and keeps the chosen moment in `?moment=` so a link or a refresh
 * lands on the same one. Nothing here is saved: moving on is not a decision.
 */
export function ReviewMode({ projectId }: { projectId: string }) {
  const { active } = useWorkspaceScope()
  const router = useRouter()
  const { candidates, query: moments } = useProjectCandidates(projectId)
  const hasNextPage = moments.hasNextPage
  const ordered = useMemo(
    () => [...candidates].sort((left, right) => left.rank - right.rank),
    [candidates],
  )
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [helpOpen, setHelpOpen] = useState(false)

  useEffect(() => {
    if (ordered.length === 0 || currentId !== null) return
    const requested = new URLSearchParams(window.location.search).get('moment')
    setCurrentId(ordered.some((entry) => entry.id === requested) ? requested : ordered[0]!.id)
  }, [ordered, currentId])

  const index = ordered.findIndex((entry) => entry.id === currentId)
  const current = index === -1 ? null : ordered[index]!

  function go(nextIndex: number): void {
    const next = ordered[nextIndex]
    if (next === undefined) return
    setCurrentId(next.id)
    const url = new URL(window.location.href)
    url.searchParams.set('moment', next.id)
    window.history.replaceState(window.history.state, '', url.toString())
  }

  const player = useRef<{ toggle: () => void } | null>(null)
  const edit = useOpenEdit(current ?? { id: '', projectId })

  useReviewKeys({
    onPlayPause: () => player.current?.toggle(),
    onNext: () => go(index + 1),
    onPrevious: () => go(index - 1),
    onEdit: () => (current === null ? undefined : edit.open()),
    onExit: () => router.push(`/dashboard/projects/${projectId}`),
    onHelp: () => setHelpOpen(true),
  })

  if (moments.isPending || hasNextPage) {
    return <LoadingState label="Loading moments…" variant="cards" count={1} />
  }
  if (moments.isError) {
    return <ErrorNotice error={moments.error} onRetry={() => void moments.refetch()} />
  }
  if (current === null) {
    return <p className="text-small text-muted-foreground">There are no moments to review yet.</p>
  }

  return (
    <div className="-mx-4 -my-6 min-h-[calc(100vh-52px)] bg-stage px-4 py-4 sm:-mx-6 sm:px-6 lg:-my-8">
      <div className="mb-4 flex items-center justify-between gap-3">
        <Link href={`/dashboard/projects/${projectId}`} className="inline-flex items-center gap-1.5 text-small font-medium text-muted-foreground hover:text-foreground">
          <ArrowLeft aria-hidden="true" strokeWidth={1.75} className="size-4" />
          Back to project
        </Link>
        <p className="font-mono text-caption text-subtle-foreground tabular">
          {index + 1} / {ordered.length}
        </p>
        <IconButton label="Review shortcuts" shortcut="?" icon={<Keyboard strokeWidth={1.75} />} onClick={() => setHelpOpen(true)} />
      </div>

      <div className="grid gap-6 md:grid-cols-[200px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,420px)_minmax(0,1fr)]">
        <nav aria-label="Moments" className="hidden md:block">
          <ol className="space-y-2">
            {ordered.map((entry, position) => (
              <li key={entry.id}>
                <button
                  type="button"
                  aria-current={entry.id === current.id ? 'true' : undefined}
                  onClick={() => go(position)}
                  className={cn(
                    'grid w-full grid-cols-[56px_minmax(0,1fr)] gap-2 rounded-md border p-1.5 text-left transition-colors duration-fast ease-signal',
                    entry.id === current.id ? 'border-primary bg-card' : 'border-transparent hover:bg-card',
                  )}
                >
                  <span className="relative aspect-[9/16] overflow-hidden rounded-sm">
                    <Poster projectId={projectId} startMs={entry.startMs} endMs={entry.endMs} aspect="portrait" />
                  </span>
                  <span className="min-w-0 space-y-0.5">
                    <span className="block font-mono text-caption text-primary">#{entry.rank}</span>
                    <span className="line-clamp-2 block text-caption">{entry.hook}</span>
                    <span className="block font-mono text-caption text-subtle-foreground">{formatClock(entry.durationMs)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </nav>

        <div className="space-y-3">
          <MomentPlayer ref={player} projectId={projectId} moment={current} />
          <div className="flex items-center justify-between gap-2 md:hidden">
            <Button variant="secondary" onClick={() => go(index - 1)} disabled={index <= 0} aria-label="Previous moment">
              <ChevronLeft aria-hidden="true" strokeWidth={1.75} /> Previous
            </Button>
            <Button variant="secondary" onClick={() => go(index + 1)} disabled={index >= ordered.length - 1} aria-label="Next moment">
              Next <ChevronRight aria-hidden="true" strokeWidth={1.75} />
            </Button>
          </div>
        </div>

        <MomentDetail projectId={projectId} moment={current} onEdit={() => edit.open()} editing={edit.isPending} error={edit.error} />
      </div>

      <Dialog open={helpOpen} onOpenChange={setHelpOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Review shortcuts</DialogTitle>
          </DialogHeader>
          <dl className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-4 gap-y-2">
            {SHORTCUTS.map(([key, action]) => (
              <div key={key} className="contents">
                <dt>
                  <kbd className="rounded-sm border border-line-strong px-1.5 py-0.5 font-mono text-caption">{key}</kbd>
                </dt>
                <dd className="text-small">{action}</dd>
              </div>
            ))}
          </dl>
        </DialogContent>
      </Dialog>
    </div>
  )
}
```

Add, in the same file:

```tsx
/** The moment's range from the proxy, bounded, with an optional loop. */
const MomentPlayer = forwardRef<{ toggle: () => void }, { projectId: string; moment: CandidateResponse }>(
  function MomentPlayer({ projectId, moment }, ref) {
    const { active } = useWorkspaceScope()
    const video = useRef<HTMLVideoElement>(null)
    const [loop, setLoop] = useState(false)
    const playback = useQuery<ProxyPlaybackResponse, ApiError>({
      queryKey: ['/api/v1/projects/proxy', active.id, projectId],
      queryFn: ({ signal }) => showApiV1ProjectsProjectIdProxyGet(projectId, { workspace_id: active.id }, { signal }),
      retry: false,
      gcTime: 0,
      staleTime: 0,
    })

    useImperativeHandle(ref, () => ({
      toggle: () => {
        const element = video.current
        if (element === null) return
        if (element.paused) void element.play()
        else element.pause()
      },
    }))

    useEffect(() => {
      if (video.current !== null && video.current.readyState > 0) {
        video.current.currentTime = moment.startMs / 1000
      }
    }, [moment.id, moment.startMs])

    function start(event: SyntheticEvent<HTMLVideoElement>): void {
      event.currentTarget.currentTime = moment.startMs / 1000
      void event.currentTarget.play()
    }

    function bound(event: SyntheticEvent<HTMLVideoElement>): void {
      const element = event.currentTarget
      if (element.currentTime >= moment.endMs / 1000) {
        element.currentTime = moment.startMs / 1000
        if (!loop) element.pause()
      }
    }

    return (
      <div className="space-y-2">
        <div className="relative mx-auto aspect-[9/16] max-h-[calc(100vh-10rem)] overflow-hidden rounded-lg bg-background">
          {playback.data === undefined ? (
            playback.isError ? <ErrorNotice error={playback.error} /> : null
          ) : (
            <video
              ref={video}
              data-testid="review-video"
              src={playback.data.url}
              playsInline
              preload="metadata"
              onLoadedMetadata={start}
              onTimeUpdate={bound}
              className="absolute inset-0 size-full object-cover"
            />
          )}
        </div>
        <label className="flex items-center justify-center gap-2 text-caption text-muted-foreground">
          <Repeat aria-hidden="true" strokeWidth={1.75} className="size-3.5" />
          Loop
          <Switch checked={loop} onCheckedChange={setLoop} aria-label="Loop this moment" />
        </label>
      </div>
    )
  },
)

/** Hook, reason, warnings, words in context, and the score, for the chosen moment. */
function MomentDetail({
  projectId,
  moment,
  onEdit,
  editing,
  error,
}: {
  projectId: string
  moment: CandidateResponse
  onEdit: () => void
  editing: boolean
  error: ApiError | null
}) {
  const transcript = useTranscript(projectId, { enabled: true })
  const words = transcript.data?.words ?? []
  const context = transcriptWindow(words, moment.startMs, moment.endMs)

  return (
    <section aria-label="Moment" className="space-y-5 md:col-span-2 xl:col-span-1">
      <div className="space-y-2">
        <p className="font-mono text-caption text-primary">
          #{moment.rank} · {formatClock(moment.startMs)}–{formatClock(moment.endMs)} · {Math.round(moment.score * 100)}
        </p>
        <h1 className="font-display text-h1 lg:text-display">{moment.hook}</h1>
        <p className="text-body text-muted-foreground">{moment.reason}</p>
      </div>
      {moment.contextWarnings.length === 0 ? null : (
        <div role="group" aria-label="Context warnings" className="flex gap-2 rounded-md bg-warning-soft p-3 text-small text-warning">
          <AlertTriangle aria-hidden="true" strokeWidth={1.75} className="mt-0.5 size-4 shrink-0" />
          <ul className="space-y-1">
            {moment.contextWarnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
      <div role="group" aria-label="Transcript" className="space-y-1 rounded-lg border bg-card p-4 text-small leading-relaxed">
        {words.length === 0 ? (
          <p className="text-muted-foreground">{moment.transcriptExcerpt}</p>
        ) : (
          <p>
            {context.before === '' ? null : <span className="text-subtle-foreground">{context.before}</span>}{' '}
            <span className="text-foreground">{context.inside}</span>{' '}
            {context.after === '' ? null : <span className="text-subtle-foreground">{context.after}</span>}
          </p>
        )}
      </div>
      <ScoreBars breakdown={moment.scoreBreakdown} />
      <div className="flex flex-wrap items-center gap-3">
        <Button size="lg" loading={editing} onClick={onEdit}>
          Edit clip
        </Button>
        <span className="text-caption text-subtle-foreground">
          or press <kbd className="font-mono">E</kbd>
        </span>
      </div>
      {error === null ? null : <ErrorNotice error={error} />}
    </section>
  )
}
```


The first test queries the transcript spans by text; each span holds exactly one word here (`Before.`, `Inside.`, `After.`), which is what makes `getByText` precise. `useOpenEdit` is called with a placeholder `{ id: '', projectId }` before a moment is chosen; the `onEdit` handler never runs in that state.

- [ ] **Step 5: Add the route**

`frontend/app/dashboard/projects/[projectId]/review/page.tsx`:

```tsx
import { ReviewMode } from '@/features/review/ReviewMode'

/** Review one Project's moments, one at a time, from the keyboard. */
export default async function ReviewPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params
  return <ReviewMode projectId={projectId} />
}
```

- [ ] **Step 6: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/review-mode.test.tsx` → PASS.

---

### Task 5: Point the journey at review mode

**Files:**
- Modify: `frontend/features/workspaces/workspace-overview.tsx` (reel links)
- Modify: `frontend/features/clips/ClipBrowser.tsx` (suggested tiles get a Review action)
- Modify: `frontend/tests/dashboard.test.tsx`, `frontend/tests/creator-studio.test.tsx`

- [ ] **Step 1: Update the tests**

In `frontend/tests/dashboard.test.tsx` "offers the strongest moments as a reel of posters", expect the link `href` to be `/dashboard/projects/44444444-4444-4444-8444-444444444444/review?moment=99999999-9999-4999-8999-999999999999`.

In `frontend/tests/creator-studio.test.tsx` "shows every clip before anything is searched", add:

```tsx
    const suggested = within(list).getByRole('link', { name: 'The surprising opening' }).closest('article')!
    expect(within(suggested).getByRole('link', { name: 'Review' })).toHaveAttribute(
      'href',
      `/dashboard/projects/${PROJECT_ID}/review?moment=55555555-5555-4555-8555-555555555551`,
    )
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm --dir frontend exec vitest run tests/dashboard.test.tsx tests/creator-studio.test.tsx`
Expected: FAIL on both new expectations.

- [ ] **Step 3: Implement**

In `workspace-overview.tsx` `ReadyToReview`, set `href={`/dashboard/projects/${candidate.projectId}/review?moment=${candidate.id}`}`.

In `ClipBrowser.tsx` `ClipTile`'s `footer`, when `clip.stage === 'suggested'` render:

```tsx
<Link href={`/dashboard/projects/${clip.projectId}/review?moment=${clip.id}`} className="text-caption font-semibold text-primary hover:underline">
  Review
</Link>
```

and keep "Continue editing" for clips with an Edit.

- [ ] **Step 4: Run the tests**

Run: `pnpm --dir frontend exec vitest run tests/dashboard.test.tsx tests/creator-studio.test.tsx` → PASS.

---

### Task 6: Browser scenario, gates, and handover

**Files:**
- Create: `frontend/e2e/review-mode.spec.ts`
- Modify: `frontend/e2e/design-screens.spec.ts` (add the review route)
- Modify: `PROGRESS.md`

- [ ] **Step 1: Write the browser scenario**

`frontend/e2e/review-mode.spec.ts`:

```ts
import { expect, test } from '@playwright/test'

import { seedMemberWithClip, signIn, uniqueEmail } from './support/seed'

const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'

test('a member reviews a moment from the keyboard and opens it in the editor', async ({ page }) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('review-mode'),
    displayName: 'Reviewing Member',
    workspaceName: 'Reviewing Workspace',
    projectName: 'Reviewed Episode',
  })
  await signIn(page.context(), member, SITE)

  await page.goto(`/dashboard/projects/${member.project.projectId}/review`)
  await expect(page.getByRole('region', { name: 'Moment' }).getByRole('heading', { level: 1 })).toBeVisible()

  await page.keyboard.press('?')
  await expect(page.getByRole('dialog', { name: 'Review shortcuts' })).toBeVisible()
  await page.keyboard.press('Escape')

  await page.keyboard.press('e')
  await expect(page).toHaveURL(/\/editor\//)
})
```

- [ ] **Step 2: Add the route to the screenshot spec**

In `frontend/e2e/design-screens.spec.ts`, add `['review', `/dashboard/projects/${projectId}/review`]` after the `project` route.

- [ ] **Step 3: Run the gates**

From the repository root: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build` → all PASS.

- [ ] **Step 4: Run the browser suite and capture**

Rebuild the frontend service, then from `frontend/`:

```bash
CLIPAH_E2E_BASE_URL=http://localhost:3000 pnpm exec playwright test --project=chromium --project=webkit
CLIPAH_CAPTURE_SCREENS=review CLIPAH_E2E_BASE_URL=http://localhost:3000 pnpm exec playwright test e2e/design-screens.spec.ts --project=chromium
```

Expected: the new scenario passes in both engines; earlier scenarios keep their counts after selector fixes (`clips-review.spec.ts` looks for moments through the `Ranked clips` list and the "Why this moment" sheet); no route overflows sideways.

- [ ] **Step 5: Record progress**

Append "Signal Studio redesign — Plan 4, project page and review mode" to `PROGRESS.md` with what changed, which Task 20 assertions moved behind "Why this moment" and why that still satisfies them, gate output, screenshot folder, and the owner commit message `feat: add moments-first project page and review mode`. Do not run `git commit`.
