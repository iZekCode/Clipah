"""Contracts for the strict B-roll beat schema and its local resolution to word IDs.

A planning model may say what a viewer should see and where in the dialogue it belongs. It
may not say when: every beat is keyed to authoritative transcript word IDs, and the
milliseconds are resolved here from the transcript the Project actually holds.
"""

from __future__ import annotations

from typing import Any

import pytest

from clipah.broll.models import (
    BeatProtection,
    BrollCoverage,
    CandidateSpan,
    VisualIntent,
)
from clipah.broll.planner import (
    INVALID_CODE,
    OUTSIDE_CANDIDATE_CODE,
    RANGE_REVERSED_CODE,
    RATE_LIMITED_CODE,
    REJECTED_CODE,
    SCHEMA_INVALID_CODE,
    UNAVAILABLE_CODE,
    UNKNOWN_WORD_CODE,
    BeatValidationError,
    BrollPlanner,
    BrollProviderRetryableError,
    BrollProviderTerminalError,
    FakeBrollProvider,
    GroqBrollPlanner,
    ProposalResult,
    beat_json_schema,
    validate_beat,
)
from clipah.highlights.provider import ProviderCall
from clipah.transcripts.models import TranscriptResult, TranscriptWord

WORD_MS = 500
SENTENCE = ("We", "cut", "the", "signup", "form", "and", "activation", "doubled")


def _words(count: int = 120) -> tuple[TranscriptWord, ...]:
    """Lay one word every 500 ms so a resolved beat's milliseconds are exact."""
    return tuple(
        TranscriptWord(
            word_id=f"w{index + 1:06d}",
            text=SENTENCE[index % len(SENTENCE)],
            punctuation="." if index % len(SENTENCE) == len(SENTENCE) - 1 else "",
            start_ms=index * WORD_MS,
            end_ms=index * WORD_MS + WORD_MS,
            confidence=0.9,
            speaker="A",
        )
        for index in range(count)
    )


WORDS = _words()
WORDS_BY_ID = {word.word_id: word for word in WORDS}
CANDIDATE = CandidateSpan(
    start_word_id=WORDS[0].word_id,
    end_word_id=WORDS[119].word_id,
    start_ms=0,
    end_ms=60_000,
)


def _transcript() -> TranscriptResult:
    """Wrap the words as the sole authority a planner's beats are checked against."""
    return TranscriptResult(
        provider="assemblyai",
        provider_version="1.0.0",
        model="universal-3-pro",
        language="en",
        full_text=" ".join(word.text for word in WORDS),
        words=WORDS,
        speaker_segments=(),
        utterances=(),
        duration_ms=WORDS[-1].end_ms,
        raw_result={},
    )


def _intent_payload(**overrides: Any) -> dict[str, Any]:
    """Build one schema-valid intent, overriding only the field under test."""
    payload: dict[str, Any] = {
        "subject": "a shortened signup form",
        "action": "a hand deleting form fields",
        "setting": "a laptop screen on a desk",
        "mood": "focused",
        "search_terms_id": ["formulir pendaftaran", "layar laptop"],
        "search_terms_en": ["signup form", "laptop screen"],
        "portrait_suitable": True,
        "exclusions": ["stock office handshake"],
        "factual_risk_flags": [],
        "confidence": 0.8,
    }
    payload.update(overrides)
    return payload


def _payload(**overrides: Any) -> dict[str, Any]:
    """Build one schema-valid beat covering words 20 through 39."""
    payload: dict[str, Any] = {
        "start_word_id": WORDS[20].word_id,
        "end_word_id": WORDS[39].word_id,
        "placement_reason": "The sentence names an object the viewer cannot see",
        "protection": None,
        "intent": _intent_payload(),
    }
    payload.update(overrides)
    return payload


def _call() -> ProviderCall:
    """Describe one provider call so a fake result is shaped like a real one."""
    return ProviderCall(
        provider="fake",
        operation="broll_plan",
        model="fake",
        request_id="req-1",
        latency_ms=1,
        input_units=10,
        output_units=5,
        prompt_version="broll/plan/1",
        schema_version="broll-beat/1",
    )


@pytest.mark.unit
def test_a_valid_beat_resolves_its_milliseconds_from_the_transcript() -> None:
    """The transcript, never the model, decides when a beat starts and ends."""
    beat = validate_beat(_payload(), words_by_id=WORDS_BY_ID, candidate=CANDIDATE)

    assert (beat.start_ms, beat.end_ms) == (10_000, 20_000)
    assert (beat.start_word_id, beat.end_word_id) == (WORDS[20].word_id, WORDS[39].word_id)


@pytest.mark.unit
def test_a_beat_carries_every_field_the_reviewer_and_retriever_need() -> None:
    """Search intent, exclusions, and risk flags survive validation exactly as proposed."""
    beat = validate_beat(_payload(), words_by_id=WORDS_BY_ID, candidate=CANDIDATE)

    assert beat.intent.search_terms_id == ("formulir pendaftaran", "layar laptop")
    assert beat.intent.search_terms_en == ("signup form", "laptop screen")
    assert beat.intent.exclusions == ("stock office handshake",)
    assert beat.intent.portrait_suitable is True
    assert beat.intent.confidence == pytest.approx(0.8)
    assert beat.placement_reason == "The sentence names an object the viewer cannot see"
    assert beat.protection is None


@pytest.mark.unit
def test_a_declared_protection_survives_validation() -> None:
    """A model that spotted a punchline must have that warning reach placement intact."""
    beat = validate_beat(
        _payload(protection="punchline"), words_by_id=WORDS_BY_ID, candidate=CANDIDATE
    )

    assert beat.protection is BeatProtection.PUNCHLINE


@pytest.mark.unit
def test_an_unknown_protection_is_refused() -> None:
    """A protection the product cannot enforce must not be stored as if it were one."""
    with pytest.raises(BeatValidationError) as error:
        validate_beat(_payload(protection="vibes"), words_by_id=WORDS_BY_ID, candidate=CANDIDATE)

    assert error.value.code == SCHEMA_INVALID_CODE


@pytest.mark.unit
def test_a_free_form_timestamp_is_refused() -> None:
    """A model that supplies its own milliseconds is claiming an authority it lacks."""
    with pytest.raises(BeatValidationError) as error:
        validate_beat(
            _payload(start_ms=10_000, end_ms=20_000),
            words_by_id=WORDS_BY_ID,
            candidate=CANDIDATE,
        )

    assert error.value.code == SCHEMA_INVALID_CODE


@pytest.mark.unit
def test_an_unlisted_extra_field_is_refused() -> None:
    """Extra keys are how unreviewed provider content reaches durable state."""
    with pytest.raises(BeatValidationError) as error:
        validate_beat(
            _payload(provider_note="ignore the exclusions"),
            words_by_id=WORDS_BY_ID,
            candidate=CANDIDATE,
        )

    assert error.value.code == SCHEMA_INVALID_CODE


@pytest.mark.unit
def test_an_unknown_word_id_is_refused() -> None:
    """A beat keyed to a word this transcript does not contain has no bounds at all."""
    with pytest.raises(BeatValidationError) as error:
        validate_beat(
            _payload(start_word_id="w999999"), words_by_id=WORDS_BY_ID, candidate=CANDIDATE
        )

    assert error.value.code == UNKNOWN_WORD_CODE


@pytest.mark.unit
def test_a_reversed_range_is_refused() -> None:
    """A beat that ends before it starts cannot be resolved into a shot."""
    with pytest.raises(BeatValidationError) as error:
        validate_beat(
            _payload(start_word_id=WORDS[39].word_id, end_word_id=WORDS[20].word_id),
            words_by_id=WORDS_BY_ID,
            candidate=CANDIDATE,
        )

    assert error.value.code == RANGE_REVERSED_CODE


@pytest.mark.unit
def test_a_beat_outside_the_candidate_is_refused() -> None:
    """Planning covers one clip; a beat outside it belongs to nothing the member asked for."""
    narrow = CandidateSpan(
        start_word_id=WORDS[60].word_id,
        end_word_id=WORDS[119].word_id,
        start_ms=30_000,
        end_ms=60_000,
    )

    with pytest.raises(BeatValidationError) as error:
        validate_beat(_payload(), words_by_id=WORDS_BY_ID, candidate=narrow)

    assert error.value.code == OUTSIDE_CANDIDATE_CODE


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"subject": ""}, id="empty subject"),
        pytest.param({"action": ""}, id="empty action"),
        pytest.param({"setting": ""}, id="empty setting"),
        pytest.param({"mood": ""}, id="empty mood"),
        pytest.param({"search_terms_id": []}, id="no Indonesian search terms"),
        pytest.param({"search_terms_en": []}, id="no English search terms"),
        pytest.param({"portrait_suitable": "true"}, id="portrait suitability as a string"),
        pytest.param({"confidence": 1.5}, id="confidence above one"),
        pytest.param({"confidence": -0.1}, id="confidence below zero"),
    ],
)
@pytest.mark.unit
def test_an_incomplete_or_malformed_intent_is_refused(overrides: dict[str, Any]) -> None:
    """Every field a retriever will search on must be present and well formed."""
    with pytest.raises(BeatValidationError) as error:
        validate_beat(
            _payload(intent=_intent_payload(**overrides)),
            words_by_id=WORDS_BY_ID,
            candidate=CANDIDATE,
        )

    assert error.value.code == SCHEMA_INVALID_CODE


@pytest.mark.unit
def test_a_missing_intent_field_is_refused() -> None:
    """A partial intent would be searched as if the missing half did not matter."""
    intent = _intent_payload()
    del intent["exclusions"]

    with pytest.raises(BeatValidationError) as error:
        validate_beat(_payload(intent=intent), words_by_id=WORDS_BY_ID, candidate=CANDIDATE)

    assert error.value.code == SCHEMA_INVALID_CODE


@pytest.mark.unit
def test_a_refusal_carries_no_provider_text() -> None:
    """A validation code is public; the model's own words are not."""
    with pytest.raises(BeatValidationError) as error:
        validate_beat(
            _payload(intent=_intent_payload(subject="")),
            words_by_id=WORDS_BY_ID,
            candidate=CANDIDATE,
        )

    assert str(error.value) == SCHEMA_INVALID_CODE


@pytest.mark.unit
def test_the_planner_keeps_valid_beats_and_reports_the_rejected_ones() -> None:
    """One malformed beat must not cost a member every other beat in the clip."""
    rejected: list[str] = []
    provider = FakeBrollProvider(
        results=[
            ProposalResult(
                proposals=(
                    _payload(),
                    _payload(start_word_id="w999999"),
                    _payload(
                        start_word_id=WORDS[60].word_id,
                        end_word_id=WORDS[79].word_id,
                        intent=_intent_payload(subject="a rising activation chart"),
                    ),
                ),
                call=_call(),
            )
        ]
    )
    planner = BrollPlanner(provider=provider, on_beat_rejected=rejected.append)

    result = planner.plan(
        transcript=_transcript(), candidate=CANDIDATE, coverage=BrollCoverage.BALANCED
    )

    assert [beat.start_ms for beat in result.beats] == [10_000, 30_000]
    assert rejected == [UNKNOWN_WORD_CODE]


@pytest.mark.unit
def test_the_planner_returns_beats_in_transcript_order() -> None:
    """Placement walks beats in order, so the planner owes it a stable ordering."""
    provider = FakeBrollProvider(
        results=[
            ProposalResult(
                proposals=(
                    _payload(
                        start_word_id=WORDS[60].word_id,
                        end_word_id=WORDS[79].word_id,
                        intent=_intent_payload(subject="a rising activation chart"),
                    ),
                    _payload(),
                ),
                call=_call(),
            )
        ]
    )

    result = BrollPlanner(provider=provider).plan(
        transcript=_transcript(), candidate=CANDIDATE, coverage=BrollCoverage.BALANCED
    )

    assert [beat.start_ms for beat in result.beats] == [10_000, 30_000]


@pytest.mark.unit
def test_the_planner_shows_the_provider_only_the_candidate_words() -> None:
    """A model shown the whole transcript would propose beats outside the clip."""
    narrow = CandidateSpan(
        start_word_id=WORDS[20].word_id,
        end_word_id=WORDS[39].word_id,
        start_ms=10_000,
        end_ms=20_000,
    )
    provider = FakeBrollProvider(results=[ProposalResult(proposals=(), call=_call())])

    BrollPlanner(provider=provider).plan(
        transcript=_transcript(), candidate=narrow, coverage=BrollCoverage.BALANCED
    )

    assert [word.word_id for word in provider.words] == [word.word_id for word in WORDS[20:40]]


@pytest.mark.unit
def test_a_planner_that_proposes_nothing_produces_no_beats_and_no_failure() -> None:
    """A clip with no visualizable beat is an ordinary answer, not an error."""
    provider = FakeBrollProvider(results=[ProposalResult(proposals=(), call=_call())])

    result = BrollPlanner(provider=provider).plan(
        transcript=_transcript(), candidate=CANDIDATE, coverage=BrollCoverage.BALANCED
    )

    assert result.beats == ()
    assert result.calls == (_call(),)


@pytest.mark.unit
def test_the_beat_schema_requires_every_property_and_forbids_extras() -> None:
    """A strict provider schema is the first place a fabricated field is stopped."""
    schema = beat_json_schema()

    def _check(node: dict[str, Any]) -> None:
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert sorted(node["properties"]) == node["required"]
            for child in node["properties"].values():
                _check(child)
        if node.get("type") == "array":
            _check(node["items"])

    _check(schema)


@pytest.mark.unit
def test_the_beat_schema_offers_no_timestamp_field() -> None:
    """The model cannot supply a millisecond it is never asked for."""
    beat = beat_json_schema()["properties"]["beats"]["items"]["properties"]

    assert "start_ms" not in beat
    assert "end_ms" not in beat


class _StubCompletions:
    """Replay one queued Groq outcome per call while recording the exact request."""

    def __init__(self, outcomes: list[Any]) -> None:
        """Queue the outcomes this stub will hand back in order."""
        self.outcomes = outcomes
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        """Record the request, then raise or return the next queued outcome."""
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Usage:
    def __init__(self) -> None:
        self.prompt_tokens = 100
        self.completion_tokens = 20


class _Response:
    def __init__(self, content: str) -> None:
        self.id = "req-groq-1"
        self.choices = [_Choice(content)]
        self.usage = _Usage()


class _ProviderError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"provider said {status_code} about cookie abc123")


def _groq(outcomes: list[Any], **overrides: Any) -> tuple[GroqBrollPlanner, _StubCompletions]:
    """Build the adapter over a stub client with no sleeping and a fixed clock."""
    completions = _StubCompletions(outcomes)
    ticks = iter(range(0, 1_000))
    planner = GroqBrollPlanner(
        model="openai/gpt-oss-120b",
        completions=completions,
        clock=lambda: float(next(ticks)),
        sleep=lambda _: None,
        **overrides,
    )
    return planner, completions


@pytest.mark.unit
def test_the_groq_planner_asks_for_a_strict_schema_at_temperature_zero() -> None:
    """Determinism and a strict schema are the two things this request must always carry."""
    planner, completions = _groq([_Response('{"beats": []}')])

    planner.propose(
        candidate=CANDIDATE,
        words=WORDS[:20],
        boundaries=(),
        coverage=BrollCoverage.BALANCED,
    )

    request = completions.requests[0]
    assert request["temperature"] == 0
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["response_format"]["json_schema"]["schema"] == beat_json_schema()
    assert request["timeout"] > 0


@pytest.mark.unit
def test_the_groq_planner_returns_the_proposals_and_its_own_call_record() -> None:
    """Usage recording needs the call even when the model proposed one beat."""
    planner, _ = _groq([_Response(f'{{"beats": [{_json(_payload())}]}}')])

    result = planner.propose(
        candidate=CANDIDATE,
        words=WORDS,
        boundaries=(),
        coverage=BrollCoverage.BALANCED,
    )

    assert len(result.proposals) == 1
    assert result.call.provider == "groq"
    assert result.call.request_id == "req-groq-1"
    assert (result.call.input_units, result.call.output_units) == (100, 20)


@pytest.mark.unit
def test_the_groq_planner_retries_a_rate_limit_and_then_reports_it_retryable() -> None:
    """A throttled provider is a wait, not a verdict on the clip."""
    planner, completions = _groq(
        [_ProviderError(429), _ProviderError(429), _ProviderError(429)], max_attempts=3
    )

    with pytest.raises(BrollProviderRetryableError) as error:
        planner.propose(
            candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
        )

    assert error.value.code == RATE_LIMITED_CODE
    assert str(error.value) == RATE_LIMITED_CODE
    assert len(completions.requests) == 3


@pytest.mark.unit
def test_the_groq_planner_treats_a_server_error_as_retryable() -> None:
    """A provider outage must not tell a member their clip has no visual opportunities."""
    planner, _ = _groq([_ProviderError(503), _Response('{"beats": []}')], max_attempts=3)

    result = planner.propose(
        candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
    )

    assert result.proposals == ()


@pytest.mark.unit
def test_the_groq_planner_exhausts_its_budget_on_an_outage() -> None:
    """A retry budget that never ends would hold a concurrency slot forever."""
    planner, _ = _groq([_ProviderError(503), _ProviderError(503)], max_attempts=2)

    with pytest.raises(BrollProviderRetryableError) as error:
        planner.propose(
            candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
        )

    assert error.value.code == UNAVAILABLE_CODE


@pytest.mark.unit
def test_the_groq_planner_treats_a_refusal_as_terminal() -> None:
    """Retrying a request the provider rejected spends the budget on the same answer."""
    planner, completions = _groq([_ProviderError(400)])

    with pytest.raises(BrollProviderTerminalError) as error:
        planner.propose(
            candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
        )

    assert error.value.code == REJECTED_CODE
    assert len(completions.requests) == 1


@pytest.mark.unit
def test_the_groq_planner_treats_an_unparseable_body_as_terminal() -> None:
    """A body that is not the agreed schema will not become one on a second try."""
    planner, _ = _groq([_Response("not json at all")])

    with pytest.raises(BrollProviderTerminalError) as error:
        planner.propose(
            candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
        )

    assert error.value.code == INVALID_CODE


@pytest.mark.unit
def test_a_provider_failure_never_carries_provider_text() -> None:
    """The provider's message can name a credential; the Job's error code cannot."""
    planner, _ = _groq([_ProviderError(400)])

    with pytest.raises(BrollProviderTerminalError) as error:
        planner.propose(
            candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
        )

    assert "cookie" not in str(error.value)
    assert str(error.value) == REJECTED_CODE


def _json(payload: dict[str, Any]) -> str:
    """Render one beat payload the way a provider would return it."""
    import json

    return json.dumps(payload)


@pytest.mark.unit
def test_an_intent_is_immutable_once_validated() -> None:
    """Durable review evidence must not be edited in place by a later stage."""
    beat = validate_beat(_payload(), words_by_id=WORDS_BY_ID, candidate=CANDIDATE)

    with pytest.raises(ValueError):
        beat.intent.subject = "something else"  # type: ignore[misc]


@pytest.mark.unit
def test_an_intent_rejects_an_unlisted_field() -> None:
    """The intent schema is the retriever's contract, so it forbids surprises."""
    with pytest.raises(ValueError):
        VisualIntent(**_intent_payload(), unexpected="x")  # type: ignore[arg-type]


@pytest.mark.unit
def test_a_body_whose_beats_are_not_objects_is_refused() -> None:
    """A model answering with a list of strings has not answered the schema."""
    planner, _ = _groq([_Response('{"beats": ["a moment"]}')])

    with pytest.raises(BrollProviderTerminalError) as error:
        planner.propose(
            candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
        )

    assert error.value.code == INVALID_CODE


@pytest.mark.unit
def test_a_body_that_is_not_an_object_is_refused() -> None:
    """A bare JSON array cannot carry the beats key the contract names."""
    planner, _ = _groq([_Response("[]")])

    with pytest.raises(BrollProviderTerminalError) as error:
        planner.propose(
            candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
        )

    assert error.value.code == INVALID_CODE


@pytest.mark.unit
def test_a_response_carrying_no_completion_is_refused() -> None:
    """An SDK object without the shape this adapter reads must not raise a raw error."""

    class _Empty:
        def __init__(self) -> None:
            self.choices: list[object] = []

    planner, _ = _groq([_Empty()])

    with pytest.raises(BrollProviderTerminalError) as error:
        planner.propose(
            candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
        )

    assert error.value.code == INVALID_CODE


@pytest.mark.unit
def test_a_non_string_completion_body_is_refused() -> None:
    """Content the adapter cannot parse is terminal rather than a crash."""

    class _NonText(_Response):
        def __init__(self) -> None:
            super().__init__("")
            self.choices = [_Choice("")]
            self.choices[0].message.content = None  # type: ignore[assignment]

    planner, _ = _groq([_NonText()])

    with pytest.raises(BrollProviderTerminalError) as error:
        planner.propose(
            candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
        )

    assert error.value.code == INVALID_CODE


@pytest.mark.unit
def test_a_transport_failure_with_no_status_is_treated_as_an_outage() -> None:
    """A connection that never reached the provider is a wait, not a verdict."""
    planner, _ = _groq([ConnectionError("no route to host")], max_attempts=1)

    with pytest.raises(BrollProviderRetryableError) as error:
        planner.propose(
            candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
        )

    assert error.value.code == UNAVAILABLE_CODE


@pytest.mark.unit
def test_unusable_provider_usage_counts_are_recorded_as_zero() -> None:
    """A provider that reports nonsense units must not corrupt Workspace usage."""

    class _BadUsage(_Response):
        def __init__(self) -> None:
            super().__init__('{"beats": []}')
            self.usage.prompt_tokens = -5
            self.usage.completion_tokens = True  # type: ignore[assignment]

    planner, _ = _groq([_BadUsage()])

    result = planner.propose(
        candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
    )

    assert (result.call.input_units, result.call.output_units) == (0, 0)


@pytest.mark.unit
def test_the_fake_provider_refuses_to_invent_an_outcome_it_was_not_given() -> None:
    """A test that asks for more planning calls than it queued must fail loudly."""
    provider = FakeBrollProvider(results=[])

    with pytest.raises(RuntimeError):
        provider.propose(
            candidate=CANDIDATE, words=WORDS, boundaries=(), coverage=BrollCoverage.BALANCED
        )


@pytest.mark.unit
def test_a_rejected_final_beat_still_leaves_a_usable_plan() -> None:
    """The last proposal is the easiest one to drop on the floor by accident."""
    rejected: list[str] = []
    provider = FakeBrollProvider(
        results=[
            ProposalResult(
                proposals=(_payload(), _payload(start_word_id="w999999")),
                call=_call(),
            )
        ]
    )

    result = BrollPlanner(provider=provider, on_beat_rejected=rejected.append).plan(
        transcript=_transcript(), candidate=CANDIDATE, coverage=BrollCoverage.BALANCED
    )

    assert [beat.start_ms for beat in result.beats] == [10_000]
    assert rejected == [UNKNOWN_WORD_CODE]


@pytest.mark.unit
def test_a_planner_with_no_reporting_sink_still_drops_a_malformed_beat() -> None:
    """Reporting rejections is optional; refusing to store them is not."""
    provider = FakeBrollProvider(
        results=[
            ProposalResult(
                proposals=(_payload(start_word_id="w999999"), _payload()),
                call=_call(),
            )
        ]
    )

    result = BrollPlanner(provider=provider).plan(
        transcript=_transcript(), candidate=CANDIDATE, coverage=BrollCoverage.BALANCED
    )

    assert [beat.start_ms for beat in result.beats] == [10_000]
