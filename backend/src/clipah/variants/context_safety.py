"""Deterministic context-safety rules over the authoritative transcript.

Every rule here answers the same question: does this boundary make the speaker appear to
have said something they did not? The rules read only the transcript — no model, no
network, no configuration — so their answers are reproducible and can be scored against
labeled fixtures in the evaluation harness.

A rule may only point at word IDs inside the span it was asked about, and may only suggest
a boundary that is a real word of the same transcript. Nothing here edits text or moves a
boundary; that is a member's decision, and the generator's to refuse.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from clipah.transcripts.models import TranscriptWord
from clipah.variants.models import (
    ContextWarning,
    ContextWarningSeverity,
    ContextWarningType,
)

#: Punctuation that ends a sentence, in every language Clipah transcribes.
TERMINAL_PUNCTUATION = frozenset({".", "!", "?", "…"})
QUESTION_PUNCTUATION = "?"

#: Negations whose removal inverts a claim. Indonesian and English are both first-class,
#: and code-switched speech is simply both vocabularies applied to the same words.
NEGATIONS = frozenset(
    {
        "not",
        "no",
        "never",
        "cannot",
        "dont",
        "doesnt",
        "didnt",
        "isnt",
        "arent",
        "wont",
        "tidak",
        "tak",
        "bukan",
        "belum",
        "jangan",
        "tanpa",
    }
)

#: References that mean nothing without the thing they refer back to.
REFERENCES = frozenset(
    {
        "it",
        "its",
        "they",
        "them",
        "their",
        "he",
        "she",
        "this",
        "that",
        "these",
        "those",
        "itu",
        "ini",
        "mereka",
        "dia",
        "beliau",
        "tersebut",
    }
)

#: Words that qualify the claim beside them; a cut that drops one changes the claim.
CAVEATS = frozenset(
    {
        "but",
        "however",
        "although",
        "though",
        "unless",
        "except",
        "only",
        "tapi",
        "tetapi",
        "namun",
        "kecuali",
        "meskipun",
        "walaupun",
        "hanya",
    }
)

#: Markers that attribute a statement to somebody other than the speaker.
ATTRIBUTIONS = frozenset(
    {
        "said",
        "says",
        "told",
        "according",
        "claimed",
        "reported",
        "kata",
        "katanya",
        "menurut",
        "ujar",
        "ujarnya",
        "bilang",
    }
)

#: Enumeration openers. A span holding one of these while the next continues outside it is
#: showing part of a list as if it were the whole of one.
ENUMERATIONS = (
    ("first", "second", "third", "fourth", "fifth"),
    ("pertama", "kedua", "ketiga", "keempat", "kelima"),
    ("one", "two", "three", "four", "five"),
    ("satu", "dua", "tiga", "empat", "lima"),
)

_SEVERITY: dict[ContextWarningType, ContextWarningSeverity] = {
    ContextWarningType.CUT_OFF_QUESTION: ContextWarningSeverity.BLOCKING,
    ContextWarningType.CUT_OFF_PAYOFF: ContextWarningSeverity.BLOCKING,
    ContextWarningType.MISSING_NEGATION: ContextWarningSeverity.BLOCKING,
    ContextWarningType.MISSING_ATTRIBUTION: ContextWarningSeverity.WARNING,
    ContextWarningType.UNSUPPORTED_REFERENCE: ContextWarningSeverity.WARNING,
    ContextWarningType.OMITTED_CAVEAT: ContextWarningSeverity.WARNING,
    ContextWarningType.INCOMPLETE_LIST: ContextWarningSeverity.WARNING,
    ContextWarningType.CLAIM_NEEDS_SOURCE: ContextWarningSeverity.INFO,
}


@dataclass(frozen=True, slots=True)
class _Span:
    """One resolved boundary, and the sentence that contains each of its ends."""

    words: tuple[TranscriptWord, ...]
    start: int
    end: int
    sentence_start: int
    sentence_end: int

    def word_id(self, position: int) -> str:
        """Name the word at one transcript position."""
        return self.words[position].word_id


def assess_context(
    *,
    words: Sequence[TranscriptWord],
    start_word_id: str,
    end_word_id: str,
) -> tuple[ContextWarning, ...]:
    """Report every warning the transcript itself can establish about one boundary."""
    span = _resolve(tuple(words), start_word_id=start_word_id, end_word_id=end_word_id)
    found = (
        _cut_off_question(span),
        _cut_off_payoff(span),
        _missing_negation(span),
        _unsupported_reference(span),
        _omitted_caveat(span),
        _missing_attribution(span),
        _incomplete_list(span),
    )
    return tuple(warning for warning in found if warning is not None)


def assess_context_with_proposals(
    *,
    words: Sequence[TranscriptWord],
    start_word_id: str,
    end_word_id: str,
    proposals: Sequence[Mapping[str, Any]],
) -> tuple[ContextWarning, ...]:
    """Merge validated provider proposals into what the rules already established.

    A proposal is evidence only once it resolves: its type must be one Clipah evaluates,
    its evidence must name real words inside the span, and any boundary it suggests must
    be a real word of the same transcript. Anything else is discarded rather than
    repaired, because a warning nobody can check is worse than a missing one — it teaches
    a member to dismiss the whole panel.
    """
    words = tuple(words)
    deterministic = assess_context(
        words=words, start_word_id=start_word_id, end_word_id=end_word_id
    )
    span = _resolve(words, start_word_id=start_word_id, end_word_id=end_word_id)
    inside = {span.word_id(position) for position in range(span.start, span.end + 1)}
    known = {word.word_id for word in words}

    merged = list(deterministic)
    seen = {(warning.type, warning.evidence_word_ids) for warning in deterministic}
    seen_types = {warning.type for warning in deterministic}
    for proposal in proposals:
        warning = _validated_proposal(proposal, inside=inside, known=known)
        if warning is None or warning.type in seen_types:
            continue
        if (warning.type, warning.evidence_word_ids) in seen:
            continue
        merged.append(warning)
        seen.add((warning.type, warning.evidence_word_ids))
        seen_types.add(warning.type)
    return tuple(merged)


def _validated_proposal(
    proposal: Mapping[str, Any], *, inside: set[str], known: set[str]
) -> ContextWarning | None:
    """Accept one proposal only when every claim it makes resolves against the transcript."""
    warning_type = proposal.get("type")
    evidence = proposal.get("evidence_word_ids")
    if not isinstance(warning_type, str) or warning_type not in set(ContextWarningType):
        return None
    if not isinstance(evidence, (list, tuple)) or not evidence:
        return None
    if not all(isinstance(word_id, str) and word_id in inside for word_id in evidence):
        return None
    suggestions = (proposal.get("suggested_start_word_id"), proposal.get("suggested_end_word_id"))
    if any(value is not None and value not in known for value in suggestions):
        return None
    start, end = suggestions
    return _warning(
        ContextWarningType(warning_type),
        evidence=tuple(evidence),
        suggested_start=start if isinstance(start, str) else None,
        suggested_end=end if isinstance(end, str) else None,
    )


def blocking(warnings: Sequence[ContextWarning]) -> tuple[ContextWarning, ...]:
    """Return only the warnings severe enough to refuse a Variant outright."""
    return tuple(
        warning for warning in warnings if warning.severity is ContextWarningSeverity.BLOCKING
    )


def _resolve(words: tuple[TranscriptWord, ...], *, start_word_id: str, end_word_id: str) -> _Span:
    """Bind two word IDs to real transcript positions, refusing anything unresolvable."""
    index = {word.word_id: position for position, word in enumerate(words)}
    start = index.get(start_word_id)
    end = index.get(end_word_id)
    if start is None or end is None:
        raise ValueError("context assessment requires two known word IDs")
    if end < start:
        raise ValueError("a context assessment span cannot end before it starts")
    return _Span(
        words=words,
        start=start,
        end=end,
        sentence_start=_sentence_start(words, start),
        sentence_end=_sentence_end(words, end),
    )


def _cut_off_question(span: _Span) -> ContextWarning | None:
    """Report a cut that asks a question whose answer falls outside it."""
    if _is_terminal(span.words[span.end]):
        return None
    if not _contains_question(span.words, span.end, span.sentence_end):
        return None
    return _warning(
        ContextWarningType.CUT_OFF_QUESTION,
        evidence=(span.word_id(span.end),),
        suggested_end=span.word_id(span.sentence_end),
    )


def _cut_off_payoff(span: _Span) -> ContextWarning | None:
    """Report a cut that stops mid-sentence with no question to explain it."""
    if _is_terminal(span.words[span.end]) or span.sentence_end == span.end:
        return None
    if _contains_question(span.words, span.end, span.sentence_end):
        return None
    return _warning(
        ContextWarningType.CUT_OFF_PAYOFF,
        evidence=(span.word_id(span.end),),
        suggested_end=span.word_id(span.sentence_end),
    )


def _missing_negation(span: _Span) -> ContextWarning | None:
    """Report a cut whose sentence was negated before the boundary excluded the negation."""
    position = _find(span.words, span.sentence_start, span.start - 1, NEGATIONS)
    if position is None:
        return None
    return _warning(
        ContextWarningType.MISSING_NEGATION,
        evidence=(span.word_id(span.start),),
        suggested_start=span.word_id(position),
    )


def _unsupported_reference(span: _Span) -> ContextWarning | None:
    """Report a cut opening on a reference whose antecedent it excludes."""
    if span.start == 0 or _normalized(span.words[span.start]) not in REFERENCES:
        return None
    return _warning(
        ContextWarningType.UNSUPPORTED_REFERENCE,
        evidence=(span.word_id(span.start),),
        suggested_start=span.word_id(_sentence_start(span.words, span.start - 1)),
    )


def _omitted_caveat(span: _Span) -> ContextWarning | None:
    """Report a cut that stops before the qualifier attached to its own claim."""
    if _is_terminal(span.words[span.end]) and span.sentence_end == span.end:
        return None
    position = _find(span.words, span.end + 1, span.sentence_end, CAVEATS)
    if position is None:
        return None
    return _warning(
        ContextWarningType.OMITTED_CAVEAT,
        evidence=(span.word_id(span.end),),
        suggested_end=span.word_id(span.sentence_end),
    )


def _missing_attribution(span: _Span) -> ContextWarning | None:
    """Report a cut that presents a reported claim as the speaker's own."""
    position = _find(span.words, span.sentence_start, span.start - 1, ATTRIBUTIONS)
    if position is None:
        return None
    return _warning(
        ContextWarningType.MISSING_ATTRIBUTION,
        evidence=(span.word_id(span.start),),
        suggested_start=span.word_id(position),
    )


def _incomplete_list(span: _Span) -> ContextWarning | None:
    """Report a cut showing one enumerated step as though it were the whole list."""
    for series in ENUMERATIONS:
        inside = _series_positions(span.words, span.start, span.end, series)
        if not inside:
            continue
        after = _series_positions(span.words, span.end + 1, len(span.words) - 1, series)
        highest_inside = max(series.index(_normalized(span.words[position])) for position in inside)
        continues = any(
            series.index(_normalized(span.words[position])) > highest_inside for position in after
        )
        if continues:
            return _warning(
                ContextWarningType.INCOMPLETE_LIST,
                evidence=tuple(span.word_id(position) for position in inside),
                suggested_end=span.word_id(_sentence_end(span.words, after[-1])),
            )
    return None


def _series_positions(
    words: tuple[TranscriptWord, ...], start: int, end: int, series: tuple[str, ...]
) -> tuple[int, ...]:
    """Find every position in a range whose word opens an item of one enumeration."""
    if start > end:
        return ()
    return tuple(
        position
        for position in range(max(start, 0), min(end, len(words) - 1) + 1)
        if _normalized(words[position]) in series
    )


def _find(
    words: tuple[TranscriptWord, ...], start: int, end: int, vocabulary: frozenset[str]
) -> int | None:
    """Return the first position in a range whose word belongs to one vocabulary."""
    if start > end:
        return None
    for position in range(max(start, 0), min(end, len(words) - 1) + 1):
        if _normalized(words[position]) in vocabulary:
            return position
    return None


def _contains_question(words: tuple[TranscriptWord, ...], start: int, end: int) -> bool:
    """Report whether the remainder of this sentence is a question."""
    return any(
        QUESTION_PUNCTUATION in words[position].punctuation for position in range(start, end + 1)
    )


def _sentence_start(words: tuple[TranscriptWord, ...], position: int) -> int:
    """Find the first word of the sentence containing one position."""
    cursor = position
    while cursor > 0 and not _is_terminal(words[cursor - 1]):
        cursor -= 1
    return cursor


def _sentence_end(words: tuple[TranscriptWord, ...], position: int) -> int:
    """Find the last word of the sentence containing one position."""
    cursor = position
    while cursor < len(words) - 1 and not _is_terminal(words[cursor]):
        cursor += 1
    return cursor


def _is_terminal(word: TranscriptWord) -> bool:
    """Report whether one word ends a sentence."""
    return any(mark in TERMINAL_PUNCTUATION for mark in word.punctuation)


def _normalized(word: TranscriptWord) -> str:
    """Compare speech without case, punctuation, or the apostrophes contractions carry."""
    return "".join(character for character in word.text.casefold() if character.isalnum())


def _warning(
    warning_type: ContextWarningType,
    *,
    evidence: tuple[str, ...],
    suggested_start: str | None = None,
    suggested_end: str | None = None,
) -> ContextWarning:
    """Build one warning at the severity its type always carries."""
    return ContextWarning(
        type=warning_type,
        severity=_SEVERITY[warning_type],
        evidence_word_ids=evidence,
        suggested_start_word_id=suggested_start,
        suggested_end_word_id=suggested_end,
    )
