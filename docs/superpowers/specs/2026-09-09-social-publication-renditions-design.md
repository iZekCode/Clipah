# Social Publication Renditions and Preflight Design

## Scope

Task 38 makes publication media validation deterministic and preserves every byte and
decision used to prepare one provider upload. The existing `1080x1920` H.264/AAC Render
Artifact is the canonical high-quality social master. A Publication always remains bound
to the Render Artifact and SHA-256 snapshot created in Task 37.

This task does not implement provider HTTP publishing, live capability discovery, UI, or
automatic watermark removal. Tasks 39-43 own those behaviors.

## Provider profiles

`publishing/profiles.py` owns immutable, versioned `ProviderProfile` values for YouTube,
Instagram, and TikTok. A profile describes accepted containers, video/audio codecs,
duration, byte size, aspect ratio, resolution, frame rate, audio requirements, caption and
thumbnail requirements, safe zones, watermark policy, disclosure requirements, and
metadata bounds. The profile version is explicit and persisted; validation never reads
provider documentation or mutable configuration at runtime.

The initial fixtures are conservative snapshots of official provider documentation as of
2026-09-09. Later provider-adapter tasks may add newer profile versions without changing
old validation evidence:

- YouTube upload: https://developers.google.com/youtube/v3/docs/videos/insert
- Instagram Reels publishing: https://www.postman.com/meta/workspace/instagram/documentation/23987686-9386f468-7714-490f-9bfc-9442db5c8f00
- TikTok media restrictions: https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
- TikTok watermark policy: https://developers.tiktok.com/doc/content-sharing-guidelines

## Validation model

`publishing/preflight.py` accepts normalized media facts, the Publication's frozen
metadata/options/consent, and a `ProviderProfile`. It returns a `PreflightReport` with the
profile version, a deterministic ordered set of typed violations, and non-destructive
remediation. It checks every Task 38 constraint without throwing on ordinary user-fixable
invalidity.

Malformed internal inputs raise a sanitized domain error. Reports never contain storage
keys, signed URLs, provider diagnostics, or raw FFmpeg output. TikTok promotional marks
produce a blocking violation telling the member to render a clean master; validation never
removes or obscures a mark.

Safe-zone validation consumes the composition-derived overlay bounds recorded with the
master rather than attempting computer vision. Caption, thumbnail, disclosure, and
metadata checks consume the Publication snapshot. This keeps preflight deterministic and
honest about evidence the system actually has.

## Immutable rendition storage

Migration `0021` adds a tenant-scoped `social_renditions` table. Each row records:

- source Render Artifact and its SHA-256;
- provider and profile version;
- immutable output SHA-256, byte size, duration, and internal object key;
- FFmpeg argument/config version and normalized source checksums;
- the complete secret-free validation report;
- creation time and retention linkage to the source artifact.

The cache identity is uniquely constrained by
`(workspace_id, source_sha256, provider, profile_version)`. Runtime roles can select and
insert rows but cannot update or delete them. Composite Workspace foreign keys and forced
RLS preserve the repository's tenant invariant. `publications.social_rendition_id` points
to the exact cached result selected for dispatch; the API never serializes its object key.

## Rendition execution

`publishing/renditions.py` decides whether the canonical master already satisfies a
profile. A compliant master is represented as a byte-for-byte reuse result and no new
object is written. Otherwise, `publishing/render_tasks.py` runs one fixed, shell-free
FFmpeg argument vector in the Job workspace, with cancellation checks before download,
before FFmpeg, before upload, and before persistence.

The worker downloads only the frozen Render Artifact, verifies its recorded SHA-256,
transcodes from those bytes, probes and validates the output, uploads under a deterministic
tenant key, verifies stored length and SHA-256, then inserts the immutable cache row.
Concurrent attempts converge through the unique cache key. A retry reuses a healthy row
and never reads mutable Edit state.

## Publication flow

The draft preflight endpoint loads each Social Account's current capability version,
selects the matching profile, validates the frozen master and Publication snapshot, and
stores a public report in the Publication checkpoint metadata. A blocking report leaves
the Publication in `awaiting_approval`; a passing report makes it eligible for explicit
confirmation.

Immediately before provider dispatch, worker-side revalidation repeats the same profile
selection and validation against the chosen master or rendition. If the provider
capability/profile version differs from the approved snapshot, or the latest review
decision no longer approves the Publication's exact Edit Revision, the Publication
transitions to `awaiting_approval` and stores a field-level diff. It never silently adopts
new defaults. Connection or authorization failures retain Task 37's existing transition
behavior.

## Testing and stopping condition

Unit tests cover every profile field, deterministic violation ordering, clean/pass cases,
TikTok watermark rejection, safe remediation, metadata boundaries, and capability diffs.
Integration tests cover RLS, immutable provenance, master reuse, actual rendition caching,
concurrent convergence, checksum verification, cancellation boundaries, private-key
non-disclosure, and approval/capability revalidation. Golden-media tests use checked-in or
existing deterministic fixtures and skip only when the repository's established native
FFmpeg prerequisite is unavailable.

The task stops when every Task 38 checkbox is covered, migration downgrade/upgrade/drift
passes, all four backend gates pass, and `PROGRESS.md` records the result and any genuine
deferral. No provider adapter or publishing UI work is pulled forward.
