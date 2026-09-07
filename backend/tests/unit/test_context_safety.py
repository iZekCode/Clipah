"""Labeled context-safety contracts in Indonesian, English, and code-switched speech.

Every case here is a boundary a human labeled as honest or misleading, written the way the
transcript stores speech: canonical words, trailing punctuation split off, and word IDs as
the only way to name a position. A rule that only works in English would pass half of this
file, which is the point of writing all three languages into it.
"""

from __future__ import annotations

import pytest

from clipah.transcripts.models import TranscriptWord
from clipah.variants.assessor import (
    CONTEXT_ASSESS_OPERATION,
    RATE_LIMITED_CODE,
    REJECTED_CODE,
    UNAVAILABLE_CODE,
    ContextProviderRetryableError,
    ContextProviderTerminalError,
    GroqContextSafetyAssessor,
    context_warning_json_schema,
)
from clipah.variants.context_safety import assess_context, assess_context_with_proposals
from clipah.variants.models import (
    ContextWarning,
    ContextWarningSeverity,
    ContextWarningType,
)

#: How long each fabricated word occupies, so a span's duration is predictable.
WORD_MS = 500


def _words(text: str) -> tuple[TranscriptWord, ...]:
    """Build a transcript from plain speech, splitting trailing punctuation as ingest does."""
    words: list[TranscriptWord] = []
    for index, token in enumerate(text.split(), start=1):
        stripped = token.rstrip(".,!?;:")
        words.append(
            TranscriptWord(
                word_id=f"w{index:06d}",
                text=stripped,
                punctuation=token[len(stripped) :],
                start_ms=(index - 1) * WORD_MS,
                end_ms=index * WORD_MS,
                confidence=0.9,
                speaker="A",
            )
        )
    return tuple(words)


def _types(
    text: str, *, start: int, end: int, speakers: dict[int, str] | None = None
) -> set[ContextWarningType]:
    """Assess one word-numbered span and report only which warning types it raised."""
    words = _words(text)
    if speakers is not None:
        words = tuple(
            TranscriptWord(
                word_id=word.word_id,
                text=word.text,
                punctuation=word.punctuation,
                start_ms=word.start_ms,
                end_ms=word.end_ms,
                confidence=word.confidence,
                speaker=speakers.get(position, word.speaker),
            )
            for position, word in enumerate(words, start=1)
        )
    return {
        warning.type
        for warning in assess_context(
            words=words,
            start_word_id=f"w{start:06d}",
            end_word_id=f"w{end:06d}",
        )
    }


# Each case is (label, speech, start word, end word, the type a human says it must raise).
CUT_OFF_QUESTION_CASES = (
    (
        "english",
        "So here is the thing. What happens when the funding runs out? Nothing good.",
        5,
        11,
    ),
    (
        "indonesian",
        "Jadi begini ceritanya. Apa yang terjadi kalau dananya habis? Tidak ada yang bagus.",
        4,
        8,
    ),
    (
        "code_switched",
        "Oke jadi begini. So what happens kalau dananya habis? Ya repot sekali.",
        4,
        8,
    ),
)

MISSING_NEGATION_CASES = (
    ("english", "We do not recommend that approach for new teams at all.", 4, 10),
    ("indonesian", "Kami tidak menyarankan pendekatan itu untuk tim baru sama sekali.", 3, 9),
    ("code_switched", "Kami tidak recommend approach itu untuk tim yang masih baru.", 3, 9),
)

UNSUPPORTED_REFERENCE_CASES = (
    ("english", "The migration finished last night. It broke every downstream report.", 6, 10),
    ("indonesian", "Migrasinya selesai tadi malam. Itu merusak semua laporan yang ada.", 5, 9),
    ("code_switched", "Migrasinya finished tadi malam. It broke semua downstream report.", 5, 9),
)

OMITTED_CAVEAT_CASES = (
    ("english", "The numbers doubled, but only because we changed how we counted.", 1, 3),
    ("indonesian", "Angkanya naik dua kali, tapi hanya karena cara hitungnya berubah.", 1, 4),
    ("code_switched", "Angkanya doubled dua kali, but hanya karena cara hitungnya berubah.", 1, 4),
)

MISSING_ATTRIBUTION_CASES = (
    ("english", "The auditor said the whole quarter was misreported to the board.", 4, 11),
    (
        "indonesian",
        "Menurut auditor seluruh kuartal itu dilaporkan salah kepada dewan direksi.",
        3,
        10,
    ),
    ("code_switched", "Menurut auditor the whole quarter itu dilaporkan salah ke board.", 3, 10),
)

INCOMPLETE_LIST_CASES = (
    ("english", "There are three steps. First you measure. Second you cut. Third you check.", 5, 8),
    (
        "indonesian",
        "Ada tiga langkah. Pertama kamu ukur. Kedua kamu potong. Ketiga kamu cek.",
        4,
        7,
    ),
    (
        "code_switched",
        "Ada tiga steps. Pertama kamu measure. Kedua kamu cut. Ketiga kamu check.",
        4,
        7,
    ),
)

SAFE_CASES = (
    ("english", "We shipped the feature on Friday. The whole team stayed late for it.", 1, 6),
    ("indonesian", "Kami merilis fiturnya hari Jumat. Seluruh tim lembur untuk itu semua.", 1, 5),
    ("code_switched", "Kami ship fiturnya hari Jumat. Seluruh tim lembur untuk itu semua.", 1, 5),
)


def _severity(
    warnings: tuple[ContextWarning, ...], warning_type: ContextWarningType
) -> ContextWarningSeverity:
    """Read the severity of the one warning of this type the assessment raised."""
    matches = [warning for warning in warnings if warning.type is warning_type]
    assert len(matches) == 1
    return matches[0].severity


@pytest.mark.unit
@pytest.mark.parametrize(("label", "speech", "start", "end"), CUT_OFF_QUESTION_CASES)
def test_a_span_ending_inside_a_question_is_reported(
    label: str, speech: str, start: int, end: int
) -> None:
    """A clip that asks and never answers misrepresents what the speaker actually said."""
    del label
    assert ContextWarningType.CUT_OFF_QUESTION in _types(speech, start=start, end=end)


@pytest.mark.unit
@pytest.mark.parametrize(("label", "speech", "start", "end"), MISSING_NEGATION_CASES)
def test_a_span_that_drops_its_negation_is_reported(
    label: str, speech: str, start: int, end: int
) -> None:
    """Cutting away "not" inverts the claim, which is the most damaging edit there is."""
    del label
    assert ContextWarningType.MISSING_NEGATION in _types(speech, start=start, end=end)


@pytest.mark.unit
@pytest.mark.parametrize(("label", "speech", "start", "end"), UNSUPPORTED_REFERENCE_CASES)
def test_a_span_opening_on_a_pronoun_without_its_antecedent_is_reported(
    label: str, speech: str, start: int, end: int
) -> None:
    """ "It broke everything" means nothing when what broke was cut away."""
    del label
    assert ContextWarningType.UNSUPPORTED_REFERENCE in _types(speech, start=start, end=end)


@pytest.mark.unit
@pytest.mark.parametrize(("label", "speech", "start", "end"), OMITTED_CAVEAT_CASES)
def test_a_span_that_stops_before_its_caveat_is_reported(
    label: str, speech: str, start: int, end: int
) -> None:
    """A qualified claim cut before its qualifier is a different claim."""
    del label
    assert ContextWarningType.OMITTED_CAVEAT in _types(speech, start=start, end=end)


@pytest.mark.unit
@pytest.mark.parametrize(("label", "speech", "start", "end"), MISSING_ATTRIBUTION_CASES)
def test_a_span_that_drops_who_said_it_is_reported(
    label: str, speech: str, start: int, end: int
) -> None:
    """A reported claim without its source reads as the speaker's own assertion."""
    del label
    assert ContextWarningType.MISSING_ATTRIBUTION in _types(speech, start=start, end=end)


@pytest.mark.unit
@pytest.mark.parametrize(("label", "speech", "start", "end"), INCOMPLETE_LIST_CASES)
def test_a_span_cut_inside_an_enumeration_is_reported(
    label: str, speech: str, start: int, end: int
) -> None:
    """Three steps shown as one is advice nobody can follow."""
    del label
    assert ContextWarningType.INCOMPLETE_LIST in _types(speech, start=start, end=end)


@pytest.mark.unit
@pytest.mark.parametrize(("label", "speech", "start", "end"), SAFE_CASES)
def test_a_complete_thought_raises_nothing(label: str, speech: str, start: int, end: int) -> None:
    """A rule that fires on an honest cut is worse than no rule, because it trains dismissal."""
    del label
    assert _types(speech, start=start, end=end) == set()


@pytest.mark.unit
def test_a_warning_evidences_itself_inside_the_span_and_suggests_a_real_boundary() -> None:
    """A warning nobody can check is an opinion; evidence and a fix make it actionable."""
    speech = "So here is the thing. What happens when the funding runs out? Nothing good."
    words = _words(speech)

    warnings = assess_context(words=words, start_word_id="w000005", end_word_id="w000011")

    known = {word.word_id for word in words}
    span = {f"w{position:06d}" for position in range(5, 12)}
    assert warnings
    for warning in warnings:
        assert warning.evidence_word_ids
        assert set(warning.evidence_word_ids) <= span
        for boundary in (warning.suggested_start_word_id, warning.suggested_end_word_id):
            assert boundary is None or boundary in known


@pytest.mark.unit
def test_a_cut_off_question_blocks_while_a_missing_caveat_only_warns() -> None:
    """Severity is what lets a variant be refused without refusing every imperfect cut.

    A cut can raise both at once — stopping before "but" is also stopping mid-sentence —
    so severity is asserted per warning rather than over the span, which is how the
    generator reads it too.
    """
    question = assess_context(
        words=_words("So here is the thing. What happens when the funding runs out? Nothing good."),
        start_word_id="w000005",
        end_word_id="w000011",
    )
    caveat = assess_context(
        words=_words("The numbers doubled, but only because we changed how we counted."),
        start_word_id="w000001",
        end_word_id="w000003",
    )

    assert _severity(question, ContextWarningType.CUT_OFF_QUESTION) is (
        ContextWarningSeverity.BLOCKING
    )
    assert _severity(caveat, ContextWarningType.OMITTED_CAVEAT) is ContextWarningSeverity.WARNING


@pytest.mark.unit
def test_an_attribution_from_another_speaker_is_not_read_as_the_clips_own() -> None:
    """Attribution follows the turn, so a quote lifted from another speaker is flagged."""
    speech = "The auditor said the whole quarter was misreported to the board."
    speakers = {1: "A", 2: "A", 3: "A", 4: "B", 5: "B", 6: "B", 7: "B", 8: "B", 9: "B"}

    assert ContextWarningType.MISSING_ATTRIBUTION in _types(
        speech, start=4, end=11, speakers=speakers
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("start", "end"),
    (("w000404", "w000005"), ("w000001", "w000404")),
)
def test_a_span_naming_an_unknown_word_is_refused_rather_than_guessed(start: str, end: str) -> None:
    """A boundary Clipah cannot resolve is not a boundary it may assess."""
    with pytest.raises(ValueError):
        assess_context(
            words=_words("We shipped the feature on Friday."),
            start_word_id=start,
            end_word_id=end,
        )


@pytest.mark.unit
def test_a_reversed_span_is_refused() -> None:
    """An end before its start cannot describe any real cut."""
    with pytest.raises(ValueError):
        assess_context(
            words=_words("We shipped the feature on Friday."),
            start_word_id="w000005",
            end_word_id="w000002",
        )


def _proposal(**overrides: object) -> dict[str, object]:
    """Build one provider-proposed warning over the standing question fixture."""
    values: dict[str, object] = {
        "type": "claim_needs_source",
        "evidence_word_ids": ["w000006", "w000007"],
        "suggested_start_word_id": None,
        "suggested_end_word_id": None,
    }
    values.update(overrides)
    return values


QUESTION_SPEECH = "So here is the thing. What happens when the funding runs out? Nothing good."


def _assessed(*proposals: dict[str, object]) -> tuple[ContextWarning, ...]:
    """Merge provider proposals into the deterministic assessment of one fixed span."""
    return assess_context_with_proposals(
        words=_words(QUESTION_SPEECH),
        start_word_id="w000005",
        end_word_id="w000011",
        proposals=proposals,
    )


@pytest.mark.unit
def test_a_valid_provider_proposal_joins_the_deterministic_warnings() -> None:
    """A model can see what rules cannot, so a well-formed proposal is kept."""
    warnings = _assessed(_proposal())

    types = {warning.type for warning in warnings}
    assert ContextWarningType.CLAIM_NEEDS_SOURCE in types
    assert ContextWarningType.CUT_OFF_QUESTION in types


@pytest.mark.unit
@pytest.mark.parametrize(
    "proposal",
    (
        _proposal(evidence_word_ids=["w000404"]),
        _proposal(evidence_word_ids=["w000001"]),
        _proposal(evidence_word_ids=[]),
        _proposal(type="vibes_are_off"),
        _proposal(suggested_end_word_id="w000404"),
        _proposal(evidence_word_ids="w000006"),
    ),
)
def test_a_proposal_that_does_not_resolve_is_discarded_rather_than_repaired(
    proposal: dict[str, object],
) -> None:
    """A model that invents a word, a range, or a type must not reach a member's screen."""
    warnings = _assessed(proposal)

    assert ContextWarningType.CLAIM_NEEDS_SOURCE not in {warning.type for warning in warnings}
    assert ContextWarningType.CUT_OFF_QUESTION in {warning.type for warning in warnings}


@pytest.mark.unit
def test_a_proposal_repeating_a_deterministic_finding_is_not_shown_twice() -> None:
    """Two engines agreeing is one observation, not two things for a member to read."""
    warnings = _assessed(
        _proposal(type="cut_off_question", evidence_word_ids=["w000011"]),
    )

    assert len([w for w in warnings if w.type is ContextWarningType.CUT_OFF_QUESTION]) == 1


@pytest.mark.unit
def test_an_absent_provider_still_returns_what_the_rules_established() -> None:
    """Generated assessment is optional; the deterministic floor never is."""
    assert _assessed() == assess_context(
        words=_words(QUESTION_SPEECH),
        start_word_id="w000005",
        end_word_id="w000011",
    )


class _Completions:
    """Answer one chat completion with fixed content, or a chosen failure."""

    def __init__(
        self, *, content: str = '{"warnings": []}', error: Exception | None = None
    ) -> None:
        """Bind the single answer or failure this stub always produces."""
        self.content = content
        self.error = error
        self.requests: list[dict[str, object]] = []

    def create(self, **request: object) -> object:
        """Record the request, then answer exactly as configured."""
        self.requests.append(request)
        if self.error is not None:
            raise self.error

        class _Message:
            content = self.content

        class _Choice:
            message = _Message()

        class _Response:
            id = "groq-request-1"
            choices = (_Choice(),)

        return _Response()


class _RateLimitError(Exception):
    """Stand in for the provider SDK's rate-limit exception by name."""


class _APIConnectionError(Exception):
    """Stand in for the provider SDK's connection exception by name."""


class _BadRequestError(Exception):
    """Stand in for the provider SDK's rejection exception by name."""


def _assessor(**overrides: object) -> GroqContextSafetyAssessor:
    """Build the adapter over a stubbed client and a clock that never really sleeps."""
    completions = overrides.pop("completions", _Completions())
    return GroqContextSafetyAssessor(
        model="openai/gpt-oss-20b",
        completions=completions,  # type: ignore[arg-type]
        clock=lambda: 0.0,
        sleep=lambda _: None,
        **overrides,  # type: ignore[arg-type]
    )


@pytest.mark.unit
def test_the_assessor_returns_proposals_and_the_call_that_produced_them() -> None:
    """Usage recording needs the same evidence for assessment as for extraction."""
    completions = _Completions(
        content='{"warnings": [{"type": "claim_needs_source", "evidence_word_ids": ["w000006"]}]}'
    )

    result = _assessor(completions=completions).assess(
        words=_words(QUESTION_SPEECH), start_word_id="w000005", end_word_id="w000011"
    )

    assert result.proposals == ({"type": "claim_needs_source", "evidence_word_ids": ["w000006"]},)
    assert result.call.operation == CONTEXT_ASSESS_OPERATION
    assert result.call.model == "openai/gpt-oss-20b"
    assert result.call.request_id == "groq-request-1"


@pytest.mark.unit
def test_the_assessor_shows_the_model_word_ids_and_never_asks_for_a_timestamp() -> None:
    """A model that cannot see a timestamp cannot invent one."""
    completions = _Completions()

    _assessor(completions=completions).assess(
        words=_words(QUESTION_SPEECH), start_word_id="w000005", end_word_id="w000011"
    )

    prompt = str(completions.requests[0]["messages"])
    assert "w000005" in prompt
    assert "start_ms" not in prompt


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "expected", "code"),
    (
        (_RateLimitError("slow down"), ContextProviderRetryableError, RATE_LIMITED_CODE),
        (_APIConnectionError("no route"), ContextProviderRetryableError, UNAVAILABLE_CODE),
        (_BadRequestError("nope"), ContextProviderTerminalError, REJECTED_CODE),
    ),
)
def test_provider_failures_become_fixed_codes_carrying_no_provider_text(
    error: Exception, expected: type[Exception], code: str
) -> None:
    """A failure a member might see must never carry the provider's own words."""
    assessor = _assessor(completions=_Completions(error=error))

    with pytest.raises(expected) as raised:
        assessor.assess(
            words=_words(QUESTION_SPEECH), start_word_id="w000005", end_word_id="w000011"
        )

    assert str(raised.value) == code
    assert "slow down" not in str(raised.value)


@pytest.mark.unit
@pytest.mark.parametrize("content", ("not json at all", '["warnings"]', '{"warnings": "none"}'))
def test_an_unreadable_answer_is_terminal_rather_than_guessed_at(content: str) -> None:
    """An answer Clipah cannot parse proposes nothing; it does not propose something else."""
    assessor = _assessor(completions=_Completions(content=content))

    with pytest.raises(ContextProviderTerminalError):
        assessor.assess(
            words=_words(QUESTION_SPEECH), start_word_id="w000005", end_word_id="w000011"
        )


@pytest.mark.unit
def test_the_answer_schema_offers_only_types_clipah_can_evaluate() -> None:
    """A model cannot report a warning the evaluation harness has no way to score."""
    schema = context_warning_json_schema()

    offered = schema["properties"]["warnings"]["items"]["properties"]["type"]["enum"]
    assert set(offered) == {value.value for value in ContextWarningType}
