# Clipah: Guided Creator Studio Redesign

## Summary

Redesign the complete product for solo creators, with one clear journey:

**New project → Import video → Choose moments → Edit → Export → Publish**

Use a calm, light visual style and plain English. Prioritize making the next step obvious, while keeping advanced capabilities accessible.

Implement in the existing `feat/rebuild-foundation` worktree. Preserve the rebuilt media pipeline and editing engine; include targeted functional additions needed to connect the experience.

## Design and Navigation

- **Visual system:** warm off-white background, white surfaces, charcoal text, violet primary actions, subtle borders, and restrained shadows. Use consistent typography, spacing, form controls, status badges, and loading states through the existing Tailwind/Radix components.
- **Main navigation:** Home, Projects, Clips, Publishing. Group Assets, Templates, and Brand Kits under Library. Place Team and Connections within Settings.
- **Global controls:** workspace switcher, search, activity indicator, user menu, and a prominent **New project** action.
- **Page structure:** clear title, short explanation where useful, one primary action, and contextual secondary actions. Move rename/delete operations into item menus.
- **Responsive behavior:** full editor on desktop and larger tablets; phones support importing, reviewing, downloading, and publishing. On narrow screens, show an edit preview and a clear route to continue editing on a larger screen.
- Preserve existing URLs and search-result timecodes. Navigation grouping does not require replacing established routes.

## Page and Workflow Changes

### 1. Foundation, Home, and Project Creation

- Build shared page headers, media cards, empty states, loading skeletons, status indicators, and actionable error notices.
- Home prioritizes **New project**, recent projects, and work ready to continue. Move detailed quota information to Settings; surface relevant limits when starting metered work.
- New project opens an upload/import dialog. Default to file upload, with YouTube import as a second tab.
- Collect media and an editable project name together; suggest the filename for uploads. Create the Project when submitted, then use the existing upload/import flow.
- Preserve resumable uploads and idempotent submissions. A failed upload keeps its Project available for retry.

### 2. Projects and Clip Discovery

- Projects becomes a browsable media grid with thumbnails, names, processing status, and contextual actions.
- Project detail shows source preview, processing progress, and tabs for **Moments, Edits, Exports, and Activity**.
- Show the next useful action according to durable state: add media, follow processing, review moments, continue editing, or open exports. A Project can contain clips at different stages.
- During processing, show real stage information, reconnection state, and supported recovery actions. Do not invent percentages or completion estimates.
- Present ranked moments as preview cards with hook, duration, a short selection rationale, and **Edit clip**. Keep detailed scores, evidence, and variants behind expandable sections.
- Clips opens with browsable content before a search is entered. Distinguish suggested moments from saved Edits and available exports.
- Clip detail resolves its own Project context and exposes preview, available Edits, revision history, exports, variants, evidence, and campaign copy.

### 3. Focused Editor and Export

- Replace the long stack of panels with a dedicated studio: header, tool rail, large preview, selected tool panel, contextual inspector, and bottom timeline.
- Group tools into Captions, Layout, Media/B-roll, Audio, Text, Style, and Review. Put keyframes, motion, karaoke timing, and source controls within their relevant tools.
- Keep playback, undo/redo, save state, project return navigation, and **Export** readily available.
- Preserve the composition reducer, preview engine, keyboard shortcuts, autosave, revision conflict handling, provenance, and review rules. Switching panels must not discard draft changes.
- Export opens preset selection, saves pending changes, and requests a render bound to the intended saved revision.
- Show queued/running/failed/completed export states. Completed exports offer **Download** and, when available, **Publish**.
- Exports remain accessible after navigation or refresh.

### 4. Publishing and Supporting Pages

- Publishing opens to a queue/history view grouped into scheduled, in progress, published, and needs attention.
- **New publication** begins by choosing a completed eligible export. Entering from an export preselects that artifact.
- Organize composition into artifact preview, destination selection, destination-specific fields, timing, and final confirmation.
- Preserve explicit destination selection, provider consent requirements, review eligibility, capability gates, and independent retry/cancel behavior.
- Assets becomes a real media browser with Project/type filters, previews, metadata, and available provenance. Retain current Project ownership; do not introduce cross-project asset reuse.
- Templates gains visual previews; Brand Kits gains clear editing and version information.
- Settings exposes supported workspace, membership, connection, session, and usage controls. Remove promises of preferences that have no implementation.
- Redesign landing, sign-in, demo, and invitation pages using the same identity. Explain creator outcomes; use clearly labeled example content in the public demo.

## Technical Changes and Delivery Order

1. Establish design tokens and shared components, then apply the new shell and Home.
2. Complete project creation, processing guidance, and Project detail.
3. Deliver clip browsing and reliable detail navigation.
4. Reorganize the editor and connect export/download.
5. Complete publishing entry points, libraries, Settings, and public pages.
6. Run cross-page accessibility, responsive, and workflow verification.

**Interfaces and backend work:**

- Add workspace-scoped, paginated reads for clip browsing and asset browsing; include filters needed by these screens.
- Add a candidate detail resolver that returns its Project context, associated Edits, and available exports.
- Add paginated export listing by Edit/Project and expose the metadata needed to select an artifact for publishing.
- Expose thumbnail/preview capabilities using existing stored media and short-lived signed access. Use intentional placeholders when media is unavailable.
- Extend render requests with an optional expected revision; reject a stale revision rather than silently exporting a newer edit. Preserve existing callers when omitted.
- Reuse existing authentication, authorization, job events, queries, and mutation APIs. Generate changed frontend contracts from backend schemas.
- Keep new browsing data derived from existing records; no new workflow state machine or media-processing subsystem.
- Record this work as a post-rebuild extension to the approved specification and progress notes. Follow the repository’s instruction that the owner commits.

## Verification and Acceptance

- **Core journey:** a new user can create a Project, import media, review a moment, edit captions/crop, export, and download without entering identifiers or manually assembling URLs.
- **Publishing:** select an eligible export, configure a destination, and reach explicit confirmation; test unavailable providers, failed preflight, partial success, and destination-specific retry.
- **Recovery:** refresh during upload/processing/rendering, reconnect job events, recover supported failures, and reopen completed exports.
- **Editor:** panel switching preserves changes; autosave, undo/redo, revision conflicts, and export revision binding remain correct.
- **Navigation:** existing deep links, workspace switching, permissions, pagination, empty libraries, and expired preview URLs behave correctly.
- **Visual/accessibility:** review all routes at desktop, tablet, and phone widths; verify keyboard access, focus, readable contrast, reduced motion, and loading/error/empty states.
- Run all four frontend gates: `pnpm lint`, `pnpm typecheck`, `pnpm test`, and `pnpm build`. Backend changes also require all four backend gates, including at least 90% coverage, plus regenerated-contract checks.
- Run Chromium and WebKit workflow tests. Report live-provider checks separately from fixture-backed results.

**Defaults:** English UI, light theme, existing Clipah branding, no new subscription system, no AI-provider changes, and no full phone timeline editor. Success means every page explains what it contains, what is happening, and what the creator can do next.
