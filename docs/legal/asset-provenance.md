# Asset provenance

This document records how Clipah acquires third-party visual media, what it stores about
each piece, and which obligations follow from the licences it relies on. It exists so that
a question asked a year after a clip is published — *was this footage ever licensed for
this use?* — has an answer that does not depend on anybody's memory.

## The rule

**An external asset becomes selectable only after complete provenance is stored beside
it.** The check is in `clipah/broll/retriever.py` (`provenance_of`), it runs before
anything is downloaded, and it refuses the candidate outright when any field is missing.
The asset row and its provenance row are written in the same database transaction, so an
asset nobody can trace cannot exist even briefly.

The stored fields are:

| Field | Why it is required |
| --- | --- |
| `provider` | Which licence regime applies at all. |
| `provider_asset_id` | Identifies the work at the source if a takedown or dispute arrives. |
| `source_url` | The page a reviewer can open to check the asset still exists on the stated terms. |
| `author`, `author_url` | The creator, for attribution and for any later claim. |
| `license_name`, `license_url` | Which licence was granted. |
| `terms_snapshot` | The licence text **as it read on the day of retrieval**, because provider terms change and the version that applied is the one that matters. |
| `retrieved_at` | When the grant was taken. |
| `query` | What Clipah asked for, which shows the asset was retrieved for a purpose rather than scraped. |
| `moderation_result` | That a safe-search filter was applied and passed. |
| `attribution_text` | The exact credit line to display if a member chooses or is required to. |
| `checksum` | Ties the licence record to the exact bytes stored, so a substituted file is detectable. |

`prompt`, `model`, `model_version`, and `seed` are reserved for generated media and are
empty for retrieved media. The plan forbids secrets in this table; nothing written to it
comes from anywhere but a normalized provider record, and no credential, signed URL, or raw
provider payload is ever stored.

## Providers

### Pexels

- Licence: **Pexels License** — <https://www.pexels.com/license/>
- Attribution: not required, but Clipah stores and can display a credit line anyway.
- Obligations honoured: safe-search filtering before storage; no redistribution of
  unmodified assets; no use that would create a competing stock service; results cached
  rather than re-requested (24 hours).

### Pixabay

- Licence: **Pixabay Content License** — <https://pixabay.com/service/license-summary/>
- Attribution: not required; a credit line is stored and available.
- Obligations honoured: `safesearch=true` on every request; no redistribution on a
  competing platform; no sale of unaltered content; results cached for at least 24 hours,
  which the terms require.

## What Clipah does and does not do

**Only the selected asset is downloaded.** A search returns metadata; exactly one candidate
per suggestion is fetched, and only after it has been ranked and accepted. Systematically
downloading search results is a licence breach at both providers and is not something the
code is able to do — `broll_retrieve_task.py` fetches inside the branch that stores.

**Nothing is hotlinked.** The `download_url` a provider returns is used once and never
persisted. Media a member may see is served from Clipah's own private object storage
behind five-minute signed URLs, so a provider's bandwidth is not used to serve Clipah's
product and a provider deleting an asset cannot break a published clip.

**Provider search responses are cached** (`clipah/broll/search_cache.py`) for the duration
each provider's terms require. The cache stores normalized candidates rather than raw
provider payloads, so nothing provider-shaped and nothing resembling a credential is
written to Redis.

**Safe search is applied twice** — as a provider-side filter, and again on this side before
storage. A candidate marked unsafe is refused by the provenance gate regardless of how it
was retrieved.

**Reuse is scoped to one Workspace.** Identical bytes are stored once per Workspace and
never shared across tenants, because one Workspace's asset library must not depend on what
another Workspace searched for.

## Retention

Section 7 of `plan.md` requires unselected stock previews to be deleted after 24 hours, and
accepted assets to follow project retention. Clipah stores no unselected previews at all —
only selected assets are ever downloaded — so the first obligation is met by construction.
Deleting accepted assets on project retention is owned by Task 45.

## Open items before enabling stock retrieval in production

1. **Provider accounts and credentials.** `CLIPAH_PEXELS_API_KEY` and
   `CLIPAH_PIXABAY_API_KEY` are unset by default, and a provider with no configured key is
   simply absent. No stock provider is contacted until an operator sets one.
2. **Terms review at the configured version.** The `terms_snapshot` constants in
   `pexels_adapter.py` and `pixabay_adapter.py` are summaries written against the terms as
   read on 2026-09-05. They must be re-read and, if they have changed, updated before
   enablement — the stored snapshot is only evidence if it is accurate.
3. **Attribution display.** Neither licence requires attribution, so the editor does not
   currently show a credit line. If a future provider requires one, the display is a
   product change and not only a data change; the attribution text is already stored.
4. **Takedown handling.** No process exists yet for a provider or creator asking that a
   stored asset be removed. `asset_provenance` carries everything such a request needs to
   be actioned; the operational procedure is not written.
5. **A third provider.** Adding one means a new adapter, a new licence snapshot, and a new
   section here — not a change to any domain module.
