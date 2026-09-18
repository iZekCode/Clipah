# Clipah: Signal Studio Redesign

## Summary

The Guided Creator Studio redesign (`redesign-plan.md`, `plan.md` Section 13) fixed the
journey but not the product's character. The result reads as a stock template: a violet
primary on warm off-white, white cards in empty space, a "C" in a rounded square, Inter named
but never loaded, and gradient rectangles standing in for product imagery. The media a creator
is working on is almost invisible, and the editor presents millisecond fields, native selects,
and a row of text buttons where a creator expects a stage, a transport, and a timeline.

This plan replaces the visual system and reworks every screen around the media:

- **Identity:** "Signal" — dark only, graphite surfaces, one acid-lime accent, condensed
  display type, broadcast-style timecodes, sharp corners.
- **Media first:** every clip, Project, and export is shown as a picture that scrubs on hover,
  backed by a storyboard and a waveform the backend now derives once per source.
- **A real editor:** transport, draggable playhead, filmstrip and waveform tracks, a
  transcript-style caption editor, visual style controls, and timecodes instead of
  milliseconds.
- **Flow and layout:** a dense home, a moments-first Project page, and a new keyboard-driven
  review mode, all on the existing routes.

The journey, routes, backend contracts, editor engine, and safety rules from the rebuild stay
as they are. Implement in the `feat/rebuild-foundation` worktree. The agent never commits.

### Evidence from the current build

Observed on the running Compose stack at 1440 px with a seeded member:

| Area | Finding |
| --- | --- |
| Identity | Default shadcn tokens with a violet primary; `Inter` is declared in `globals.css` but no font is loaded; logo is a letter in a rounded square |
| Media | Project and clip cards show a grey "No preview yet" block; moment cards are text with a large "90 SCORE"; landing and demo use gradient rectangles |
| Editor | `Clip starts at (ms)` number fields, a hex text field for colour, native selects and checkboxes, thirteen text buttons above a thin timeline labelled `scene-1`, and a playhead slider detached from the timeline |
| Consistency | Radix/shadcn primitives are installed but imported by 15 files; features render 28 native `<select>`, 13 native checkboxes, and 120 raw `<button>` elements; no toast is ever shown |
| Layout | At 1440 px Home is a title, one card, and one row; most text is 12–14 px |
| Copy | Internal language reaches members, for example "Every destination keeps its own state, so a batch never hides a failure behind a success" |
| Limits | Ordinary navigation exhausted the 60-per-minute read limit and Settings rendered "Too many requests" |

## Design Principles

1. **The media is the loudest thing on screen.** Chrome is graphite and quiet; pictures,
   captions, and the playhead carry colour.
2. **One accent, one meaning.** Lime marks the single primary action in a view, the playhead,
   the current selection, the caption highlight, and the ready state. Nothing else is lime.
3. **Pro density, creator energy.** Tight, information-rich layouts like an editing suite;
   energy comes from condensed display type, caption-style overlays, and motion on media —
   not from decoration.
4. **Say what it does.** Plain creator language; no domain jargon, no reassurance filler.
5. **Honest state.** Real stages, real progress, real timecodes. No invented percentages,
   estimates, or placeholder imagery pretending to be product.

### Banned patterns

These are the tells that make the current UI read as generated. A pull request that
reintroduces one is not finished.

- Violet, indigo, or purple anywhere in the palette.
- Gradients used as imagery, backgrounds, or buttons.
- An icon inside a tinted rounded square as decoration.
- Eyebrow pills such as "For solo creators".
- A small centred card floating in an empty page (sign-in, empty states, errors).
- Numbered "How it works" rows of four icon cards.
- Emoji in product UI.
- Drop shadows on in-flow cards.
- Copy that explains the system's architecture instead of the member's outcome.

## Visual System

### Colour tokens

Dark only. `next-themes`, the `dark:` variants, and light-mode token blocks are removed. The hex
values below are the source of truth. The implementation stores each token as an RGB triplet
(`--background: 14 14 16`) wired through `rgb(var(--token) / <alpha-value>)`, so the stored
value is exact and Tailwind opacity modifiers keep working. The existing shadcn variable names
are kept and pointed at these values (the mapping is recorded in the foundation plan), so every
existing component adopts the new palette at once.

| Token | Hex | Use |
| --- | --- | --- |
| `--background` | `#0E0E10` | Page canvas |
| `--panel` | `#16161A` | Rail, panels, cards |
| `--raised` | `#1E1E23` | Inputs, hovered rows, selected tiles |
| `--overlay` | `#26262C` | Menus, popovers, dialogs, toasts |
| `--stage` | `#0A0A0B` | Behind video in the editor, review mode, and players |
| `--line` | `#2A2A30` | Default 1 px hairline between regions |
| `--line-strong` | `#3A3A42` | Hovered borders, dividers between major regions |
| `--control-border` | `#72727C` | Borders that identify a control (inputs, selects, checkboxes); 3:1 on every surface |
| `--text` | `#F2F2EE` | Primary text |
| `--text-secondary` | `#A1A1A8` | Supporting text |
| `--text-muted` | `#8F8F98` | Metadata and placeholders (4.5:1 on every surface, including `--overlay`) |
| `--accent` | `#C6FF3D` | Primary action, playhead, selection, caption highlight, ready state |
| `--accent-hover` | `#D4FF66` | Primary hover |
| `--accent-soft` | `#2B321E` | Selected ranges and rows (lime at 12 % over `--panel`, stored solid) |
| `--on-accent` | `#0E0E10` | Text and icons on lime |
| `--signal` | `#FF5A50` | Recording and live, destructive actions, failures only; also used as text |
| `--signal-soft` | `#372022` | Failure banners (signal at 14 % over `--panel`, stored solid) |
| `--on-signal` | `#0E0E10` | Text on red (white fails contrast) |
| `--warning` | `#FFB020` | Context warnings, attention needed |
| `--warning-soft` | `#372C1B` | Warning banners (amber at 14 % over `--panel`, stored solid) |

Status mapping: ready uses a lime dot; processing uses a `--text-secondary` dot that pulses
(static under reduced motion); needs attention uses amber; failed uses signal red. Every pairing
used in the product must meet WCAG 2.2 AA (4.5:1 text, 3:1 large text and UI boundaries); the
foundation phase adds a unit test that checks each declared text/surface pair.

Focus: a 2 px `--accent` outline with a 2 px offset on every interactive element.

### Typography

Vendored in the repository and loaded with `next/font/local`, `display: swap`, so builds are
reproducible offline:

- **Archivo** (variable, `wght` and `wdth` axes) for all UI text. Normal width for body and
  headings; condensed (`wdth` 75, weight 800, uppercase via CSS) for display: page titles,
  scores, large counts, hero copy, and caption-style overlays on posters.
- **JetBrains Mono** for timecodes, durations, scores in badges, counts, and request
  identifiers, always with tabular figures.

Source strings stay in sentence case; uppercase is applied with `text-transform` so tests and
assistive technology read normal text.

| Step | Size / line height | Use |
| --- | --- | --- |
| `caption` | 12 / 16 | Mono metadata, badges |
| `small` | 13 / 20 | Secondary text, table cells |
| `body` | 15 / 22 | Default |
| `title` | 18 / 24 | Section headings (weight 650) |
| `h2` | 24 / 28 | Dialog and panel titles |
| `h1` | 36 / 36 | Page titles (condensed display) |
| `display` | 56 / 52 | Home hero, review mode hook |
| `hero` | 88 / 80 | Landing only |

### Shape, spacing, and motion

- Radius 4 px for controls, 6 px for cards and tiles, 0 for full-bleed media and the stage.
- 1 px hairlines; depth comes from the surface steps. Shadows only on `--overlay` elements.
- 4 px spacing base; 24 px page gutters on desktop, 16 px on phones. Content widens to
  1600 px instead of stopping at 1280 px.
- Motion: 120 ms for hover, 180 ms for panels, 240 ms for dialogs, easing
  `cubic-bezier(0.2, 0, 0, 1)`. No bounce, parallax, or looping decoration. The existing
  reduced-motion rule stays, and hover-scrub falls back to a static frame under it.
- Icons: `lucide-react` at 16 and 20 px, stroke 1.75, drawn bare. Every icon-only button has
  an accessible name and a tooltip that includes its keyboard shortcut when one exists.

### Copy guide

- Sentence case, verb first, no terminal punctuation on labels and buttons.
- Speak to outcomes: "Finding the best moments" instead of "Analysis job running".
- Errors say what happened and what to do next; the request identifier sits behind a "Copy
  details" control, not in the sentence.
- Empty states are invitations with one action ("Drop a long video to start").
- Replace current strings that describe internals. Examples:

| Current | Replacement |
| --- | --- |
| Every destination keeps its own state, so a batch never hides a failure behind a success. | Schedule clips to your connected accounts. |
| This area belongs to a Workspace, so it opens only for a signed-in member. | Sign in to open your studio. |
| Reusable looks for captions and drawn text. Applying one writes its type into a clip and records the version it came from. | Caption looks you can reuse on any clip. |
| Clip starts at (ms) | Start |

`CONTEXT.md` vocabulary still governs code, tests, and docstrings; UI copy may use plain
lowercase words ("workspace", "clip") where the canonical term would read as jargon.

## Components

`frontend/components/ui/` is restyled to Signal and becomes the only place native form
controls are rendered. Feature code composes these components.

**Enforcement.** ESLint `no-restricted-syntax` fails on JSX `<select>` and on `<input>` with
`type` of `checkbox`, `radio`, `range`, or `color` anywhere outside `components/ui/`.
`react/no-danger` stays an error.

| Component | Behaviour |
| --- | --- |
| `Button` | Variants `primary` (lime, at most one per view), `secondary` (raised), `ghost`, `destructive` (signal red). Sizes 32 / 36 / 44 px. Loading state keeps width |
| `IconButton` | Square, requires `label`; shows a tooltip with the label and optional shortcut |
| `Select`, `Checkbox`, `Slider` | Styled native `<select>`, `<input type="checkbox">`, and `<input type="range">` wrappers. Native controls keep phone pickers, form semantics, and the existing tests that drive them; the styling is Signal's |
| `Switch`, `Tabs`, `Tooltip`, `Dialog`, `Sheet` | Radix-backed, Signal styling |
| `ItemMenu` | The existing accessible disclosure menu, moved to `components/ui/` and restyled |
| `SegmentedControl` | A labelled group of buttons with `aria-pressed`, single selection, used for aspect, alignment, weight, and stage filters |
| `TimecodeInput` | Displays and accepts `m:ss.cc` (hundredths); stores milliseconds; arrow keys nudge by 1/100 s, Shift by 1 s; rejects values outside the given bounds with an inline message |
| `NumberScrub` | Numeric field whose label can be dragged horizontally to change the value; keyboard arrows step; shows unit |
| `SwatchPicker` | Brand-kit colours first, recent colours, then a custom colour popover with hex entry; always shows the current value as text |
| `FontPicker` | Lists only fonts the renderer can draw, each name rendered in its own face |
| `Toast` | `sonner`, bottom centre, `--overlay` surface; used for saved, export queued, export ready (with Download and Publish), copied, and recoverable errors |
| `Poster` | Media tile (9:16 or 16:9). Draws a storyboard tile by CSS sprite offset; on pointer hover scrubs across the clip range with no network request per frame; keyboard focus shows the middle frame. Overlays: rank badge, duration, status dot, and the hook as a caption-style line on a solid graphite band at 60 % opacity. Fallbacks, in order: Project thumbnail crop, then a designed graphite frame showing the timecode range. Never the text "No preview yet" |
| `Filmstrip` | Row of storyboard tiles for a time range at a given pixel-per-second scale |
| `Waveform` | Canvas drawing of waveform peaks for a time range; selected range tinted `--accent-soft` |
| `StageBar` | The real pipeline stages (Uploading, Importing, Transcribing, Finding moments) with the current one active, upload percentage only where the upload reports it, a reconnecting state, and Cancel or Retry where the backend allows |
| `StatusDot` | Ready, processing, attention, failed, per the status mapping |
| `EmptyState` | Left-aligned, full width of its region, one action; may contain a drop zone. Replaces the centred icon-card pattern |
| `ErrorNotice` | Plain sentence, Retry when retryable, "Copy details" with the request identifier |
| `Skeleton` | Shapes match the final layout; shimmer disabled under reduced motion |

`page-header.tsx`, `media-card.tsx`, `status-badge.tsx`, `empty-state.tsx`, `error-notice.tsx`,
and `loading-state.tsx` are rebuilt on these components rather than kept beside them.

## Shell

- **Rail.** A 64 px icon rail replaces the 256 px sidebar: New project (top; a lime icon on a
  `--raised` button, so the page's own primary action stays the only filled lime), Home,
  Projects, Clips, Publishing, Library (a flyout for Assets, Templates, Brand kits), and
  Settings at the bottom. Labels appear in tooltips; a pin control expands the rail to 220 px
  with labels, remembered per browser in `localStorage` (wrapped in `try`/`catch`).
- **Top bar (52 px).** Page context or breadcrumb on the left; a command trigger ("Search or
  jump to…", `⌘K` / `Ctrl K`); Workspace switcher; the render queue toggle; the account menu.
- **Command palette.** `cmdk` in a `Dialog`. Groups: Go to (every route), Actions (New
  project, Open last edit, Review moments of the current Project), Projects and Clips (results
  from the existing search endpoint, debounced). `/dashboard/search` and its `?q=` and `?t=`
  deep links keep working.
- **Render queue.** A dock anchored bottom right. Collapsed, it shows a chip such as
  "2 running" with a mini `StageBar`; expanded, it lists running and recent jobs with a
  poster, stage, and Cancel or Retry. It stays mounted when collapsed so the Workspace event
  stream keeps its single connection and history, and it is emptied on Workspace switch —
  the job-center guarantees from Task 18 are preserved.
- **Global drop.** Dragging a video file anywhere in the dashboard shows a full-window drop
  target, "Drop to start a project", which opens New project with the file attached.
- **Phones (< 768 px).** A bottom tab bar (Home, Projects, Clips, Publishing, More) replaces
  the rail; the render queue becomes a sheet.

Breakpoints: phone < 768 px, tablet 768–1279 px, desktop ≥ 1280 px.

## Screens

Every existing route stays. One route is added: `/dashboard/projects/[projectId]/review`.

### Home (`/dashboard`)

- **First run** (no Projects): the page is the importer — a full-width drop zone with the
  YouTube link field beside it, not a card with a button.
- **Continue editing:** a wide hero for the most recently revised Edit — a large `Poster`,
  hook in display type, Project name, last saved time, and Continue editing (primary). Up to
  four more recent Edits follow as a row. Requires the `order=recent` parameter below.
- **Processing now:** one row per Project still in the pipeline, each with its `StageBar`.
- **Ready to review:** a horizontal reel of 9:16 posters for exposed candidates from the
  dashboard summary, each opening review mode at that moment.
- **Recent projects:** a 16:9 poster grid with hover-scrub.

### New project

- The dialog keeps its behaviour (Project created on submit, resumable upload, idempotent
  submission, failed uploads keep the Project) and changes shape: one large drop area, and a
  single "Or paste a YouTube link" field that is detected by content instead of a second tab.
  The name field is prefilled from the file name or the imported title.
- Metered-work limits appear here, at the moment they matter.

### Projects (`/dashboard/projects`)

16:9 poster grid with hover-scrub; each tile shows `StatusDot` with its stage label, the source
duration in mono (read from the storyboard manifest once it exists — the project list read
carries no duration or clip count, and this redesign does not add one), and the item menu for
rename, delete, and restore. Cursor pagination and the recovery window are unchanged.

### Project (`/dashboard/projects/[projectId]`)

- **Header:** name in display type, source duration, status, and one primary action chosen
  from durable state (Add media, Review moments, Continue editing, Open exports).
- **Processing:** a full-width `StageBar`, the source preview once a proxy exists, and the
  supported recovery actions. The "Next step" card is removed.
- **Ready:** a two-column layout.
  - Main column: Moments as a 9:16 poster grid — rank, condensed score, hook, duration, and
    context warnings as an amber marker. Sort and category filters use `SegmentedControl` and
    `Select` over the complete exposed set, as Task 20 requires. "Why this moment" opens a
    `Sheet` with the reason, all seven score dimensions as bars, dependencies, and visual
    opportunities, replacing the `<details>` disclosure.
  - Side column (360 px, sticky): the source player and the transcript, with every moment's
    range marked. Selecting a moment highlights its range and seeks the player.
- Tabs remain Moments, Edits, Exports, Activity, restyled, with the tab in the URL.

### Review mode (`/dashboard/projects/[projectId]/review?moment=<candidateId>`)

A focused, full-height theater on `--stage`. It needs no new backend state.

- Left: a compact list of moments (small posters, rank, duration).
- Centre: a 9:16 player that plays exactly the candidate range from the proxy, using the
  existing preview rule of a fresh signed URL per open and stopping at the candidate end;
  centre-cropped; loop toggle.
- Right: hook in display type, the reason, context warnings, the transcript of the range with
  the surrounding sentences dimmed, and compact score bars.
- Keys: `Space` play or pause, `J` next moment, `K` previous moment, `E` or `Enter` edit this
  clip (the same action as Edit clip today), `Esc` back to the Project. Keys are listed in a
  `?` help sheet.
- Phones: the list collapses into Previous and Next buttons under the player.

### Clips (`/dashboard/clips`) and clip page (`/dashboard/clips/[clipId]`)

- Clips: stage `SegmentedControl` (All, Suggested, In editing, Exported), a Project `Select`,
  a search field, and a poster grid whose primary action follows the stage (Review, Continue
  editing, Download).
- Clip page: a player on the left; on the right the stage, Continue editing (primary; Edit
  clip when no Edit exists yet), Export, and Publish; below, `Tabs` for Exports, Revisions, B-roll, Variants, Evidence, and Campaign
  copy, replacing the stacked disclosures. Revisions render as a vertical timeline.

### Editor (`/editor/[editId]`)

The composition reducer, preview engine, autosave, revision-conflict handling, provenance,
review rules, and panels that stay mounted across tool switches are preserved. The layout and
controls change.

**Desktop layout (≥ 1280 px)**

- **Top bar (48 px):** back to Project, clip hook, save state ("Saved 12 s ago", "Saving…",
  "Unsaved changes", or the conflict banner), Undo, Redo, and Export (primary).
- **Tool rail (56 px, icon with label):** Captions, Style, Layout, Media, Audio, Text, Review.
- **Tool panel (320 px).**
- **Stage:** the preview on `--stage`, an aspect `SegmentedControl` (9:16, 4:5, 1:1, 16:9)
  above it, and an optional safe-area overlay.
- **Transport bar under the stage:** play or pause, step back and forward one frame, jump to
  start and end, current and total time in mono as `m:ss.cc`, and loop the clip range.
- **Inspector (280 px), contextual:** a selected timeline item shows its Start, End, and
  Duration as `TimecodeInput` with Split and Delete; a selected caption word shows its text and
  timing; a selected text overlay shows position and style; nothing selected shows canvas
  settings.
- **Timeline (resizable, default 240 px):**
  - A ruler in `m:ss`; clicking the ruler seeks; a lime playhead with a handle spans every
    track and can be dragged. The separate playhead slider is removed.
  - Tracks, each with a label column: Video (filmstrip tiles inside item bounds, with the
    existing trim handles and drag), Captions (phrase blocks), Text, B-roll, and audio lanes
    (waveform inside each item).
  - Markers as flags on the ruler.
  - Toolbar of `IconButton`s with tooltips: Split, Delete, Duplicate, Snap, Ripple, Add
    marker, Previous and Next marker, Add music lane, Add audio lane; zoom slider and Fit.

**Keyboard.** The existing map stays: `⌘Z` / `⇧⌘Z` undo and redo, `⌘S` save, `Space` play,
`S` split, `Delete` / `Backspace` delete, `+` / `-` zoom. Added: `←` / `→` one frame (the
proxy's nominal rate, or 1/30 s when unknown), `⇧←` / `⇧→` one second, `M` add marker, `?`
shortcut sheet. Shortcuts never fire while focus is in a text field.

**Tool panels**

- **Captions:** a transcript-style editor. Words flow as inline tokens grouped into caption
  lines; the word under the playhead is highlighted lime during playback; clicking a word
  seeks; double-clicking edits it in place without moving its timing; line breaks can be
  inserted. Karaoke timing is a "Timing" mode inside this panel.
- **Style:** templates as visual cards — small 9:16 frames rendering each look's real font,
  colour, and placement — followed by `FontPicker`, size `NumberScrub`, weight
  `SegmentedControl`, colour `SwatchPicker`, background `Switch`, alignment and vertical
  position `SegmentedControl`s, and letter-spacing and line-height `Slider`s. Keyframes and
  motion sit in a "Motion" section of this panel.
- **Layout:** aspect, crop rectangle adjusted directly on the stage with handles, fit or
  fill.
- **Media:** B-roll suggestions as posters with Accept and Replace, the Project's assets grid,
  and the source monitor.
- **Audio:** lanes with volume `Slider`s and music.
- **Text:** overlays list and Add text.
- **Review:** the accessibility and review-rule checks, each linking to the item it concerns.

**Preview fidelity.** The preview remains the HTML video engine with drawn overlays; the plan
does not claim pixel parity with export. Caption and text previews use the same font files the
renderer uses, so the face a member picks is the face they export. Today the render image
installs only `fonts-noto-core`, so none of the eight composition fonts (Inter, Montserrat,
Poppins, Roboto, Open Sans, Bebas Neue, Anton, Nunito) is actually drawn by the renderer. The
editor phase vendors those eight OFL/Apache font files once in the repository, loads them in
the editor with `next/font/local`, copies the same files into the render image, and adds a
readiness check that `fc-list` reports every family.

**Tablet (768–1279 px):** the inspector becomes a `Sheet` opened from the top bar and the tool
panel overlays the stage. **Phone:** preview, caption text edits, Export, and a clear route to
continue on a larger screen, as today.

### Export

- The Export dialog shows each format (9:16, 4:5, 1:1, 16:9) as the clip's poster cropped to
  that shape, with the platforms it suits.
- Saving first and binding the render to the saved Revision (`expectedRevision`) are
  unchanged.
- Progress appears in the render queue; completion raises a toast with Download and Publish.
  Export lists use posters.

### Publishing

- `/dashboard/publishing` opens with Needs attention pinned at the top, then a scheduled
  timeline grouped Today, Tomorrow, and Later, each entry showing its destination account and
  time, then History. A publication read carries no link back to its clip, so queue entries
  show no poster; adding that link is a later backend change, not part of this redesign.
- The composer is three columns: the export preview in a phone frame for the destination being
  edited, destination selection with account names and avatars, and the destination fields
  plus timing; a final summary step confirms. Explicit destination selection, consent
  requirements, review eligibility, capability gates, and independent retry and cancel are
  preserved. The legacy publishing query form keeps working.

### Library

- **Assets:** poster grid with type and Project `Select`s; opening an asset shows a `Sheet`
  with the preview, metadata, and provenance.
- **Templates:** visual look cards as in the editor; "Publish a look" moves into a `Dialog`;
  archived looks behind a `Switch`.
- **Brand kits:** large swatches, logo, font specimens, and the version list.

### Settings

A left sub-navigation (General, Members, Connections, Sessions, Usage). General, Members, and
Connections are the existing routes; Sessions and Usage are sections of the General page reached
by in-page anchors (`#sessions`, `#usage`), so no route is added. Usage renders as horizontal
meters with mono counts. Team (`/dashboard/team`) and Connections
(`/dashboard/settings/connections`) keep their URLs.

### Public pages

- **Landing (`/`):** graphite page; a hero headline in condensed display type ("Long video in.
  Clips worth posting out."); a looping, muted screen recording of review mode and the editor
  captured from the real product with demo content (WebM and MP4, under 4 MB total, with a
  poster image; the poster alone under reduced motion). Below it, three full-width product
  frames — Review, Edit, Publish — each a real screenshot with one sentence. One primary
  action, Get started, and a secondary link to the demo.
- **Sign-in (`/signin`) and invitation (`/invite/[token]`):** split layout — product imagery
  on the left 60 %, the sign-in panel on the right. The Google button follows Google's
  branding guidelines.
- **Demo (`/demo`):** the review-mode layout with bundled, clearly labelled example content
  and no API calls. Imagery must be footage the owner has rights to; without it, use designed
  graphite frames with caption typography — never gradients.

## Backend Additions

All additions are derived from existing media, scoped to one Workspace, and invisible to the
Project workflow. The four backend gates apply, and every new route returns the same 404 for a
foreign identifier as for a missing one.

**Before starting:** the owner's direct-upload and Gemini changes landed as `5f61b7b`. Backend
work starts from that commit and re-reads every file it touches.

**Test isolation:** backend integration tests truncate the database they point at. They run
only against a disposable PostgreSQL cluster and a separate Redis database (see "Candidate
contract experiment — 2026-09-12" in `PROGRESS.md`), never against the application's local
database.

### 1. Migration `0023_preview_media`

- `ALTER TYPE asset_kind ADD VALUE IF NOT EXISTS 'storyboard'`.
- `ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'preview_media'`.
- The downgrade documents that PostgreSQL cannot remove enumeration values, following
  `0013_asset_provenance.py`.
- Confirm the worker role's existing grants cover inserting these Assets and Jobs; add grants
  only if a test proves they are missing.

### 2. `PREVIEW_MEDIA` job

- `JobKind.PREVIEW_MEDIA`, routed to the ingest queue in `celery_app.py` (the image already
  has FFmpeg), runner in `jobs/preview_media_task.py`, registered in `jobs/tasks.py`.
- Admitted when an `INGEST` Job succeeds, after its `TRANSCRIBE` successor has been admitted,
  under the key `pipeline:{ingest_job_id}:preview_media`.
- It is **not** added to `NEXT_STAGE` or `STAGE_STATUS` in `jobs/pipeline.py`: it never
  changes Project status, and its failure never fails the Project.
- If admission refuses it (for example `CONCURRENCY_LIMIT`), nothing is recorded and the
  backfill command picks the Project up later. It never blocks or displaces transcription.
- It charges no metered quota. It is idempotent: a redelivery that finds both derivatives
  reuses them without repeating media work.
- Cancellation checks run between the storyboard and waveform steps.

### 3. Storyboard, version 1

Constants live in `assets/preview_media.py` as `STORYBOARD_V1`.

- Source: the Project's newest proxy.
- One frame every 2,000 ms, taken at `i × 2000 ms`.
- Tiles scaled so the longer side is 160 px, preserving aspect.
- 10 columns × 10 rows per sheet (200 seconds per sheet); the last sheet may be partial.
- JPEG via one FFmpeg invocation using `fps`, `scale`, and `tile` filters, with the argument
  array, isolated process group, timeout, and bounded diagnostics used by the other media
  commands. Target: each sheet under 300 KB.
- Each sheet is an Asset of kind `storyboard`, source type `derived`, content type
  `image/jpeg`, with `width` and `height` of the sheet, `duration_ms` of the span it covers,
  a deterministic ID, and the key segment `storyboard-v1/sheet-0000.jpg`. Upload integrity
  checks match ingest (local SHA-256 verified against the stored object).
- Tile geometry is derived from the version constants and the sheet dimensions, so no
  metadata column is needed. Changing geometry means a new version and a backfill.

### 4. Waveform, version 1

- Source: the transcription audio Asset (mono 16 kHz PCM WAV) ingest already produces.
- 20 peaks per second; each byte is the maximum absolute sample in its 50 ms window, scaled
  linearly to 0–255. Computed in Python by streaming the WAV in chunks — a pure,
  deterministic function with unit tests for silence, full scale, odd tail lengths, and
  malformed headers.
- Stored as one Asset of kind `waveform` (already declared, never produced), content type
  `application/octet-stream`, `duration_ms` of the source, key segment `waveform-v1.bin`.

### 5. Read endpoints (`api/routes/studio.py`)

Strict response models; five-minute signed URLs like the existing preview routes.

- `GET /api/v1/projects/{project_id}/storyboard` →
  `{ version, intervalMs, tileWidth, tileHeight, columns, rows, durationMs, sheets: [{ index, startMs, tileCount, url }], expiresAt }`.
- `GET /api/v1/projects/{project_id}/waveform` →
  `{ version, peaksPerSecond, durationMs, url, expiresAt }`.
- A Project with no derivative yet answers the same 404 as a missing Project; the frontend
  falls back as `Poster` describes.
- The browser fetches waveform bytes with `fetch()` from the signed URL. Verify the bucket's
  CORS policy allows `GET` from the application origin — browser upload `PUT`s already depend
  on the same policy.
- `GET /api/v1/projects/{project_id}/transcript` →
  `{ language, durationMs, words: [{ id, text, punctuation, startMs, endMs, speaker }] }` from
  the Project's newest canonical Transcript. No read of the transcript exists today, and the
  Project page's side column and review mode both show it. A Project without a Transcript
  answers the same 404 as a missing Project.
- `GET /api/v1/clips` gains `order` (`created`, the current default, or `recent`, ordering by
  the Edit's last update). `recent` is a top-N read for Home: it returns `nextCursor: null`,
  and combining it with `cursor` answers `422 VALIDATION_ERROR`. Omitted, behaviour is
  unchanged.
- Re-export `contracts/openapi.json` and regenerate the client;
  `scripts/check-contracts-clean.sh` must pass.

### 6. Backfill

`python -m clipah.studio.backfill_preview_media --workspace-id UUID --user-id UUID [--dry-run] [--retry-failed]`
admits `PREVIEW_MEDIA` for every active Project in that one Workspace that has a proxy and no
storyboard, through normal admission, under the key `backfill:{project_id}:preview-media-v1`.
Both identifiers are required: the named User must be a live member allowed to write Projects,
and the command never enumerates Workspaces, so it cannot reach past the tenant it was given.
`--retry-failed` re-admits Projects whose last preview job ended without a storyboard, under a
key suffixed with that Job's ID. It prints one line per Project with the outcome.

### 7. Retention

Project purge and account deletion (Task 45) must remove storyboard and waveform objects. Add
a test proving it.

### 8. Read rate limit

`read_requests_per_minute` default rises from 60 to 300; write and upload limits are
unchanged. A dashboard page load issues several reads, so 60 blocked ordinary browsing. Update
`ENVIRONMENT_SETUP.md` and record the reason in `PROGRESS.md`.

## Delivery Order

Each phase ends with its gates green: the four frontend gates for frontend changes
(`pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`) and the four backend gates for
backend changes. Unit and browser tests whose selectors change are updated in the same phase
as the copy or markup that breaks them, never deferred.

0. **Baseline.** Capture screenshots of every route at 1440, 820, and 390 px into
   `docs/design/signal/before/`.
1. **Foundation.** Dark tokens, removal of `next-themes`, `dark:` variants, and light-mode
   tokens, fonts, contrast test, rebuilt `components/ui`, lint bans, shell (rail, top bar,
   command palette, render queue dock, global drop), toasts. Existing pages render inside the
   new shell with the new tokens.
2. **Backend media.** Migration, `PREVIEW_MEDIA`, storyboard, waveform, endpoints, `order`
   parameter, backfill, retention test, rate limit, regenerated contracts. Run the backfill
   against local data only after a dry run.
3. **Media components.** `Poster`, `Filmstrip`, `Waveform`, `StageBar`; apply to Home,
   Projects, Clips, and the clip page.
4. **Project and review.** Project page restructure, "Why this moment" sheet, review mode.
5. **Editor.** Layout, transport, timeline, inspector, caption editor, style controls,
   keyboard additions.
6. **Everything else.** Export dialog, Publishing, Library, Settings, public pages, and the copy
   pass across the whole product.
7. **Verification.** The checks below, then screenshots into `docs/design/signal/after/`.

## Verification and Acceptance

- **Identity:** no banned pattern appears on any route; the frontend contains no `violet-`,
  `indigo-`, `purple-`, `fuchsia-`, or `bg-gradient-` classes and no chromatic token (HSL
  saturation of 20 % or more) with a hue between 240 and 300; both fonts load (`document.fonts` lists Archivo and JetBrains Mono).
- **Media:** with derivatives present, every Project, moment, clip, export, and asset tile
  shows a real frame and scrubs on hover without a request per frame; without them, the
  fallback frames render and nothing says "No preview yet".
- **Core journey:** create a Project, import, follow real stages, review in review mode with
  the keyboard, edit captions in the transcript editor, restyle, crop, export, and download —
  without entering an identifier or reading milliseconds.
- **Editor:** tool switching keeps drafts; autosave, undo and redo, revision conflicts, and
  export revision binding still pass their existing tests; the playhead drags and the ruler
  seeks; every existing shortcut still works and none fires inside a text field.
- **Backend:** preview media never changes Project status; a failed or refused preview job
  leaves transcription and analysis unaffected; redelivery does no repeated media work;
  foreign identifiers answer 404; the backfill is idempotent; purge removes derivatives.
- **Limits:** browsing twenty pages in a minute produces no rate-limit error.
- **Accessibility:** WCAG 2.2 AA contrast for every token pair; keyboard access to every
  control including timeline items and review mode; visible focus; reduced motion honoured;
  `vitest-axe` checks on each rebuilt screen with no violations.
- **Responsive:** no horizontal overflow at 1440, 820, and 390 px on every route; the editor
  meets its tablet and phone behaviour.
- **Browser suite:** Chromium and WebKit. Report fixture-backed results separately from any
  live-provider run.
- **Performance:** on a production build, Home's largest contentful paint under 2.5 s locally;
  storyboard sheets under 300 KB each.

## Constraints and Out of Scope

**Preserved.** Routes and deep links; generated API types and `apiFetch`; CSRF, session, and
tenant rules; the 404 indistinguishability rule; the composition reducer, preview engine,
autosave, and revision rules; resumable uploads and idempotent submissions; publishing
consent and eligibility rules; the job center's single connection; nothing rendered as markup.

**Out of scope.** A light theme; new AI or provider behaviour; merging or removing pages,
publishing from inside the editor, or other journey changes; a phone timeline editor; face
detection for smart crop; stock image retrieval; subscription or pricing changes.

**Recording.** On completion, add Section 14 to `plan.md` summarising this extension and a
"Post-rebuild extension — Signal Studio redesign" entry to `PROGRESS.md` with gate output and
anything left unverified. The owner commits; the suggested message is
`feat: signal studio redesign`.
