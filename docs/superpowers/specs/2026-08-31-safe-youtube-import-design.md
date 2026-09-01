# Safe YouTube Import Design

**Date:** 2026-08-31
**Status:** Approved and self-reviewed
**Plan scope:** `plan.md` Task 10

## Goal

Add one safe convenience connector that imports a single public, non-live YouTube video into a
Workspace-scoped Project. The API creates durable work and returns immediately; an isolated source
import worker validates the source again, downloads into the Job workspace, stores the source under
a server-owned object key, and records the resulting source Asset.

Task 10 is split into ordered parts 10a, 10b, and 10c. Task 10 remains incomplete until every part
and all four backend gates pass.

## Non-goals

- Authenticated, private, members-only, age-restricted, playlist, or cookie-backed imports. Task 27
  owns those capabilities and their legal/security gates.
- ffprobe media validation, proxies, thumbnails, or transcription audio. Task 11 owns ingest.
- General-purpose URL downloading. The adapter accepts only normalized YouTube video URLs.
- Production deployment orchestration. Task 46 owns Compose/Railway topology; Task 10 creates only
  the independently buildable source-import image and its runtime readiness probe.
- An enabled PO Token implementation. Task 10 defines the port only.

## Part 10a: URL and network safety

`assets/source_validation.py` exposes:

```python
validate_youtube_url(url, *, resolver=None) -> NormalizedYouTubeUrl
revalidate_youtube_url(source, *, resolver=None) -> NormalizedYouTubeUrl
validate_redirect_chain(source, redirects, *, resolver=None) -> NormalizedYouTubeUrl
```

`NormalizedYouTubeUrl` contains the canonical URL, the eleven-character video ID, the normalized
host, and the validated DNS address snapshot from that call. The one-argument call remains the
stable public interface; the optional resolver makes domain code deterministic in tests.

Accepted hosts are exactly `youtube.com`, `www.youtube.com`, `m.youtube.com`, and `youtu.be`, after
case and trailing-dot normalization. Accepted single-video forms are `watch?v=`, `shorts/`,
`embed/`, `live/`, and the first `youtu.be` path segment. Every form canonicalizes to
`https://www.youtube.com/watch?v=<video-id>`. A `list` query, playlist path, userinfo, non-HTTPS
scheme, non-default port, malformed ID, or deceptive hostname suffix is rejected.

The resolver must return at least one address, and every IPv4/IPv6 result must be globally routable.
Private, loopback, link-local, multicast, reserved, and unspecified addresses are rejected. The API
performs an admission-time check but does not persist its inherently short-lived DNS result. In the
worker, a bounded manual source-URL HTTP preflight follows at most five redirects, validating every
hop against the same host and IP rules without reading the response body. That preflight establishes
the worker snapshot. Immediately before metadata access and again before download, the source host
is resolved and the complete address set must equal the worker snapshot. Changed or unsafe results
fail before yt-dlp download. This scope applies to the user-supplied source navigation; provider
media-CDN URLs discovered after extractor validation remain yt-dlp's responsibility and are
constrained by the isolated worker's egress policy in Task 46.

`assets/youtube.py` defines provider-neutral request/metadata values, the source error hierarchy,
the `SourceImporter` contract, and an optional `PoTokenProvider` protocol. No production PO Token
adapter or plugin is enabled.

## Part 10b: yt-dlp adapter and isolated runtime

`source_connectors/yt_dlp_adapter.py` contains provider details behind three injectable boundaries:

- a command runner that executes argument arrays with a fixed timeout, sanitized environment,
  bounded stdout/stderr, and `shell=False`;
- a bounded source-preflight transport that exposes redirect hops without reading response bodies;
- the DNS policy supplied by the source-validation module.

Metadata preflight runs before download with `--no-config`, `--no-playlist`, `--skip-download`, and
single-JSON output. The result must identify the YouTube extractor, exactly one entry, a public and
non-age-restricted video, a non-live state, and a duration no greater than four hours. Provider
payloads stay inside the adapter and normalize into provider-neutral metadata.

Download runs separately inside `job_workspace(job_id)`. It uses no certificate bypass, browser
profile, cookie file, shared configuration, remote component update, or shell command. yt-dlp is
given a fixed output template, a two-GiB size ceiling, no-playlist mode, and the validated canonical
URL. The reported final path must resolve as a direct child of the Job workspace before it can be
opened or uploaded.

The adapter computes SHA-256 while reading the completed file. The existing object-store boundary
gains one exact-file upload operation that accepts a server-generated key and returns
`StoredObject`; S3 and deterministic fake implementations remain provider-neutral.

`infra/docker/source-import.Dockerfile` is independently buildable and pins:

- yt-dlp `2026.08.19`;
- yt-dlp-ejs `0.8.0`;
- Deno `2.9.5`, matching the yt-dlp release's pinned dependency metadata;
- Debian 13's FFmpeg package `7:7.1.5-0+deb13u1` (FFmpeg `7.1.5`).

The Docker base and package repository snapshot are immutable references so a disappeared package
cannot silently substitute another build. Updating those references is an explicit source-import
runtime change.

The image contains no login, transcription, LLM, generation, render, or social-publishing
credentials. Its readiness command checks `yt-dlp --version`, FFmpeg/ffprobe versions, Deno
execution, installed EJS availability, and an optional public-video metadata probe. The network
probe is opt-in and excluded from normal CI.

## Part 10c: durable import workflow

`POST /api/v1/projects/{project_id}/youtube-imports` requires a live Session, Workspace Project
write authority, CSRF proof, and `Idempotency-Key`. Its request body contains only `url`.

The route validates syntax and the first DNS snapshot before opening durable work. One transaction:

1. locks and verifies the active Project;
2. creates or replays a `SOURCE_IMPORT` Job through the existing admission policy;
3. creates or reloads the matching `SourceImport` with normalized URL and source video ID;
4. records the Job ID and queues only UUID strings for the source-import worker.

The response is `202` and contains `sourceImportId`, `jobId`, and `status`. A repeated key and payload
returns the same identifiers. A conflicting payload receives the existing sanitized conflict
response. A dispatch failure leaves the durable Job queued; an idempotent retry may dispatch it
again instead of creating another row.

The worker reloads and authorizes the Workspace through the existing Job machinery, creates the Job
workspace, and calls `YtDlpSourceImporter`. The object key is deterministic from Workspace, Project,
and SourceImport IDs. The source Asset uses the SourceImport UUID as its own deterministic ID, which
makes concurrent/retried inserts converge on one row without a schema migration. It stores source
type `source_import`, content type, byte length, SHA-256, and provider duration; Task 11 later fills
validated stream/codec metadata and creates derived assets.

On success the SourceImport becomes `completed`; on terminal provider policy failure it becomes
`failed`; cancellation becomes `canceled`. Recoverable provider/network failure leaves the Job retry
path authoritative. The Job runner checks cancellation before metadata, before download, and before
upload.

## Error model

All errors use the existing sanitized envelope and fixed public-message table. Provider exception
text, URLs beyond the normalized public source, filesystem paths, commands, and raw metadata never
leave the worker.

| Code | Meaning | Retry |
| --- | --- | --- |
| `SOURCE_UNSUPPORTED` | Invalid source form, playlist, live/member-only media, extractor mismatch, or unsupported metadata | No |
| `SOURCE_PRIVATE` | Private or age-restricted media | No |
| `SOURCE_TOO_LONG` | Provider duration exceeds four hours | No |
| `SOURCE_TLS_FAILED` | Certificate or TLS verification failure | No |
| `SOURCE_UNAVAILABLE` | Timeout, transient network failure, or provider outage | Yes |

Unsafe DNS, rebinding, and redirect attempts use `SOURCE_UNSUPPORTED` publicly and retain only a
sanitized internal reason.

## Tests

### 10a

- Table tests for every accepted host/path form and canonical output.
- Rejections for scheme, port, userinfo, deceptive suffix, malformed IDs, and playlist parameters.
- IPv4 and IPv6 private, loopback, link-local, multicast, reserved, and unspecified address tests.
- Redirect-to-disallowed-host, unsafe redirect DNS, empty DNS, and rebind/address-change tests.

### 10b

- Recording-runner tests for exact required arguments and forbidden cookie/browser/TLS/config flags.
- Metadata tables for public, private, age-restricted, members-only, playlist, live, too-long,
  malformed, TLS, timeout, and provider failures.
- Output path containment, deterministic hash/upload, cleanup, cancellation-boundary, and fake
  adapter tests.
- Readiness tests for version mismatch and missing yt-dlp/EJS/Deno/FFmpeg tools.
- One environment-gated network smoke test marked `slow` and skipped by default.
- Build and run the isolated source-import image readiness command.

### 10c

- Real-Postgres route tests for authorization, CSRF, active Project checks, idempotent replay,
  conflicting payload, concurrent retry, cross-Workspace indistinguishability, and admission limits.
- Eager worker tests proving UUID-only task arguments, repeated delivery creates one SourceImport,
  one source Asset, and one object key, and cancellation stops between stages.
- Fake object-store/importer tests for successful completion and every stable error code.
- Full backend Ruff, format, strict mypy, and pytest/coverage gates.

## Operational and security notes

- Direct upload remains the primary ingestion path; public YouTube import is a convenience.
- Runtime version checks fail readiness instead of silently using an incompatible extractor stack.
- Updating yt-dlp/EJS/Deno is an isolated source-import image release and does not redeploy API or
  render workers.
- The worker should additionally run with restricted egress and resource limits when Task 46
  completes deployment topology.

## Primary references

- yt-dlp `2026.08.19` release: <https://github.com/yt-dlp/yt-dlp/releases/>
- Tagged yt-dlp dependency metadata: <https://raw.githubusercontent.com/yt-dlp/yt-dlp/2026.08.19/pyproject.toml>
- yt-dlp EJS setup guidance: <https://github.com/yt-dlp/yt-dlp/wiki/EJS/5a91e64e1f5e817ff0c72db3587f9a806bae9f56>
- yt-dlp-ejs project requirements: <https://github.com/yt-dlp/ejs>
- Deno releases: <https://github.com/denoland/deno/releases>
- Debian 13 FFmpeg `7:7.1.5-0+deb13u1`: <https://packages.debian.org/trixie/ffmpeg>
