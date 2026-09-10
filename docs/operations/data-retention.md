# Data retention, recovery, and deletion

This is what Clipah keeps, how long it keeps it, and what an operator can expect to see
while retention is running. Everything here is enforced by code in `backend/src/clipah/retention/`
and covered by `backend/tests/integration/test_retention.py`.

## The one rule

Nothing is deleted except through a **retention tombstone**. A tombstone names one
Workspace, one entity, one storage prefix, and the instant that entity becomes eligible
for removal. A sweep may only remove what a tombstone names. A failed attempt is written
back onto the same tombstone; it never widens its target and never picks a different one.

A tombstone survives the data it removed. It is the record that a deletion happened, and
it is kept alongside the audit trail after everything else in a Workspace is gone.

## How long each kind of data lives

| Data | Window | Setting |
| --- | --- | --- |
| Soft-deleted Project | 30 days | `CLIPAH_RETENTION_SOFT_DELETED_PROJECT_DAYS` |
| Deleted Workspace | 30 days | `CLIPAH_RETENTION_SOFT_DELETED_WORKSPACE_DAYS` |
| Deleted account's identity data | 30 days | `CLIPAH_RETENTION_DELETED_USER_DAYS` |
| Abandoned multipart upload | 24 hours | `CLIPAH_RETENTION_ABANDONED_UPLOAD_HOURS` |
| Failed-job working directory | 7 days | `CLIPAH_RETENTION_FAILED_JOB_WORKSPACE_DAYS` |
| Rejected generated draft | 24 hours | `CLIPAH_RETENTION_REJECTED_GENERATED_DRAFT_HOURS` |
| Stock preview nobody chose | 24 hours | `CLIPAH_RETENTION_UNSELECTED_STOCK_PREVIEW_HOURS` |
| Revoked or expired source connection | immediate | fixed at zero |

Source and proxy media, exports, transcripts, and candidates live until their Project is
deleted, and then follow the Project's window. A suggestion still marked `proposed` is
left alone: it is what a member is currently looking at, and it expires with its Project.

Durations are configuration. Shorten or lengthen them in the environment; never in code.

## What a sweep does

The sweep runs on the `maintenance` queue every
`CLIPAH_RETENTION_SWEEP_INTERVAL_SECONDS` (five minutes by default), one Workspace per
transaction:

1. **Scan.** Find records whose window has closed — abandoned uploads, revoked
   connections, unchosen B-roll, failed-job workspaces — and write a tombstone for each.
   Nobody deletes these on purpose, so expiry is discovered rather than announced.
2. **Claim.** Take up to `CLIPAH_RETENTION_BATCH_SIZE` due tombstones with
   `SELECT ... FOR UPDATE SKIP LOCKED`, so a second sweeper works on different data
   instead of waiting behind the first, and two sweepers never share a prefix.
3. **Discharge.** Remove the media first, then the rows, then close the tombstone.

Media is removed before rows because a row is the only remaining record of where an
object lives. Losing the row while the object survives leaves storage nobody can find.

Listings are bounded to `CLIPAH_RETENTION_LISTING_PAGE_SIZE` keys per pass. A Workspace
larger than one page is finished across several sweeps, and its tombstone stays open
until the prefix is empty. Every key a provider returns is checked against the prefix
before it is deleted: a listing is evidence about the world, not an instruction.

## What defers, and what fails

- **Deferred.** A Project or Workspace with an unfinished Job, or a Publication that has
  not settled, is left alone this pass. The tombstone keeps its date and the next sweep
  tries again.
- **Failed.** An unreachable object store increments `failure_count` and records
  `RETENTION_STORAGE_UNAVAILABLE`. The rows survive, so nothing is half-deleted. After
  `CLIPAH_RETENTION_MAX_FAILURES` attempts the tombstone stops being claimed and waits
  for an operator, rather than retrying forever against something that is not working.

Watch `clipah.retention.outcome` (labelled `outcome` and `code`) and the
`retention.sweep.finished` log line, which reports what one pass scheduled, purged,
deferred, and failed.

## Deleting a Project

`DELETE /api/v1/projects/{project_id}` hides the Project immediately and refuses any new
Job for it from that moment — admission locks the Project row and declines a deleted one.
`POST /api/v1/projects/{project_id}/restore` brings it back inside the window and
withdraws the pending tombstone with it. After the window the purge removes the Project's
media prefix and its rows, children before parents.

## Deleting a Workspace

`DELETE /api/v1/workspaces/{workspace_id}` requires an owner and an authentication within
the last ten minutes. It closes the Workspace to further work and, at once rather than in
thirty days:

- revokes every source connection,
- revokes every Social Account and cryptographically erases its OAuth Grant, whether or
  not the provider could be reached,
- cancels or pauses every unpublished Publication,
- writes a `workspace.deleted` audit event,
- schedules the Workspace tombstone.

**Posts already published stay on their platforms.** Clipah stops publishing and forgets
its credentials; it does not delete someone's live YouTube, Instagram, or TikTok content.
Removing those is done on the platform itself. The API says so explicitly in the deletion
response (`publishedPostsRemainOnProviders`).

`POST /api/v1/workspaces/{workspace_id}/restore` recovers a Workspace for its owner while
the window is open. Recovery returns the Workspace, not its credentials: revoked
connections stay revoked and are reconnected deliberately.

After the window, the purge removes every tenant row except the compliance record: audit
events, tombstones, Membership rows and their history, and the quota ledger survive, as
does the Workspace row itself, marked deleted.

## Deleting an account

`DELETE /api/v1/account` requires an authentication within the last ten minutes and is
refused with `LAST_OWNER` while the caller is the only owner of a live Workspace. A
Workspace nobody owns cannot be recovered or deleted by anyone, so the caller transfers
ownership or deletes the Workspace first — including their personal Workspace, which only
they can decide about.

Once accepted, it revokes every Session immediately, removes every Membership, cancels
the unpublished Publications that person approved, marks the account deleted so the same
Google identity cannot sign in again, and schedules the erasure of their identity data.

Thirty days later the purge deletes their sign-in identities and replaces their name and
address with a placeholder. The `users` row itself remains, because Projects, audit
events, and Membership history all reference it, and an audit trail that cannot name its
actor is not an audit trail.

## Who retention runs as

The sweep connects with the migration principal, because the trigger that protects
append-only history admits the table owner alone, and retention is the one caller allowed
to remove that history. It is not privileged in the tenant sense: row-level security is
forced on the owner too, so every statement is confined to the one Workspace the
transaction declared, and the delete authorization is bound to that transaction and
expires with it. The actor it declares belongs to no person and holds no Membership.

Give the retention process `CLIPAH_MIGRATION_DATABASE_URL` and nothing else it does not
need. A deployment without it refuses to sweep rather than sweeping with the wrong
credentials.
