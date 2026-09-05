# Authenticated YouTube imports

This document is the gate. Authenticated YouTube import stays **disabled in production**
until every section below is filled in with a decision somebody actually made, and the
sections that are still open are marked as open rather than left blank.

`CLIPAH_AUTHENTICATED_SOURCE_IMPORT_ENABLED` defaults to `false`, and the production
profile pins it to `false` unless a deployment sets it explicitly. With the flag off, the
connection routes answer exactly like routes that do not exist, `GET /api/v1/me` reports
the capability as `false`, and the browser renders no way to begin.

## What the feature does

A member exports the cookie jar for their own YouTube session and uploads it. Clipah
reduces the file to the handful of cookies YouTube authentication actually needs, encrypts
what survives, and uses it — for at most seven days — to import videos that member has
access to and Clipah's public importer cannot reach.

## What it costs the member

This is the most dangerous thing the product asks anybody to do, and the consent dialog
says so in these words:

- A cookie file is a live signed-in session. Anyone holding it can act as that member on
  that account until it stops working.
- Google may restrict or ban an account whose session is used by automated tools.
- The connection is kept for at most seven days, and less when the member's own cookies
  expire sooner.
- Signing out of YouTube in the browser the jar came from ends it immediately, and the
  member can revoke it at any time.
- Only the member's own account may be connected.

Both confirmations — understanding the risk, and owning the account — are recorded with
the connection, and a request without either is refused with
`SOURCE_CONNECTION_CONSENT_REQUIRED`.

## How the credential is handled

| Stage | What happens |
| --- | --- |
| Upload | The jar is size-checked at 256 KiB before it is decoded, parsed as Netscape format, and reduced to the documented allowlist of names on `.youtube.com` and `.google.com`. Anything else is dropped; a file this parser cannot understand is refused whole. |
| Storage | The accepted rows are encrypted under a fresh AES-GCM data key, and that key is wrapped by a key derived from `CLIPAH_SECRET_ENCRYPTION_KEY` through HKDF with a label naming this one use. The Workspace and Connection are authenticated data, so a row copied elsewhere cannot be opened. |
| Separation | `source_connections` holds the metadata a member sees. `source_connection_secrets` holds the material, and the API role has `INSERT`, `DELETE`, and column-level `SELECT` on the identifying columns only: an API process can store a credential and destroy it, and can never read one back. The worker role has `SELECT`. |
| Lease | A worker borrows the credential for one Job, bounded by the connection's own expiry and a thirty-minute lease window. The lease never prints what it holds. |
| Use | The jar is written `0600` inside the Job's own `0700` workspace, passed to yt-dlp as one argument-array value, and removed on success, failure, cancellation, and worker shutdown. |
| Revocation | Revoking marks the connection and **deletes the secret row**. There is nothing left to lease. |

`--cookies-from-browser` is **not used and not offered** by the hosted product: on a hosted
worker the browser profile belongs to the machine rather than to the member, so the flag
would read somebody else's session. It remains a reasonable command for a **local or
self-hosted** operator whose worker and browser belong to the same person:

```bash
yt-dlp --cookies-from-browser firefox "https://www.youtube.com/watch?v=..."
```

That command is documented here and nowhere in the product.

## What is verified by tests

- `backend/tests/unit/test_youtube_cookie_validation.py` — format, line endings, expiry,
  flags, the allowlist, unrelated domains, control characters, duplicates, oversized
  files, and that no refusal names a cookie value.
- `backend/tests/unit/test_source_secrets.py` — envelope encryption, per-Workspace
  binding, tampering, wrong keys, and lease expiry and discard.
- `backend/tests/unit/test_authenticated_youtube.py` — the jar's permissions, its path,
  and its removal on success, failure, and interruption.
- `backend/tests/integration/test_source_connections.py` — the feature flag, both
  consents, tenancy, revocation, expiry, lease refusals, the least-privilege grants, and
  the worker's use of the leased jar across a retry.
- `backend/tests/security/test_source_secret_redaction.py` — one canary value driven
  through the whole feature and then hunted for in every response, every column of every
  table, the logs, and the text of every refusal.
- `frontend/tests/youtube-connection.test.tsx` — the dialog does not exist without the
  capability, states every warning, refuses to send without both confirmations, and shows
  a connection only by its metadata.

## Open items — production enablement is blocked until these are decided

Each of these needs a named owner and a date. **Nothing here is decided yet.**

- [ ] **Legal approval.** Whether offering this feature is acceptable under YouTube's
      Terms of Service for this product, in the jurisdictions it operates in, with the
      wording of the consent dialog reviewed as part of that decision.
- [ ] **Data retention.** Whether seven days is the agreed maximum, what is logged about a
      connection's lifecycle, and how long those records are kept.
- [ ] **Incident response.** What happens when a credential is suspected of being exposed:
      who is told, how connections are revoked in bulk, and what the member is told.
- [ ] **Key management.** Whether production wraps data keys with a managed key
      (`CLIPAH_SECRET_MANAGER_KEY_NAME`) rather than the local key material, and how that
      key is rotated.

## Pinned versions

These are the versions the source-import image is built with. Any change to them is a
change to this document.

| Component | Version |
| --- | --- |
| yt-dlp | `2026.08.19` |
| yt-dlp-ejs | `0.8.0` |
| Deno | `2.9.5` |
| FFmpeg / ffprobe | `7.1.5-0+deb13u1` |
| uv | `0.12.7` |
| PO Token plugin | **none pinned** — no plugin is installed, and adding one requires a
  revision recorded here first. |
