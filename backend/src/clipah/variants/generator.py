"""Hook and duration variants, cut only where the transcript allows an honest boundary.

A Variant is a narrower reading of one Clip Candidate. It is built from sentence
boundaries the transcript already contains, never from a timestamp arithmetic that happens
to land mid-word, and it is offered only when the context rules find nothing blocking.

The generator returns nothing rather than something misleading. A 20-second target over a
candidate whose every 20-second window severs a payoff produces no Variant at all, which
is the truthful answer to "show me this at twenty seconds".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from clipah.transcripts.models import TranscriptWord
from clipah.variants.context_safety import assess_context_with_proposals, blocking
from clipah.variants.models import (
    SUPPORTED_TARGET_DURATIONS_MS,
    ContextWarning,
    HookStrategy,
    Platform,
)
from clipah.variants.packaging import PlatformPackaging, packaging_for

#: How far from its target a Variant may land and still be that target. A quarter is wide
#: enough to respect sentence boundaries and narrow enough that the number means something.
DURATION_TOLERANCE = 0.25


@dataclass(frozen=True, slots=True)
class ClipVariantDraft:
    """One proposed alternative cut, with the evidence a member decides on."""

    hook_strategy: HookStrategy
    platform: Platform
    target_duration_ms: int
    start_word_id: str
    end_word_id: str
    start_ms: int
    end_ms: int
    title: str
    rationale: str
    warnings: tuple[ContextWarning, ...]
    packaging: PlatformPackaging


@dataclass(frozen=True, slots=True)
class _Sentence:
    """One sentence of the candidate, as a pair of transcript positions."""

    start: int
    end: int


def generate_variants(
    *,
    words: Sequence[TranscriptWord],
    start_word_id: str,
    end_word_id: str,
    hook: str,
    platforms: Sequence[Platform],
    durations_ms: Sequence[int],
    proposals: Sequence[Mapping[str, object]] = (),
) -> tuple[ClipVariantDraft, ...]:
    """Offer every honest cut of this candidate at the requested lengths and platforms."""
    for target in durations_ms:
        if target not in SUPPORTED_TARGET_DURATIONS_MS:
            raise ValueError("a variant may only target a supported duration")

    resolved = tuple(words)
    span = _span(resolved, start_word_id=start_word_id, end_word_id=end_word_id)
    sentences = _sentences(resolved, span)
    if not sentences:
        return ()

    drafts: list[ClipVariantDraft] = []
    for target in durations_ms:
        for strategy in HookStrategy:
            opening = _opening(resolved, sentences, strategy)
            if opening is None:
                continue
            closing = _closing(resolved, sentences, opening=opening, target_ms=target)
            if closing is None:
                continue
            warnings = assess_context_with_proposals(
                words=resolved,
                start_word_id=resolved[sentences[opening].start].word_id,
                end_word_id=resolved[sentences[closing].end].word_id,
                proposals=proposals,
            )
            if blocking(warnings):
                continue
            drafts.extend(
                _draft(
                    resolved,
                    sentences=sentences,
                    opening=opening,
                    closing=closing,
                    strategy=strategy,
                    target_ms=target,
                    platform=platform,
                    hook=hook,
                    warnings=warnings,
                )
                for platform in platforms
            )
    return tuple(drafts)


def _draft(
    words: tuple[TranscriptWord, ...],
    *,
    sentences: tuple[_Sentence, ...],
    opening: int,
    closing: int,
    strategy: HookStrategy,
    target_ms: int,
    platform: Platform,
    hook: str,
    warnings: tuple[ContextWarning, ...],
) -> ClipVariantDraft:
    """Bind one accepted boundary to the platform it is being packaged for."""
    first = words[sentences[opening].start]
    last = words[sentences[closing].end]
    packaging = packaging_for(platform)
    return ClipVariantDraft(
        hook_strategy=strategy,
        platform=platform,
        target_duration_ms=target_ms,
        start_word_id=first.word_id,
        end_word_id=last.word_id,
        start_ms=first.start_ms,
        end_ms=last.end_ms,
        title=hook[: packaging.max_title_characters],
        rationale=_rationale(strategy, target_ms=target_ms, actual_ms=last.end_ms - first.start_ms),
        warnings=warnings,
        packaging=packaging,
    )


def _rationale(strategy: HookStrategy, *, target_ms: int, actual_ms: int) -> str:
    """State plainly why this cut was offered, in the terms a member is choosing on."""
    openings = {
        HookStrategy.COLD_OPEN: "Opens on the candidate's own first sentence",
        HookStrategy.QUESTION_FIRST: "Opens on the question this clip goes on to answer",
        HookStrategy.STATEMENT_FIRST: "Opens on the claim, with the setup trimmed",
    }
    return (
        f"{openings[strategy]}. Runs {round(actual_ms / 1_000)}s against a "
        f"{round(target_ms / 1_000)}s target, ending on a complete sentence."
    )


def _span(words: tuple[TranscriptWord, ...], *, start_word_id: str, end_word_id: str) -> _Sentence:
    """Resolve the candidate's own boundary, refusing anything the transcript lacks."""
    index = {word.word_id: position for position, word in enumerate(words)}
    start = index.get(start_word_id)
    end = index.get(end_word_id)
    if start is None or end is None:
        raise ValueError("variant generation requires two known word IDs")
    if end < start:
        raise ValueError("a candidate span cannot end before it starts")
    return _Sentence(start=start, end=end)


def _sentences(words: tuple[TranscriptWord, ...], span: _Sentence) -> tuple[_Sentence, ...]:
    """Split the candidate into the complete sentences it actually contains."""
    found: list[_Sentence] = []
    cursor = span.start
    for position in range(span.start, span.end + 1):
        if _is_terminal(words[position]):
            found.append(_Sentence(start=cursor, end=position))
            cursor = position + 1
    return tuple(found)


def _opening(
    words: tuple[TranscriptWord, ...], sentences: tuple[_Sentence, ...], strategy: HookStrategy
) -> int | None:
    """Choose which sentence one strategy would open on, or report that it cannot."""
    if strategy is HookStrategy.COLD_OPEN:
        return 0
    if strategy is HookStrategy.QUESTION_FIRST:
        return next(
            (
                position
                for position, sentence in enumerate(sentences)
                if "?" in words[sentence.end].punctuation
            ),
            None,
        )
    # A statement-first cut trims the setup, so it needs a sentence to trim and one to keep.
    return 1 if len(sentences) > 1 else None


def _closing(
    words: tuple[TranscriptWord, ...],
    sentences: tuple[_Sentence, ...],
    *,
    opening: int,
    target_ms: int,
) -> int | None:
    """Find the sentence end that lands closest to the target inside tolerance."""
    start_ms = words[sentences[opening].start].start_ms
    best: tuple[int, int] | None = None
    for position in range(opening, len(sentences)):
        duration = words[sentences[position].end].end_ms - start_ms
        distance = abs(duration - target_ms)
        if distance > target_ms * DURATION_TOLERANCE:
            continue
        if best is None or distance < best[1]:
            best = (position, distance)
    return None if best is None else best[0]


def _is_terminal(word: TranscriptWord) -> bool:
    """Report whether one word ends a sentence."""
    return any(mark in {".", "!", "?", "…"} for mark in word.punctuation)
