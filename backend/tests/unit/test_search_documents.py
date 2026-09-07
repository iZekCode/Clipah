"""Unit contracts for the pure parts of the content library.

Everything here decides what a document *means* before any database is involved: how a
provider's language tag is read, how a name is folded for a member who half-remembers it,
how a transcript is cut into turns, and how a highlighted fragment is described without
markup.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from clipah.models import PublishingRolePolicy, WorkspaceRole
from clipah.search.indexer import MAX_SEGMENT_WORDS, _headline, _segments, normalize_text
from clipah.search.models import (
    ExportState,
    IndexedDocument,
    SearchEntityType,
    SearchLanguage,
    SearchQuery,
)
from clipah.search.use_cases import SearchQueryError, _fragments, search_content
from clipah.workspaces.models import WorkspaceAccess

WORD_MS = 1_000
_ACCESS = WorkspaceAccess(
    workspace_id=UUID("11111111-1111-5111-8111-111111111111"),
    user_id=UUID("22222222-2222-5222-8222-222222222222"),
    role=WorkspaceRole.OWNER,
    publishing_role_policy=PublishingRolePolicy.OWNER_ADMIN_EDITOR,
)


class _Transcript:
    """The two transcript fields the segmenter actually reads."""

    def __init__(self, words: list[dict[str, Any]], speaker_segments: list[Any]) -> None:
        """Hold one provider's words and whatever it claimed about who spoke them."""
        self.words = words
        self.speaker_segments = speaker_segments


def _words(count: int, *, speaker: str | None = "SPEAKER_00") -> list[dict[str, Any]]:
    """Build one run of transcript words with contiguous millisecond bounds."""
    return [
        {
            "word_id": f"w{index:06d}",
            "text": f"kata{index}",
            "start_ms": index * WORD_MS,
            "end_ms": (index + 1) * WORD_MS,
            "speaker": speaker,
        }
        for index in range(count)
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tag", "expected"),
    (
        ("id", SearchLanguage.INDONESIAN),
        ("EN-us", SearchLanguage.ENGLISH),
        ("en_GB", SearchLanguage.ENGLISH),
        ("fr", SearchLanguage.OTHER),
        (None, SearchLanguage.OTHER),
        ("", SearchLanguage.OTHER),
    ),
)
def test_a_provider_language_tag_resolves_to_a_stemmer_this_product_supports(
    tag: str | None, expected: SearchLanguage
) -> None:
    """Guessing a stemmer for a language nobody tested would corrupt its index quietly."""
    assert SearchLanguage.parse(tag) is expected


@pytest.mark.unit
def test_a_language_without_a_stemmer_is_indexed_by_its_literal_words() -> None:
    """An honest literal index beats a wrong stemmer applied confidently."""
    assert SearchLanguage.OTHER.text_search_config == "simple"
    assert SearchLanguage.INDONESIAN.text_search_config == "indonesian"
    assert SearchLanguage.ENGLISH.text_search_config == "english"


@pytest.mark.unit
def test_a_documents_identifier_is_a_function_of_what_it_describes() -> None:
    """A rebuild must produce the same rows, not a new set of them under new keys."""
    fields: dict[str, Any] = {
        "workspace_id": uuid4(),
        "project_id": uuid4(),
        "entity_type": SearchEntityType.TRANSCRIPT,
        "entity_id": uuid4(),
        "anchor_id": uuid4(),
        "segment_ordinal": 3,
        "title": "Bagian tiga",
        "body": "Bagian tiga dari sesi ini",
        "speaker": "SPEAKER_00",
        "topics": (),
        "tags": (),
        "language": SearchLanguage.INDONESIAN,
        "start_ms": 0,
        "end_ms": 1_000,
        "export_state": ExportState.NOT_EXPORTED,
        "source_created_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    first = IndexedDocument(**fields)
    second = IndexedDocument(**{**fields, "title": "Judul lain", "body": "Isi lain"})
    other_segment = IndexedDocument(**{**fields, "segment_ordinal": 4})

    assert first.document_id == second.document_id
    assert first.document_id != other_segment.document_id


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("Café  Kopi — Sesi #2", "cafe kopi sesi 2"),
        ("RAPAT Produk", "rapat produk"),
        ("  spasi   berlebih  ", "spasi berlebih"),
    ),
)
def test_a_name_folds_to_the_form_a_member_will_type_months_later(name: str, expected: str) -> None:
    """Nobody reproduces the accents and punctuation of a name they typed once."""
    assert normalize_text(name) == expected


@pytest.mark.unit
def test_a_turn_is_named_by_its_own_opening_words() -> None:
    """A heading built from an identifier tells a reader nothing about the moment."""
    short = "Formulir pendaftaran itu batasnya"
    long_turn = " ".join(["pendaftaran"] * 20)

    assert _headline(short) == short
    assert len(_headline(long_turn)) <= 80
    assert _headline(long_turn).endswith("…")


@pytest.mark.unit
def test_a_transcript_is_cut_at_the_speaker_turns_the_provider_found() -> None:
    """A result should open where one person started talking, not where a page did."""
    words = _words(6)
    transcript = _Transcript(
        words,
        [
            {"start_word_id": "w000000", "end_word_id": "w000002"},
            {"start_word_id": "w000003", "end_word_id": "w000005"},
        ],
    )

    segments = _segments(transcript)  # type: ignore[arg-type]

    assert [segment.start_ms for segment in segments] == [0, 3 * WORD_MS]
    assert [segment.end_ms for segment in segments] == [3 * WORD_MS, 6 * WORD_MS]
    assert all(segment.speaker == "SPEAKER_00" for segment in segments)


@pytest.mark.unit
@pytest.mark.parametrize(
    "speaker_segments",
    (
        [],
        ["not a segment"],
        [{"start_word_id": "missing", "end_word_id": "w000002"}],
        [{"start_word_id": "w000002", "end_word_id": "w000000"}],
    ),
)
def test_a_transcript_the_provider_could_not_divide_is_still_indexed(
    speaker_segments: list[Any],
) -> None:
    """A transcript nobody can open at a timecode is a transcript nobody can use."""
    transcript = _Transcript(_words(4), speaker_segments)

    segments = _segments(transcript)  # type: ignore[arg-type]

    assert len(segments) == 1
    assert segments[0].start_ms == 0
    assert segments[0].end_ms == 4 * WORD_MS


@pytest.mark.unit
def test_a_turn_longer_than_a_reader_will_sit_through_becomes_several() -> None:
    """One result should be a moment, not an hour of somebody talking."""
    transcript = _Transcript(_words(MAX_SEGMENT_WORDS * 2 + 5), [])

    segments = _segments(transcript)  # type: ignore[arg-type]

    assert len(segments) == 3
    assert [len(segment.text.split()) for segment in segments] == [
        MAX_SEGMENT_WORDS,
        MAX_SEGMENT_WORDS,
        5,
    ]


@pytest.mark.unit
def test_a_run_two_people_share_is_attributed_to_neither() -> None:
    """Naming one of two speakers would be a claim the transcript never made."""
    words = _words(2, speaker="SPEAKER_00")
    words[1]["speaker"] = "SPEAKER_01"

    segments = _segments(_Transcript(words, []))  # type: ignore[arg-type]

    assert segments[0].speaker is None


@pytest.mark.unit
def test_an_empty_transcript_produces_no_documents() -> None:
    """An empty result in a library is worse than no result at all."""
    assert _segments(_Transcript([], [])) == []  # type: ignore[arg-type]


@pytest.mark.unit
def test_a_fragment_says_what_matched_without_saying_how_it_should_look() -> None:
    """The server marks a match; only the client decides what a highlight looks like."""
    fragments = _fragments("kata \x02cocok\x03 lalu \x02lagi\x03")

    assert [(fragment.text, fragment.highlighted) for fragment in fragments] == [
        ("kata ", False),
        ("cocok", True),
        (" lalu ", False),
        ("lagi", True),
    ]


@pytest.mark.unit
def test_a_headline_with_nothing_marked_is_one_plain_fragment() -> None:
    """A trigram match highlights nothing, and that is an answer rather than a failure."""
    fragments = _fragments("Rapat Produk")

    assert [(fragment.text, fragment.highlighted) for fragment in fragments] == [
        ("Rapat Produk", False)
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "query",
    (
        SearchQuery(text="   "),
        SearchQuery(text="x" * 201),
        SearchQuery(text="rapat", limit=0),
        SearchQuery(text="rapat", limit=51),
    ),
)
def test_a_question_that_cannot_be_answered_as_asked_is_refused_before_any_read(
    query: SearchQuery,
) -> None:
    """A search that reaches the database to be rejected has already cost something."""
    with pytest.raises(SearchQueryError):
        search_content(None, access=_ACCESS, query=query)  # type: ignore[arg-type]
