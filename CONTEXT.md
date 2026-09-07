# Clipah

Clipah turns creator-owned long-form media into reviewable short-form edits and publishes approved artifacts to selected social destinations.

## Identity and tenancy

**User**:
A human who signs in to Clipah. A User does not own project data directly; access comes through Workspace Memberships.
_Avoid_: Account, customer, owner

**Login Identity**:
A provider-issued identity used by a User to authenticate to Clipah, uniquely identified by issuer and subject.
_Avoid_: Social Account, session, email identity

**Session**:
A revocable Clipah login established after a Login Identity is verified.
_Avoid_: OAuth token, login identity

**Workspace**:
The tenant that owns projects, assets, brand kits, quotas, social connections, and publication history. Every User starts with a personal Workspace and may join team Workspaces.
_Avoid_: User account, organization, project owner

**Workspace Membership**:
A User's role-bound relationship to a Workspace.
_Avoid_: Project member, account access

## Creation and editing

**Project**:
One source-media body of work and its transcript, candidates, assets, edits, and exports within a Workspace.
_Avoid_: Upload, job, clip

**Clip Candidate**:
A timestamp-safe proposed moment derived from a Project's canonical transcript.
_Avoid_: Render, exported clip

**Edit**:
The identity of an editable clip whose history is a sequence of immutable Edit Revisions.
_Avoid_: Video file, draft render

**Edit Revision**:
An immutable, versioned composition snapshot that can be reviewed or rendered reproducibly.
_Avoid_: Autosave, file version

**Render Artifact**:
An immutable media file produced from one Edit Revision and one render preset.
_Avoid_: Edit, source asset

**Clip Variant**:
A proposed alternative boundary for one Clip Candidate, expressed in authoritative word IDs and packaged for one platform. It changes no Edit until a member creates one from it.
_Avoid_: Cut, version, render preset

**Context Warning**:
A typed, evidenced observation that a proposed boundary may misrepresent the speaker, carrying a severity and a suggested safe boundary.
_Avoid_: Error, lint, moderation flag

**Claim Evidence**:
A User's record of where a claim in a clip came from — a normalized HTTPS source, a title, a publisher, and a retrieval date — bound to an exact word range. Clipah never asserts that it makes the claim true.
_Avoid_: Fact check, verification, citation proof

**B-roll Suggestion**:
A provenance-aware proposed visual placement that has no effect on an Edit until an authorized User accepts it.
_Avoid_: Automatic edit, generated clip

## Connections and publishing

**Source Connection**:
A revocable authorization used only to import creator-authorized source media.
_Avoid_: Social Account, Login Identity

**Social Account**:
A Workspace-scoped YouTube channel, Instagram professional account, or TikTok creator account that may receive approved publications.
_Avoid_: User, login account, OAuth token

**OAuth Grant**:
The encrypted, least-privilege provider credential that authorizes operations on one Social Account.
_Avoid_: Session, social account

**Publication**:
The intent to send one approved Render Artifact to one explicitly selected Social Account using a frozen metadata and consent snapshot.
_Avoid_: Campaign, publish-to-all request

**Publication Job**:
The durable, provider-specific execution of one Publication, including scheduling, transfer, processing, reconciliation, and terminal status.
_Avoid_: Batch publication, render job

**Publication Batch**:
A user convenience grouping of independent Publications created from one confirmation action.
_Avoid_: Atomic multi-platform publish

**Brand Kit**:
A Workspace's published, versioned constraints on how its clips may look and what they may assert: colours, type, caption safe zones, visual exclusions, required attribution, and forbidden claims. A composition records the exact version it was judged against.
_Avoid_: Theme, style guide, preset

**Template**:
A Workspace-owned, versioned look — caption treatment and drawn-text type — applied to a composition by value, so a Revision renders identically after the template changes.
_Avoid_: Preset, theme, layout

**Campaign Output**:
Generated supporting copy or creative guidance associated with an approved Edit Revision; it is not a social publication.
_Avoid_: Publication, social post
