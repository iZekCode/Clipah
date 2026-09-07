# Context-Safe Clip Variants and Platform Packaging — Design

Task 32 of `plan.md`. This document records the decisions the plan leaves open, so the
implementation can be read against something more specific than nine checkboxes.

## Purpose

A ranked candidate is a proposal about where a moment starts and ends. Task 32 asks two
further questions about that proposal — *is this cut honest?* and *what would it look like
at 20, 30, 45, 60, or 90 seconds on each platform?* — and lets a member attach evidence to
a claim the clip makes.

Nothing here renders, publishes, or edits anything. A Variant is a proposed boundary; a
Context Warning is a reviewable observation; Claim Evidence is what a member said about a
source, never what Clipah verified.

## Existing seams this builds on

- `highlights/provider.py` already defines the shape a provider-neutral port takes here:
  a Protocol, stable retryable and terminal error codes, a `ProviderCall` usage record,
  and an offline deterministic implementation. Context safety copies it rather than
  inventing a second style.
- `highlights/extractor.py` already establishes the rule that a provider may only point at
  word IDs, and that every timestamp, boundary, and excerpt is resolved from the
  authoritative transcript. Variants and warnings obey the same rule.
- `highlights/evaluation.py` already computes context-safety recall over labeled fixtures.
  Task 32 extends that harness rather than starting a second one.
- `jobs/admission.py` meters durable work. Variants deliberately do **not** use it; see
  *Execution model* below.

## Domain vocabulary

Two nouns enter `CONTEXT.md`:

**Clip Variant** — a proposed alternative boundary for one Clip Candidate, expressed in
authoritative word IDs, carrying a hook strategy, a target duration, and platform
packaging. It changes no Edit until a member creates one from it.

**Claim Evidence** — a User's record of where a claim in a clip came from: a normalized
HTTPS source, a title, a publisher, and a retrieval date, bound to an exact word range.
Clipah never asserts that evidence makes a claim true.

A third term is already in use but undefined: **Context Warning** — a typed, evidenced
observation that a proposed boundary may mislead, carrying a severity and a suggested safe
boundary.

## Context safety

### Two engines, one answer

The plan asks for "strict-schema context assessment and deterministic validation against
surrounding transcript words". Both run, and neither is trusted alone:

1. **A provider assessment** (Groq, reusing the configured extraction model) proposes
   warnings under a closed JSON Schema. It may only name word IDs it was shown.
2. **Deterministic validation** resolves every proposed warning against the transcript. A
   warning naming an unknown word ID, a range outside the candidate, a type outside the
   closed set, or a suggested boundary that is not a real word is **discarded**, not
   repaired.
3. **Deterministic detection** runs independently and adds the warnings that rules can
   establish without a model at all — a span ending inside a question, a leading pronoun
   with no antecedent, a negation stranded outside the cut, a caveat clause removed by the
   boundary, an enumerated list cut mid-item.

A deployment with no Groq credential loses step 1 and keeps the other two, exactly as
stock B-roll degrades to a Workspace's own footage. The assessment is a `ProviderCall` like
any other and is recorded as one.

### Warning types

A closed enum, because an open one cannot be evaluated:

`cut_off_question`, `cut_off_payoff`, `missing_negation`, `missing_attribution`,
`unsupported_reference`, `omitted_caveat`, `incomplete_list`, `claim_needs_source`.

Each warning carries `severity` (`info`, `warning`, `blocking`), `evidence_word_ids`
inside the candidate span, and an optional `suggested_start_word_id` /
`suggested_end_word_id` naming a boundary that would resolve it.

### What warnings do not do

They never rewrite the transcript, never silently move a boundary, and never disappear
because reranking preferred a candidate. A blocking warning refuses a *variant*; it does
not delete the candidate a member may still choose to read.

## Variants

`ClipVariantGenerator.generate(candidate, platforms, durations_ms)` returns at most three
hook strategies × the requested duration targets, and every returned Variant satisfies all
of:

- its start and end are real word IDs of the same Transcript, in order;
- its span preserves a complete thought — it neither begins mid-sentence nor ends inside a
  question or an unfinished list;
- its duration is within tolerance of the requested target (20/30/45/60/90 seconds only;
  any other target is refused rather than rounded);
- it carries no warning at or above the configured blocking severity.

A target that cannot be satisfied yields **no** Variant. Returning a 20-second cut that
severs a payoff would be worse than returning nothing.

**Hook strategies** are deterministic boundary policies, not model output: `cold_open`
(start at the candidate's strongest opening sentence), `question_first` (start at the
question whose answer the clip contains), and `statement_first` (start at the claim, with
the setup trimmed). A strategy that does not apply to a candidate simply produces nothing.

## Platform packaging

Static data, not an integration. Each of TikTok, Instagram Reels, and YouTube Shorts
contributes an aspect ratio, safe-zone insets, title length guidance, a caption style
recommendation, and the export preset name the render compiler already understands. No
upload, no account, no API call — Tasks 36-43 own publishing.

## Claim Evidence

A member attaches evidence to an exact word range of one candidate. The server:

- normalizes and validates the URL: HTTPS only, no embedded credentials, no private or
  loopback address, no executable scheme, reusing the rules `assets/source_validation.py`
  already applies to imports;
- bounds every text field and rejects raw HTML rather than sanitizing it;
- refuses a word range that is not inside the named candidate;
- records `created_by` and the instant, and treats `verification_status` as a field only a
  User may set — the default is `unverified` and nothing Clipah does changes it;
- renders as a *citation-overlay suggestion*, which like a B-roll Suggestion changes no
  Edit until someone accepts it.

## Execution model

Variant generation is **synchronous**, not a durable Job. One candidate is one bounded
provider call over a window that is already small, and a member is choosing between
variants interactively. Adding a `JobKind` would mean a queue round trip for work measured
in seconds.

Consequences, stated rather than hidden: the request is bounded by the provider timeout
and answers `503` when the provider is unavailable; it spends the existing write rate
limit and **no metered quota**, because the plan's limit table names no variant budget.
That is the same gap B-roll planning records, and it belongs to Tasks 44-46 with the rest
of operational cost accounting.

## Persistence

Two tables, both tenant-scoped. The plan's schema sketch omits `workspace_id`; the
non-negotiable rules in `AGENTS.md` require it on every tenant-scoped row, with a composite
`(workspace_id, id)` foreign key to the parent, so this design adds it.

- `clip_variants`: `id`, `workspace_id`, `project_id`, `candidate_id`, `kind`, `platform`,
  `hook_strategy`, `target_duration_ms`, `start_word_id`, `end_word_id`, `start_ms`,
  `end_ms`, `title`, `rationale`, `warnings`, `packaging`, `created_at`.
- `claim_evidence`: `id`, `workspace_id`, `project_id`, `candidate_id`, `start_word_id`,
  `end_word_id`, `claim_text`, `source_url`, `source_title`, `publisher`, `retrieved_at`,
  `verification_status`, `created_by_user_id`, `created_at`, `updated_at`.

Both carry RLS policies matching every other tenant table, and least-privilege grants: the
API reads and writes both; the worker needs neither.

**The migration is `0015`, not the `0005` the plan names.** That numbering predates ten
migrations that have since landed.

## HTTP surface

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/projects/{project_id}/candidates/{candidate_id}/variants` | `EDIT_WRITE`, CSRF, idempotent by key. Body names platforms and duration targets only. |
| `GET` | `/projects/{project_id}/candidates/{candidate_id}/variants` | `PROJECT_READ`. |
| `POST` | `/projects/{project_id}/candidates/{candidate_id}/claim-evidence` | `EDIT_WRITE`, CSRF. |
| `GET` | `/projects/{project_id}/candidates/{candidate_id}/claim-evidence` | `PROJECT_READ`. |
| `PATCH` | `/claim-evidence/{evidence_id}` | `EDIT_WRITE`, CSRF. The only way `verification_status` changes. |

A candidate the caller has no standing on answers the same `404` as one that does not
exist, as everywhere else.

## Frontend

- `ScoreBreakdown` — why this candidate ranked where it did, as figures rather than prose.
- `ContextWarnings` — typed warnings with their evidence and suggested boundary, rendered
  as text and never as markup.
- `VariantLab` — compare variants against **one** proxy. No variant duplicates the source,
  the transcript, or any asset; a variant is a pair of word IDs and a seek position.
- `EvidencePanel` — attach and review Claim Evidence. External links open isolated
  (`rel="noopener noreferrer"`), display the normalized host as text, and never render
  provider or user HTML.

## Evaluation

`highlights/evaluation.py` gains warning **recall** and **precision** against labeled
fixtures, and variant **boundary validity** — the share of generated variants whose word
IDs resolve, whose duration matches its target, and which carry no blocking warning.

Semantic preservation and user acceptance are recorded as **unmeasured**: both need human
review and the real-audio corpus that the harness already reports as short. Inventing a
number for either would misrepresent what has been measured.

## What Task 32 deliberately does not do

- No automatic fact-checking. Version 1 records what a User said about a source.
- No publishing. Packaging is metadata a later task consumes.
- No variant metering, pending Tasks 44-46.
- No brand kit or template application; Task 33 owns those.
