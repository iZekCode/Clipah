# Candidate contract comparison — 2026-09-12

This is an experimental report, not an approved replacement for Section 8 or Task 13 of
`plan.md`. Production extraction, validation, ranking, schemas, and persistence are unchanged.
The probe lives in `backend/evals/highlights/contract_probe.py` and is not imported by the app.

## Question and method

Does replacing the full transcript echo with four-word endpoint proofs make candidate
extraction reliable? Does offering labeled, timed transcript segments remove the model's
need to align a separate list of word IDs with transcript text?

Three arms keep the metadata schema and generation parameters constant:

| Arm | Input | Model-selected boundaries | Evidence checked locally |
| --- | --- | --- | --- |
| baseline | Existing separate word-ID list and transcript string | Word IDs | Existing full-excerpt comparison |
| proof | Same input, with explicit 20–90-second instruction and alignment rule | Word IDs | First and last four canonical words of the selected range |
| segments | Labeled text segments with authoritative start/end milliseconds | Segment labels | First and last four canonical words of the selected range |

The two experimental arms reconstruct the full excerpt only **after** endpoint proof
validation. This is a different contract; reconstructing an excerpt is not evidence that
the model echoed it correctly. Unknown/out-of-window boundaries, reversed ranges, and
durations outside 20–90 seconds are refused. The existing candidate validator subsequently
checks metadata. Deduplication uses the production implementation, separately per generation;
candidates from different generations are never pooled to meet the minimum of three.

Segments partition all offered words in order. They end at sentence/clause punctuation,
a speaker change, a pause of at least 800 ms, or 12 words. Every segment maps back to its
first and last authoritative word IDs. Quotes match only at the proposed boundaries;
the resolver never searches for another occurrence to repair a rejected proposal.

For experimental proofs only, NFKC, case, whitespace, and punctuation normalization precede
comparison. No fuzzy alignment or edit-distance tolerance is used. The production full-quote
normalizer is unchanged.

Each arm requests five candidates with temperature 0, low reasoning, and a 4,096-token
completion ceiling. This ceiling differs from the current production adapter's 8,000, so these
results must not be described as a reproduction with identical production request settings.
Schema refusals count as failed generations; they are not silently replaced with samples that
worked. Rate-limit retries are bounded and do not count as new generations.

## Corpus and limits

The saved database contained three copies of a 510-word English transcript and one
1,727-word Indonesian transcript. The probe uses one copy per language. English has one
146,948-ms window; Indonesian has five windows, of which only the first, containing 427
words, is tested here. This does not measure full-source Indonesian analysis success.

The English database rows declare `universal-3-pro`, whereas the previous progress narrative
said `universal-2`. The experiment preserves the recorded metadata and does not resolve that
historical discrepancy. Indonesian declares `universal-2`.

No additional real English source or human-approved labels were available. Review of complete
thoughts below is an agent's qualitative assessment, not a labeled quality score, an audio
alignment measurement, or a claim that the transcription itself is accurate.

## Live results

Results are recorded in `candidate-contract-results.json` beside this report. Provider
refusals and local validation failures are reported separately. Raw generations, transcript
content, request IDs, and recovery artifacts remain in a private local artifact directory.

The 20B comparison could not complete. It produced one baseline generation with zero of five
accepted (all duration failures), and one segment generation with zero of five accepted (all
boundary-proof mismatches). Another baseline attempt and a proof attempt returned HTTP 400.
Subsequent calls were blocked by the daily quota: limit 200,000, used 197,956, requested 6,312;
the provider estimated a wait of about 31 minutes at that point. These samples do not establish
three-generation reliability for 20B.

A separate comparison uses the other already-configured model, `openai/gpt-oss-120b`. Its
results must not be pooled with 20B. The three English segment generations produced 5, 1,
and 1 survivors. The second emitted only three proposals and consumed all 4,096 completion
tokens, of which 3,319 were reasoning tokens. The other two English arms produced no survivors.

The completed 120B comparison comprises 18 generation attempts:

| Language | Arm | Accepted / candidates returned in valid responses | Survivors by generation | Provider schema refusals | Generations meeting minimum |
| --- | --- | --- | --- | --- | --- |
| English | baseline | 0 / 5 | refused, 0, refused | 2 / 3 | 0 / 3 |
| English | proof | 0 / 10 | refused, 0, 0 | 1 / 3 | 0 / 3 |
| English | segments | 7 / 13 | 5, 1, 1 | 0 / 3 | 1 / 3 |
| Indonesian | baseline | 0 / 5 | refused, 0, refused | 2 / 3 | 0 / 3 |
| Indonesian | proof | 0 / 15 | 0, 0, 0 | 0 / 3 | 0 / 3 |
| Indonesian | segments | 3 / 5 | 3, refused, refused | 2 / 3 | 1 / 3 |

The denominator is not automatically 15: a refused generation returns no usable candidates,
and one segment generation returned only three. Across all arms, 53 candidates reached local
validation, ten were accepted, and seven generations were refused by provider-side schema
validation. All ten accepted candidates came from the segment arm. Its local failures were
six boundary mismatches and two invalid durations. Baseline local failures were all durations;
proof local failures were 24 durations and one boundary mismatch. These are first rejection
reasons: a duration failure prevents subsequent quote validation, so it says nothing about
whether that proposal would also fail its quote.

The two Indonesian segment schema refusals were an unknown category and a missing `tags`
field. They are real generation failures, separate from boundary validation. The production
adapter retries schema refusals; this experiment records each generation instead of replacing
failures with successful retries and overstating reliability.

Input token savings are not universal. For English, segment prompts used 3,422 tokens against
3,717 for baseline (about 8% fewer). For Indonesian, they used 3,691 against 3,384 (about 9%
more). Successful-response usage is in the JSON; failed-generation usage was not returned.
No dollar cost or complete billable-token total was measured.

## Decision

**Do not promote either experimental contract to production.** Segment proofs improved
mechanical acceptance, but only two of six generations met the minimum, and the accepted
output still showed context problems. Shorter proofs on the existing separate ID list did
not unblock extraction. The incomplete 20B run cannot establish a model-family capability
limit or prove that segment proofs work on the configured extraction model.

A further experiment could precompute sentence-aligned, duration-valid spans and ask the
model to select and rank those span labels. That would move more boundary construction out
of generation, but requires separate evaluation of coverage and context quality. It was not
implemented or measured in this spike. Do not weaken validation, change providers, or relax
the three-survivor rule based on these results alone.

## Verification

Seventeen focused negative controls pass. The first twelve were run against unimplemented
resolver/segment functions, failed, and then passed after implementation. Controls cover
incorrect proofs, unknown and out-of-window boundaries, reversed ranges, 19/91-second spans,
canonical excerpt construction, segment partitioning, and deduplication.

All four backend gates pass after redirecting integration tests to a disposable PostgreSQL
cluster: Ruff check; Ruff format check (412 files); strict mypy (225 source files); and pytest
with coverage (2,811 passed, 20 skipped, one warning, 92.80% coverage). The gates prove the
experiment has not regressed the backend, not that the live candidate problem is fixed.
There are no frontend changes and no commit was made.

## What accepted boundaries do not prove

In the first successful English segment generation, some accepted clips open with a backward
reference or a conjunction whose antecedent lies outside the cut. One ends at a comma before
the thought finishes. All five report empty context warnings. Their text and time boundaries
pass the proposed mechanical contract, but that does not make all five self-contained moments.

Consequently, three survivors alone cannot be the release criterion for this change. Smaller
proofs do not guarantee semantic safety, and a labeled segment does not guarantee a complete
sentence. Repeated phrases can also make short endpoint proofs weaker evidence of intent than
a full quote, even when the proof matches the exact selected occurrence.

## Reproduction

From `backend/`, provide a private JSON array of canonical transcripts (metadata, `words`,
`full_text`, and `duration_ms`), and a configured Groq key in `.env`:

```bash
uv run python -m evals.highlights.contract_probe --live \
  --model openai/gpt-oss-120b \
  --transcripts /private/path/transcripts.json \
  --output /private/path/probe-results.json
```

The default is three generations of each arm on window zero. `--window-index` selects another
window; `--variants` selects arms; `--resume` skips previously successful generations in the
same report. Raw reports contain transcript excerpts and must not be checked in. The tool is
a spike, not a production adapter or a general-purpose benchmark framework.

Run its negative controls without infrastructure:

```bash
uv run pytest tests/unit/test_highlight_contract_probe.py -q
```

## Verification incident

The agent mistakenly started the full backend suite against the application's local database
before inspecting the fixtures. `clean_database` truncates application tables. The run was
stopped after discovery, but original local database rows were lost; the transcript count had
fallen from four to zero. This was caused by the agent's verification command, not by the
candidate experiment or a pipeline defect. Original database relationships have not been
restored.

The pre-test export preserves the two distinct canonical transcripts, and all four raw
AssemblyAI artifacts were recovered from MinIO. A private inventory records 41 remaining
objects totaling 5,177,741,157 bytes. No database backup was found in the repository or
temporary directories searched, and PostgreSQL WAL archiving was disabled. A post-incident
dump preserves the remaining state but cannot restore the lost rows.

Further full-suite verification uses a disposable PostgreSQL **cluster** on port 55434,
explicit test-role connection URLs, and Redis DB 15. A separate database in the original
cluster is insufficient because schema tests alter cluster-global PostgreSQL roles.

## Follow-up: sentence selection — 2026-09-13

Three further arms were added to the probe (`sentences`, `sentences_split`, `spans_split`) and
run on the same two windows, three generations each. Sentences are partitioned locally at
terminal punctuation, speaker changes, or pauses of at least 800 ms; each start sentence lists
the end sentences that produce a 20–90-second clip. The model selects labels; the resolver
rebuilds the excerpt from stored words and the production validator and deduplicator still
decide acceptance. The split arms request metadata in a second call that sees only the
canonical transcript of each span. `spans_split` replaces the two labels with a single enum of
duration-valid pairs. Sixteen new negative controls cover partitioning, valid-end computation,
label refusals, metadata merging by position, context flags, and pair enumeration.

| Model | Arm | Generations meeting minimum | Accepted / proposed | Provider schema refusals |
| --- | --- | --- | --- | --- |
| 120B | sentences | 6 / 6 | 30 / 30 | 0 |
| 120B | sentences_split | 6 / 6 | 23 / 30 | 0 |
| 20B | sentences | 3 / 6 | 17 / 30 | 0 |
| 20B | sentences_split | 4 / 6 | 23 / 30 | 0 |
| 20B | spans_split | 3 / 6 | 15 / 15 in valid replies | 3 / 6 |

All sentence-arm local refusals were duration: the model picked an end outside the listed
range. The three `spans_split` refusals were identical English replies whose fifth label was
not in the enum; Groq validated the schema after generation and refused the whole reply.
Temperature 0 made generations near-repeats (one to three distinct proposal sets per arm and
language), so these counts overstate independent evidence.

Single-call metadata reported no context warnings and full context safety on every 120B
survivor, including openings that continue an earlier sentence. Split metadata produced
specific warnings on 19 of 23 survivors, sometimes referring to other clips shown in the same
request. Mechanical acceptance is no longer the blocker on 120B; context quality and
full-source behaviour remain unmeasured, and no production change is made here.

A later `pairs_split` arm lists the duration-valid pairs in the prompt but leaves the schema
field a plain string, so an unlisted pick is refused locally instead of refusing the reply.
The 20B run could not start: its daily token allowance was exhausted (200,000 limit,
196,944 used). On 120B it met the minimum in 6 of 6 generations, accepting 30 of 30 with no
local or provider refusals, using about 39,000 tokens. The five spans per reply did not
overlap. All three English generations returned the identical span set and Indonesian
returned three sets, so this is close to two independent samples, not six. Split metadata
warned on 24 of 30 survivors; the connector flag marked 1–2 per generation. Whether 20B
also stops choosing out-of-range ends with this field remains unmeasured.

The probe can also call OpenRouter (`--provider openrouter`), optionally sending the schema as
a forced tool call (`--tools`) with a completion-ceiling override (`--max-tokens`). Two free
NVIDIA models were run with `pairs_split`, tool calling, and an 8,000-token ceiling:

| Model | Generations meeting minimum | Accepted / proposed | Failed generations | Median seconds |
| --- | --- | --- | --- | --- |
| `nvidia/nemotron-3-super-120b-a12b:free` | 6 / 6 | 30 / 30 | 0 | 131 |
| `nvidia/nemotron-3.5-lightning:free` | 4 / 6 | 20 / 20 | 2 (no tool call returned) | 434 |

No local refusals occurred for either model. Unlike gpt-oss at temperature 0, each language
produced three distinct span sets, so these are closer to independent samples. Some replies
proposed overlapping spans, which production deduplication reduced (Lightning's second
Indonesian generation went from five to four). Super left context warnings empty on 26 of 30
survivors and often copied the opening sentence as its hook, so its self-reported context
safety is no more informative than gpt-oss's. Earlier attempts failed for probe reasons, not
model reasons: a 3,000-token ceiling truncated Lightning's metadata call before its tool call,
and Super's JSON-schema mode degenerated into whitespace after a quotation mark in the
transcript until it hit the ceiling. Tool calling avoided both.

Free OpenRouter models allow 20 requests per minute and 50 per day without at least $10 of
purchased credit (1,000 per day with it); each generation here costs two requests. Lightning's
free endpoint offers a 1,000,000-token context but no JSON-schema mode; Super's free endpoint
offers 262,144 tokens. Both free endpoints are served by NVIDIA under its trial terms, which
must be reviewed before real customer transcripts are sent.

**Full-length source, free tier, one pass.** `nvidia/nemotron-3-super-120b-a12b:free` was run
over every window of the 722,588-ms Indonesian transcript with `pairs_split`, tool calling, and
an 8,000-token ceiling: five windows of 427, 434, 400, 422, and 222 words, one generation each,
two requests per window. All five windows met the three-survivor minimum, accepting 25 of 25
proposals with no local or provider refusals. Deduplication removed one candidate inside the
last window, leaving 24 for the video; pooling all windows removed none, and four adjacent
pairs still overlap in time after pooled deduplication, which the reranker would have to
resolve. Durations ranged from 20.2 to 46.9 seconds, and the connector flag marked 7 of 24.
The pass cost 10 requests (of the 50 a day a credit-free OpenRouter account allows), 32,606
prompt tokens, and 33,294 completion tokens, at about two minutes per window. This answers
only mechanical acceptance across a whole source; no human has judged whether these 24 clips
are worth publishing.

**Whole transcript in one request.** The probe gained `--whole-transcript` (one window over
every word) and `--target-count`. The same Indonesian source was analysed as a single
16,000-ceiling request asking for 12 moments: 167 sentences, 2,507 duration-valid spans, about
30,442 prompt tokens across two requests. Eleven of twelve proposals were accepted, one refused
locally for duration, and none overlapped after deduplication, against four overlapping pairs
from the five-window pass. It cost 2 requests instead of 10 and took 165 seconds instead of
about ten minutes. The cost is coverage: eleven of twelve picks fall in sentences S0-S95 and
the twelfth at S148 of 167, leaving roughly the final third of the video unrepresented, whereas
windowing forces every part of the source to be considered. Six of eleven survivors opened on a
connector. A single request therefore trades guaranteed coverage for fewer calls and no
cross-window duplicates; neither shape has been judged for clip quality by a human.
