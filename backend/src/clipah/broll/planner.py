"""The B-roll beat provider port, its Groq adapter, and local beat resolution.

A planning model is shown one clip's own words and is asked which of them would benefit
from a picture. It answers in word IDs and meaning. Everything it returns is validated
here against the authoritative transcript before it becomes a beat, and the milliseconds
are read from that transcript rather than from anything the model wrote.

There is deliberately no offline fallback planner. Highlights fall back because a Project
without candidates is a Project without a product; B-roll is optional, and telling a member
their clip has no visual opportunities when in truth the provider was unreachable is a
worse answer than a retryable failure they can run again.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from groq import Groq
from pydantic import ValidationError

from clipah.broll.models import (
    PLANNER_VERSION,
    BeatProtection,
    BrollCoverage,
    CandidateSpan,
    VisualBeat,
    VisualBeatProposal,
)
from clipah.highlights.provider import ProviderCall
from clipah.transcripts.models import TranscriptResult, TranscriptWord

PROVIDER = "groq"
PLAN_OPERATION = "broll_plan"
PLAN_PROMPT_VERSION = "broll/plan/1"
BEAT_SCHEMA_VERSION = "broll-beat/1"
REQUEST_TIMEOUT_SECONDS = 120.0
RETRY_BASE_DELAY_SECONDS = 0.5

SCHEMA_INVALID_CODE = "BROLL_BEAT_SCHEMA_INVALID"
UNKNOWN_WORD_CODE = "BROLL_BEAT_UNKNOWN_WORD_ID"
RANGE_REVERSED_CODE = "BROLL_BEAT_RANGE_REVERSED"
OUTSIDE_CANDIDATE_CODE = "BROLL_BEAT_OUTSIDE_CANDIDATE"

RATE_LIMITED_CODE = "BROLL_PROVIDER_RATE_LIMITED"
UNAVAILABLE_CODE = "BROLL_PROVIDER_UNAVAILABLE"
REJECTED_CODE = "BROLL_PROVIDER_REJECTED"
INVALID_CODE = "BROLL_PROVIDER_INVALID"

_SYSTEM_PROMPT = (
    "You mark the moments in a clip whose meaning a viewer would grasp faster from a "
    "picture. Refer to moments only by the word IDs you were given, and never invent a "
    "timestamp. Describe a concrete subject, action, setting, and mood that a stock "
    "library could actually be searched for. Give the search terms twice: once in "
    "Indonesian and once in English, translating the intent rather than the transcript, "
    "so an Indonesian concept keeps its local meaning instead of becoming a literal "
    "English phrase. Mark a beat as protected when covering it would hide a face reveal, "
    "a punchline, a demonstration, an emotional pause, or a culturally sensitive passage. "
    "Propose no beats at all rather than weak ones."
)


class BeatValidationError(Exception):
    """Refuse one proposed beat through a stable code that carries no provider text."""

    def __init__(self, code: str) -> None:
        """Retain only the public-safe reason this beat was refused."""
        self.code = code
        super().__init__(code)


class BrollProviderRetryableError(Exception):
    """Represent a temporary planning-provider failure through a stable code."""

    def __init__(self, code: str) -> None:
        """Retain only public-safe retry information."""
        self.code = code
        super().__init__(code)


class BrollProviderTerminalError(Exception):
    """Represent a permanent planning-provider failure through a stable code."""

    def __init__(self, code: str) -> None:
        """Retain only public-safe terminal information."""
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProposalResult:
    """Unvalidated beat proposals for one clip, with the call that produced them."""

    proposals: tuple[Mapping[str, Any], ...]
    call: ProviderCall


@dataclass(frozen=True, slots=True)
class PlanResult:
    """The accepted beats of one clip and every provider call spent producing them."""

    beats: tuple[VisualBeat, ...]
    calls: tuple[ProviderCall, ...]


class BrollBeatProvider(Protocol):
    """Provider-independent capability to propose visual beats for one clip."""

    def propose(
        self,
        *,
        candidate: CandidateSpan,
        words: Sequence[TranscriptWord],
        boundaries: Sequence[int],
        coverage: BrollCoverage,
    ) -> ProposalResult:
        """Propose beats keyed to the word IDs of this clip and nothing else."""


class BrollPlanner:
    """Ask one provider for beats and keep only those the transcript can vouch for."""

    def __init__(
        self,
        *,
        provider: BrollBeatProvider,
        on_beat_rejected: Callable[[str], None] | None = None,
    ) -> None:
        """Bind the provider and an optional sink for each refused beat's code."""
        self._provider = provider
        self._on_beat_rejected = on_beat_rejected

    def plan(
        self,
        *,
        transcript: TranscriptResult,
        candidate: CandidateSpan,
        coverage: BrollCoverage,
        boundaries: Sequence[int] = (),
    ) -> PlanResult:
        """Return every beat of one clip that resolves against the authoritative words."""
        words_by_id = {word.word_id: word for word in transcript.words}
        clip_words = _clip_words(transcript.words, candidate)
        result = self._provider.propose(
            candidate=candidate,
            words=clip_words,
            boundaries=boundaries,
            coverage=coverage,
        )
        beats: list[VisualBeat] = []
        for proposal in result.proposals:
            try:
                beats.append(validate_beat(proposal, words_by_id=words_by_id, candidate=candidate))
            except BeatValidationError as error:
                if self._on_beat_rejected is not None:
                    self._on_beat_rejected(error.code)
        beats.sort(key=lambda beat: (beat.start_ms, beat.end_ms, beat.start_word_id))
        return PlanResult(beats=tuple(beats), calls=(result.call,))


def validate_beat(
    payload: Mapping[str, Any],
    *,
    words_by_id: Mapping[str, TranscriptWord],
    candidate: CandidateSpan,
) -> VisualBeat:
    """Accept one proposal only if this transcript can place it inside this clip."""
    try:
        proposal = VisualBeatProposal.model_validate(payload)
    except ValidationError:
        raise BeatValidationError(SCHEMA_INVALID_CODE) from None

    start = words_by_id.get(proposal.start_word_id)
    end = words_by_id.get(proposal.end_word_id)
    if start is None or end is None:
        raise BeatValidationError(UNKNOWN_WORD_CODE)
    if end.end_ms <= start.start_ms:
        raise BeatValidationError(RANGE_REVERSED_CODE)
    if start.start_ms < candidate.start_ms or end.end_ms > candidate.end_ms:
        raise BeatValidationError(OUTSIDE_CANDIDATE_CODE)

    return VisualBeat(
        start_word_id=start.word_id,
        end_word_id=end.word_id,
        start_ms=start.start_ms,
        end_ms=end.end_ms,
        intent=proposal.intent,
        placement_reason=proposal.placement_reason,
        protection=proposal.protection,
    )


class _Completions(Protocol):
    """Structural subset of the Groq chat completions client used by this adapter."""

    def create(self, **request: Any) -> Any:
        """Submit one chat completion request."""


class GroqBrollPlanner:
    """Translate Groq chat completions into Clipah's beat-proposal contract."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        completions: _Completions | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
    ) -> None:
        """Bind the configured model and an injectable clock, sleep, and client."""
        self._model = model
        self._completions = completions or cast(
            _Completions, Groq(api_key=api_key).chat.completions
        )
        self._clock = clock
        self._sleep = sleep
        self._max_attempts = max_attempts

    def propose(
        self,
        *,
        candidate: CandidateSpan,
        words: Sequence[TranscriptWord],
        boundaries: Sequence[int],
        coverage: BrollCoverage,
    ) -> ProposalResult:
        """Ask the model for strict-schema beats over one clip's own words."""
        response, latency_ms = self._call(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _plan_prompt(words, boundaries, coverage)},
            ]
        )
        body = _json_body(response)
        proposals = body.get("beats")
        if not isinstance(proposals, list) or not all(isinstance(item, dict) for item in proposals):
            raise BrollProviderTerminalError(INVALID_CODE)
        usage = getattr(response, "usage", None)
        return ProposalResult(
            proposals=tuple(cast(list[Mapping[str, Any]], proposals)),
            call=ProviderCall(
                provider=PROVIDER,
                operation=PLAN_OPERATION,
                model=self._model,
                request_id=_text(getattr(response, "id", "")),
                latency_ms=latency_ms,
                input_units=_units(getattr(usage, "prompt_tokens", 0)),
                output_units=_units(getattr(usage, "completion_tokens", 0)),
                prompt_version=PLAN_PROMPT_VERSION,
                schema_version=BEAT_SCHEMA_VERSION,
            ),
        )

    def _call(self, *, messages: list[dict[str, str]]) -> tuple[Any, int]:
        """Spend the retry budget on transient failures only, and time the successful call."""
        attempt = 0
        while True:
            attempt += 1
            started = self._clock()
            try:
                response = self._completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=0,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "broll_beats",
                            "strict": True,
                            "schema": beat_json_schema(),
                        },
                    },
                )
            except Exception as error:
                code = _failure_code(error)
                if code in {RATE_LIMITED_CODE, UNAVAILABLE_CODE}:
                    if attempt >= self._max_attempts:
                        raise BrollProviderRetryableError(code) from None
                    self._sleep(RETRY_BASE_DELAY_SECONDS * attempt)
                    continue
                raise BrollProviderTerminalError(code) from None
            return response, round((self._clock() - started) * 1000)


class FakeBrollProvider:
    """Deterministic provider used where real provider work would be inappropriate."""

    def __init__(self, *, results: Sequence[ProposalResult | Exception] = ()) -> None:
        """Queue one outcome per expected planning call."""
        self.results = list(results)
        self.words: tuple[TranscriptWord, ...] = ()
        self.boundaries: tuple[int, ...] = ()
        self.coverages: list[BrollCoverage] = []

    def propose(
        self,
        *,
        candidate: CandidateSpan,
        words: Sequence[TranscriptWord],
        boundaries: Sequence[int],
        coverage: BrollCoverage,
    ) -> ProposalResult:
        """Record exactly what the planner showed it before replaying its outcome."""
        del candidate
        self.words = tuple(words)
        self.boundaries = tuple(boundaries)
        self.coverages.append(coverage)
        if not self.results:
            raise RuntimeError("FakeBrollProvider ran out of planning outcomes")
        outcome = self.results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def beat_json_schema() -> dict[str, Any]:
    """Describe exactly the beat list a provider may return, and nothing else.

    There is no millisecond field anywhere in this schema, so a model cannot supply a
    timestamp even by accident: the only time it can express is a word ID.
    """
    return _object(
        {
            "beats": {
                "type": "array",
                "items": _object(
                    {
                        "start_word_id": {"type": "string"},
                        "end_word_id": {"type": "string"},
                        "placement_reason": {"type": "string"},
                        "protection": {
                            "type": ["string", "null"],
                            "enum": [*(item.value for item in BeatProtection), None],
                        },
                        "intent": _object(
                            {
                                "subject": {"type": "string"},
                                "action": {"type": "string"},
                                "setting": {"type": "string"},
                                "mood": {"type": "string"},
                                "search_terms_id": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "search_terms_en": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "portrait_suitable": {"type": "boolean"},
                                "exclusions": {"type": "array", "items": {"type": "string"}},
                                "factual_risk_flags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            }
                        ),
                    }
                ),
            }
        }
    )


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    """Build one strict object schema in which every property is required."""
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


def _clip_words(
    words: Sequence[TranscriptWord], candidate: CandidateSpan
) -> tuple[TranscriptWord, ...]:
    """Show a provider only the words of the clip it is planning for."""
    return tuple(
        word
        for word in words
        if word.start_ms >= candidate.start_ms and word.end_ms <= candidate.end_ms
    )


def _plan_prompt(
    words: Sequence[TranscriptWord],
    boundaries: Sequence[int],
    coverage: BrollCoverage,
) -> str:
    """Show the model the clip's words, its scene changes, and the coverage asked for."""
    return json.dumps(
        {
            "coverage": coverage.value,
            "planner_version": PLANNER_VERSION,
            "scene_boundaries_ms": list(boundaries),
            "words": [
                {"word_id": word.word_id, "text": f"{word.text}{word.punctuation}"}
                for word in words
            ],
        },
        ensure_ascii=False,
    )


def _json_body(response: Any) -> dict[str, Any]:
    """Parse the single completion body, refusing anything that is not a JSON object."""
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        raise BrollProviderTerminalError(INVALID_CODE) from None
    if not isinstance(content, str):
        raise BrollProviderTerminalError(INVALID_CODE)
    try:
        body = json.loads(content)
    except ValueError:
        raise BrollProviderTerminalError(INVALID_CODE) from None
    if not isinstance(body, dict):
        raise BrollProviderTerminalError(INVALID_CODE)
    return cast(dict[str, Any], body)


def _failure_code(error: Exception) -> str:
    """Classify one provider failure by status alone, never by its message."""
    status = getattr(error, "status_code", None)
    if not isinstance(status, int) or isinstance(status, bool):
        return UNAVAILABLE_CODE
    if status == 429:
        return RATE_LIMITED_CODE
    if status >= 500:
        return UNAVAILABLE_CODE
    return REJECTED_CODE


def _text(value: object) -> str:
    """Keep only a string identifier, never an SDK object."""
    return value if isinstance(value, str) else ""


def _units(value: object) -> int:
    """Keep only a non-negative integer count of provider units."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value
