# 0001 — Browser editor engine

- **Status:** Accepted, with one gate outstanding (FFmpeg parity, owned by Task 24).
- **Date:** 2026-09-03
- **Decides:** which browser engine Clipah's editor (Tasks 23–26) is built on.
- **Decision:** the **OpenReel approach over Mediabunny** — Clipah owns the timeline, and
  Mediabunny is used only at the media boundary. **Elah is not adopted.**
- **Gate:** `plan.md` § "Editor-engine adoption gate" and Task 21.

## Context

Clipah owns its composition schema, its editing UX, and its FFmpeg compiler. What it does
not want to own is browser decoding, frame scheduling, seeking, waveform generation, and
muxing. The adoption gate exists so that decision is made against evidence rather than
preference, and so the cost of leaving whichever engine is chosen is known before it is
adopted.

Four candidates were named. Two were measured:

1. **Elah** (`@elah/core` 0.4.1), Apache-2.0 — the plan's preferred embeddable engine.
2. **OpenReel's architecture over Mediabunny** 1.55.5, MPL-2.0 — the fallback and reference.
3. **OpenVideo Editor** — has no npm package at all; evaluating it means vendoring source
   under commercial terms. Not measured.
4. **Remotion** 4.0.520 — published as `SEE LICENSE IN LICENSE.md`, and the plan itself
   says it "is not treated as a complete editor". Not measured.

## Method

`BrowserEditorEngine` (`frontend/spikes/editor-engines/adapter.ts`) is the port every
candidate is measured through. No candidate's types appear in it.

`frontend/tests/editor-engine-contract.test.ts` runs the same thirty-one assertions against
every candidate — load and give the fixture back unchanged, seek to exact frame boundaries,
play and pause, trim, split a media item and a caption, edit karaoke words without moving
their timings, apply a 9:16 crop, undo and redo one step at a time, hand a history over and
take it back, render a preview frame or name the capability it needs, read a waveform or
name the capability it needs, and go inert once disposed — then requires every candidate to
end the same operation sequence holding a byte-identical canonical composition.

`frontend/e2e/editor-engine-parity.spec.ts` bundles the spike with esbuild, injects it into
a real page, and takes every timing inside that page. Both engines really decode: Elah
through `createDefaultDemuxerFactory` → `GpuRenderer` → `resolveTimeline`, waiting until
`VideoLayer`'s provider actually holds the requested source frame; the Mediabunny adapter
through `UrlSource` → `CanvasSink.getCanvas`. A seek that decodes nothing is recorded as a
failure and withholds the seek gate, so a fast-but-blank render cannot pass.

Fixtures were generated with the same encoder settings Task 11 uses for real proxies
(`ffmpeg.py:213` — 720p, libx264 veryfast, CRF 23, yuv420p, AAC 128k, faststart): a
30-minute file (626 MB) and a 60-minute file (1.2 GB), served over a range-capable local
server.

## Measurements

Reference machine: **Mac17,2 (Apple M5), macOS 26.6**. 20 sampled seeks spread across the
whole timeline, first discarded as warm-up and reported separately as time-to-first-frame.
Every run decoded 19/19 sampled frames unless stated.

### Chromium, headed, ANGLE Metal Renderer: Apple M5

| Engine | Proxy | Time to usable | Median seek | p95 seek | Heap | Leaked workers |
| --- | --- | --- | --- | --- | --- | --- |
| Elah | 30 min | 658 ms | **108 ms** ✓ | 408 ms | 101 MiB | 0 |
| Mediabunny | 30 min | 29 ms | **44 ms** ✓ | 143 ms | 109 MiB | 0 |
| Elah | 60 min | — | — | — | — | — |
| Mediabunny | 60 min | 87 ms | **38 ms** ✓ | 70 ms | 132 MiB | 0 |

### WebKit, headed, Apple GPU

| Engine | Proxy | Time to usable | Median seek | p95 seek | Heap | Leaked workers |
| --- | --- | --- | --- | --- | --- | --- |
| Elah | 30 min | 647 ms | **409 ms** ✗ | 658 ms | not observable | not observable |
| Mediabunny | 30 min | 142 ms | **82 ms** ✓ | 300 ms | not observable | not observable |
| Elah | 60 min | **timed out after 900 s** ✗ | — | — | — | — |
| Mediabunny | 60 min | 141 ms | **110 ms** ✓ | 243 ms | not observable | not observable |

Safari exposes neither `performance.memory` nor a worker count, so those two gates are
reported unmeasured there rather than claimed. The suite asserts that they *are* reported
unmeasured, so a browser limitation can never be mistaken for a passing engine.

### Gate results

| Gate | Threshold | Elah | Mediabunny |
| --- | --- | --- | --- |
| Initial load (to first decoded frame) | < 5 s | pass | pass |
| Median seek after warm-up | < 150 ms | **fail** — 409 ms in WebKit | pass — 38–110 ms |
| Bounded memory | < 1.5 GiB | pass (101 MiB, Chromium) | pass (109–132 MiB, Chromium) |
| No leaked workers after `dispose()` | 0 | pass | pass |
| Safari codec fallback | degrades gracefully | pass — see below | pass |
| One-hour proxy playback | holds | **fail** — 900 s timeout in WebKit | pass |
| Frame/timing parity vs. FFmpeg | ≤ 1 frame, SSIM ≥ 0.97 | **not measured** | **not measured** |

**Safari needs no fallback.** WebKit turned out to support WebCodecs: both engines decoded
every sampled frame there. The degradation test passes because the capability is present,
not because a fallback was exercised.

### A measurement that was nearly wrong

The first Chromium runs were headless, where WebGL falls back to SwiftShader. Under
software rendering Elah's median seek was 432 ms; on the real GPU it is 108 ms. Elah is
GPU-composited and Mediabunny's path is decode-to-canvas, so headless measurement penalised
one candidate and not the other. Every number above comes from a headed run, and the suite
now records the graphics renderer string alongside each measurement so this cannot recur
unnoticed.

## Findings beyond the numbers

- **Elah cannot hold part of Clipah's composition.** Its `Clip` has no caption word array,
  no karaoke state, and no crop rectangle, and its track kinds have no caption track. The
  adapter keeps those in a parallel `extras` map and carries them through every operation.
- **Elah's undo cannot be Clipah's undo.** Because the crop and the word timings were never
  in Elah, its history cannot restore them; the adapter restores from Clipah's canonical
  snapshot and rebuilds Elah's timeline from it. `TimelineEngine.undo`/`redo` go unused.
- **Elah publishes no waveform API.** Its audio surface is playback and a resolver, with
  nothing that reads an envelope. The gate lists waveform as required, so Clipah would have
  to write it — which is precisely what the Mediabunny route already does via
  `AudioBufferSink`.
- **Elah downloads whole sources.** Its default demuxer resolves a `Blob` before decoding;
  Mediabunny's `UrlSource` reads byte ranges. On a 1.2 GB proxy that is the difference
  between a header read and a full download, and it shows in time-to-usable (658 ms vs
  29 ms) and in the 60-minute WebKit timeout.
- **`@elah/core` ships ESM with extensionless internal imports** that Node's resolver
  refuses; `vitest.config.ts` records this by inlining the package.
- **Elah is three months old** (first published 2026-06-09) with 127 exported symbols.
- **Choosing Mediabunny does not avoid Mediabunny.** `@elah/core` depends on it, so the
  MPL-2.0 obligation would have arrived under either option.

| | Elah 0.4.1 | Mediabunny 1.55.5 |
| --- | --- | --- |
| License | Apache-2.0 (pulls MPL-2.0 mediabunny, zustand, immer) | MPL-2.0 |
| Published JS, gzipped | 67 KiB | 397 KiB |
| Public API surface | 127 symbols | 23 symbols |
| Last published | 2026-08-02 | 2026-08-31 |
| Timeline model | tracks, clips, trim, split, undo/redo | none; Clipah writes it |
| Contract test | 31/31 | 31/31 |

## Decision

**Adopt the OpenReel approach over Mediabunny.** Clipah owns the timeline, its history, and
its composition; Mediabunny is used only for demuxing, decoding, and envelope reading.

The deciding evidence is Safari. Elah misses the seek gate there by 2.7× on real Apple GPU
hardware, and cannot complete a one-hour timeline at all. Safari is not optional for an
Indonesia-first consumer product. On Chromium with a GPU Elah is fine — 108 ms — but an
engine that is fine on one of two required browsers has not passed a gate; it has passed
half of one.

The secondary evidence is that Elah was contributing less than its API suggested. Its
history is unusable for our model, it holds none of our caption or crop state, and it has no
waveform at all. Of the three things worth borrowing — decode scheduling, frame-accurate
seeking, GPU compositing — the first two are Mediabunny underneath in both candidates.

What Clipah takes on is the timeline arithmetic and a canvas compositor: about 260 lines
today in `openreel-mediabunny-adapter.ts`, growing through Tasks 25 and 26. That is the
price, and it is worth paying for a candidate that clears every observable gate in both
browsers at both lengths.

### Still outstanding

Frame and timing parity against the native FFmpeg renderer (≤ 1 frame, SSIM ≥ 0.97) needs
the fixture render Task 24 produces and the documented font mask. It is a `test.fixme` in
the parity spec, and Task 24 must discharge it. If parity fails, this decision is cheap to
revisit: the composition, its canonical form, caption words, crop, and history are all
Clipah's under either option, by design of the port.

## Escape cost

Low, and deliberately so. Nothing about Clipah's composition lives inside a vendor type.
Replacing Mediabunny means replacing demux, decode, and envelope reading behind
`BrowserEditorEngine` — the same three calls the adapter makes today. The contract test
would keep passing throughout, which is what it was written to guarantee.

## Consequences

- `@elah/core` is no longer needed and should be removed with its adapter once Task 23
  begins. Both are retained until the Task 24 parity gate is discharged, so the comparison
  can be re-run if that gate fails.
- `mediabunny` becomes a production dependency. MPL-2.0 is file-level copyleft: modified
  MPL files must be published; unmodified use in a larger work does not infect it.
- `scripts/check-editor-licenses.sh` passes over all 754 resolved packages and reports the
  weak-copyleft obligations (`mediabunny`, `axe-core`, and an LGPL native binary that never
  reaches the browser bundle). OpenVideo Editor and Remotion remain unreviewed and must not
  be added without a recorded owner and renewal cost; the script fails on either today.
