"""Offline transcription adapter that replays a case's own labels.

This adapter exists so the harness itself can be exercised in ordinary CI without credentials,
without network access, and without cost. It makes no claim about any provider: it replays the
labeled words, so a green run means the fixtures, the metrics, and the report are intact, not
that transcription quality was measured.
"""

from __future__ import annotations

from clipah.transcripts.evaluation import (
    ReferenceWord,
    TranscriptionCase,
    TranscriptionObservation,
)
from clipah.transcripts.models import RawUtterance, RawWord
from clipah.transcripts.use_cases import normalize_transcript

ADAPTER_NAME = "fake"
PROVIDER = "fixture-replay"
PROVIDER_VERSION = "1"
MODEL = "fixture-replay"

_LATENCY_DIVISOR = 20


class FakeEvaluationTranscriber:
    """Replay one case's labeled words as if a provider had returned them."""

    name = ADAPTER_NAME
    provider = PROVIDER
    provider_version = PROVIDER_VERSION
    model = MODEL

    def observe(self, case: TranscriptionCase) -> TranscriptionObservation:
        """Return the labeled words with a latency derived from the case's own duration."""
        words = tuple(
            RawWord(
                text=word.text,
                start_ms=word.start_ms,
                end_ms=word.end_ms,
                confidence=1.0,
                speaker=word.speaker,
            )
            for word in case.words
        )
        return TranscriptionObservation(
            case_id=case.case_id,
            transcript=normalize_transcript(
                provider=PROVIDER,
                provider_version=PROVIDER_VERSION,
                model=MODEL,
                language=case.language,
                duration_ms=case.duration_ms,
                words=words,
                utterances=_utterances(case),
                raw_result={},
            ),
            error_code=None,
            latency_ms=case.duration_ms // _LATENCY_DIVISOR,
            cost_micros=0,
        )


def _utterances(case: TranscriptionCase) -> tuple[RawUtterance, ...]:
    """Collapse the labeled words into one utterance per contiguous speaker run."""
    utterances: list[RawUtterance] = []
    run: list[ReferenceWord] = [case.words[0]]
    for word in case.words[1:]:
        if word.speaker != run[0].speaker:
            utterances.append(_utterance(run))
            run = []
        run.append(word)
    utterances.append(_utterance(run))
    return tuple(utterances)


def _utterance(run: list[ReferenceWord]) -> RawUtterance:
    """Describe one contiguous speaker run as the provider contract expects."""
    return RawUtterance(
        text=" ".join(word.text for word in run),
        start_ms=run[0].start_ms,
        end_ms=run[-1].end_ms,
        speaker=run[0].speaker,
    )
