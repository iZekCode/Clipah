# Accessible Workspace Review Workflows — Design

Task 35 of `plan.md`. This document narrows the approved task into implementation
boundaries that fit the repository as it exists after Task 34.

## Purpose

Workspace members need a safe way to invite collaborators, change their roles, remove
their access, transfer ownership, and review one immutable Edit Revision. The editor also
needs deterministic warnings that help a member correct captions and platform placement
without silently changing the composition.

The task does not publish content or connect Social Accounts. It establishes the
authorization and approval contracts that Tasks 36-43 will consume.

## Delivery order

Work proceeds as six vertical slices, each completed through red-green TDD:

1. collaboration schema and domain values;
2. invitations, membership mutation, ownership transfer, and audit history;
3. revision comments, decisions, resolution, and stale-review behavior;
4. the complete Workspace permission contract;
5. deterministic accessibility analysis;
6. team, review, and accessibility interfaces plus OpenAPI regeneration and full gates.

The migration is `0018`, not the `0008` named in the old task sketch, because seventeen
migrations have already landed.

## Existing seams

- `DatabaseWorkspaceAuthorizer` remains the only authority reader. Every mutation loads a
  live Workspace Membership immediately before it installs tenant context.
- `WorkspaceAction`, `ROLE_ACTIONS`, and the Workspace publishing policy remain the one
  role matrix. Route-specific owner checks are replaced with named actions rather than
  duplicated.
- `WorkspaceInvite` already reserves the core invite fields. Task 35 completes its use
  cases and API rather than creating a parallel invitation model.
- `ClipEditRevision` is immutable and is the only valid review target. Saving another
  Revision never edits review history; it makes earlier positive approval stale.
- API failures retain the fixed sanitized envelope and guessed identifiers retain the
  exact missing-resource response.
- Frontend request types continue to come from exported OpenAPI output and the generated
  client.

## Collaboration persistence

### Existing rows

`workspace_invites` continues to hold one SHA-256 token hash, explicit non-owner role,
creator, expiry, acceptance, and revocation timestamps. Only the raw token returned by
invite creation is shown once; it is never stored or logged. The email is delivery
metadata only. Acceptance authenticates the current User independently and never compares
provider identity by email.

`workspace_memberships` retains removed rows as evidence. Re-inviting a removed User
reactivates that same `(workspace_id, user_id)` relationship with the accepted role and a
new `joined_at`, so the primary key remains stable.

### New audit rows

`workspace_membership_events` is append-only and tenant-scoped. Each event records the
Workspace, affected User or invite, actor User, event type, old role, new role, and
timestamp. Event types are closed: invite created/revoked/accepted, role changed, member
removed, and ownership transferred. Runtime roles receive `SELECT` and `INSERT`, never
`UPDATE` or `DELETE`.

Every tenant relationship uses a composite Workspace foreign key where the parent is
tenant-scoped. RLS uses the repository's existing Workspace/User predicate.

## Membership rules

- Owners and admins may create, revoke, and list invitations for `admin`, `editor`,
  `reviewer`, or `viewer`; only an owner may grant or revoke the owner role.
- Invite tokens expire, are single-use, are invalid after revocation, and are accepted by
  the authenticated User represented by the current Session.
- A member cannot remove or demote the last active owner. Ownership transfer is one locked
  transaction that promotes the recipient to owner and demotes the actor to admin.
- Removing a member stamps `removed_at`. Subsequent requests fail at live authorization,
  including already-open browser sessions.
- Role changes and removals lock all active owner memberships before enforcing last-owner
  protection, preventing concurrent requests from removing the final owner.
- Personal Workspaces retain exactly one owner and do not accept invitations or ownership
  transfer.

The Publication tables do not exist until Task 37. Task 35 therefore exposes and tests a
pure `may_prepare_publication(role, publishing_role_policy)` policy through the shared
authorizer. Task 37 must invoke that policy while locking pending Publications during role
changes or removal; Task 35 cannot cancel rows that do not yet exist.

## Review persistence and state

Three tenant-scoped append-oriented tables are introduced:

- `edit_review_comments`: immutable text and anchor data plus creator and Revision.
  An anchor is either a non-negative timestamp within the composition duration or one
  existing composition item ID. Text is plain bounded Unicode and is returned as text.
- `edit_review_comment_resolutions`: one append-only resolution or reopening event,
  recording actor and time. Current resolved state is derived from the latest event.
- `edit_review_decisions`: append-only `request_changes` or `approve` decisions for one
  exact Edit Revision, recording actor and time.

Comments may be added by reviewers, editors, admins, and owners. Review decisions require
`REVIEW_DECIDE`, so viewers cannot comment or decide. Authors may resolve their own
comment; editors, admins, and owners may resolve any comment because they are responsible
for producing the next Revision.

An approval is current only when its target Revision equals `clip_edits.current_revision`
and no later request-changes decision exists for that Revision. Saving a new Revision does
not mutate the old approval; the API reports it as stale and keeps the complete audit
history. Campaign generation refuses a Revision without current approval. Future
publication preparation consumes the same predicate.

## HTTP surface

All Workspace identifiers remain explicit query/path inputs, unsafe methods require CSRF,
and membership-management actions require recent authentication.

| Method | Path | Authority |
| --- | --- | --- |
| `GET` | `/workspaces/{workspace_id}/members` | `MEMBER_READ` |
| `GET/POST` | `/workspaces/{workspace_id}/invites` | read / `MEMBER_MANAGE` |
| `DELETE` | `/workspaces/{workspace_id}/invites/{invite_id}` | `MEMBER_MANAGE` |
| `POST` | `/workspace-invites/{token}/accept` | authenticated User plus valid token |
| `PATCH` | `/workspaces/{workspace_id}/members/{user_id}` | `MEMBER_MANAGE` |
| `DELETE` | `/workspaces/{workspace_id}/members/{user_id}` | `MEMBER_MANAGE` |
| `POST` | `/workspaces/{workspace_id}/ownership-transfers` | `OWNERSHIP_TRANSFER` |
| `GET/POST` | `/edits/{edit_id}/reviews` | `PROJECT_READ` / `REVIEW_DECIDE` |
| `POST` | `/edits/{edit_id}/review-comments` | `REVIEW_DECIDE` |
| `POST` | `/edit-review-comments/{comment_id}/resolution` | review resolution policy |
| `GET` | `/edits/{edit_id}/accessibility` | `PROJECT_READ` |

Review write bodies name the immutable Revision ID, not merely its sequence number.
Unknown or cross-Workspace Edit, Revision, comment, invite, and member identifiers receive
the repository's indistinguishable `404` response.

## Permission contract

`backend/tests/contract/test_workspace_permissions.py` enumerates every action family that
exists after Task 34: Workspace, membership, Project, Asset, Job, Clip Candidate, Edit,
B-roll, Render, Brand Kit, Template/Campaign Output, review, Source Connection, and the
future Social Account/Publication actions already named by the shared policy. Each role is
tested against literal expected outcomes. The contract also proves recent-authentication
requirements and indistinguishable missing/cross-Workspace resources through real routes
for representative tenant objects.

## Accessibility analysis

Accessibility checks are pure functions over one composition and platform packaging. They
read no clock, network, browser, or database. Each warning has a stable code, severity,
human-readable action, optional item ID/time range, and measured value/threshold.

Fixed thresholds are documented beside the implementation and tests:

- caption reading speed: warning above 20 characters per second;
- caption minimum duration: warning below 1,000 ms;
- caption overlap: warning whenever visible caption intervals intersect;
- line count: warning above two rendered lines;
- contrast: WCAG 2.2 AA ratio below 4.5:1 for normal text and below 3:1 for large text;
- safe zone: warning when caption or drawn-text bounds cross the selected platform inset;
- platform UI collision: warning when bounds intersect documented TikTok, Reels, or Shorts
  control regions.

Checks never rewrite captions, move overlays, or change styles. Geometry-dependent checks
report that bounds are unavailable when the composition lacks measurable bounds instead of
inventing coordinates.

## Frontend

`TeamSettings` lists members and pending invites, exposes only actions allowed by the
current role, confirms removal/transfer, and returns focus to the initiating control after
a dialog closes.

`ReviewPanel` shows the current Revision, stale/current approval status, timestamp/item
comments, resolution history, and approve/request-changes controls. Selecting an anchor
seeks or selects the matching editor location without changing the composition.

The Project review surface also lists the Project's past Jobs and links its clips to their
Revision history, review state, and exports. It reuses the existing Workspace Job and Edit
reads; it does not create a second job-history model.

`AccessibilityPanel` groups deterministic warnings by severity, focuses the selected
caption/item, describes the exact corrective action, and uses no animation when
`prefers-reduced-motion` is set. Loading and error-only editor states gain a `main`
landmark.

All controls are keyboard reachable with visible focus, dialogs manage focus, status
changes use appropriate live regions, and user/provider text is rendered only as React
children.

## Verification

Focused RED/GREEN cycles precede each implementation slice. Final verification includes:

- migration upgrade, downgrade, re-upgrade, and schema-drift check;
- the backend permission, review, and accessibility suites;
- all four backend gates from `AGENTS.md`;
- OpenAPI export and generated-client cleanliness;
- frontend unit tests with axe-compatible assertions, Playwright team/review flows, and
  all four frontend gates;
- `git diff --check` and an updated `PROGRESS.md`.

The collaboration feature flag hides team/review mutation surfaces for plans that do not
include collaboration while leaving ordinary Workspace reads and deterministic
accessibility checks available.

## Explicit deferrals

- Cancelling concrete pending Publications is implemented with Task 37, when Publication
  persistence exists. Task 35 supplies the role-policy predicate and audit facts it needs.
- Outbound invitation email is not introduced. The API returns a one-time invite link.
- Social Account and provider OAuth behavior stays in Task 36.
- Accessibility warnings are advisory and deterministic; automatic correction is outside
  Task 35.
