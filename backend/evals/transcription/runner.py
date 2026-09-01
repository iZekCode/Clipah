"""Command-line runner for the versioned transcription evaluation.

The runner is wiring only: it selects an adapter, takes one observation per labeled case, and
writes the report the metrics module produced. The offline ``fake`` adapter needs no
credentials and must always pass. Every live adapter needs an explicit credential and a local
directory of evaluation audio, so a live comparison is always a deliberate act and never an
ordinary CI dependency.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from clipah.config import Settings
from clipah.transcripts.evaluation import (
    TranscriptionCase,
    TranscriptionObservation,
    build_transcription_report,
    load_transcription_cases,
)
from evals.transcription.adapters.fake import FakeEvaluationTranscriber

FAKE_ADAPTER = "fake"
ASSEMBLYAI_ADAPTER = "assemblyai"
DEEPGRAM_ADAPTER = "deepgram"
WHISPERX_ADAPTER = "whisperx"
ADAPTERS = (FAKE_ADAPTER, ASSEMBLYAI_ADAPTER, DEEPGRAM_ADAPTER, WHISPERX_ADAPTER)


class EvaluationTranscriber(Protocol):
    """What every evaluation adapter must expose, live or offline."""

    name: str
    provider: str
    provider_version: str
    model: str

    def observe(self, case: TranscriptionCase) -> TranscriptionObservation:
        """Measure exactly one labeled case."""


def build_adapter(name: str, audio_directory: Path | None) -> EvaluationTranscriber:
    """Select one adapter, importing a live provider only when it was actually asked for."""
    if name == FAKE_ADAPTER:
        return FakeEvaluationTranscriber()
    if audio_directory is None:
        raise SystemExit(f"the {name} adapter requires --audio-dir with evaluation audio")
    if name == ASSEMBLYAI_ADAPTER:
        import assemblyai

        from evals.transcription.adapters.assemblyai import AssemblyAIEvaluationTranscriber

        settings = Settings()
        if settings.assemblyai_api_key is None:
            raise SystemExit("the assemblyai adapter requires CLIPAH_ASSEMBLYAI_API_KEY")
        return AssemblyAIEvaluationTranscriber(
            api_key=settings.assemblyai_api_key.get_secret_value(),
            audio_directory=audio_directory,
            provider_version=str(assemblyai.__version__),
        )
    if name == DEEPGRAM_ADAPTER:
        from evals.transcription.adapters.deepgram import (
            API_KEY_VARIABLE,
            DeepgramEvaluationTranscriber,
            api_key_from_environment,
        )

        api_key = api_key_from_environment()
        if api_key is None:
            raise SystemExit(f"the deepgram adapter requires {API_KEY_VARIABLE}")
        return DeepgramEvaluationTranscriber(api_key=api_key, audio_directory=audio_directory)
    from evals.transcription.adapters.whisperx import WhisperXEvaluationTranscriber

    return WhisperXEvaluationTranscriber(audio_directory=audio_directory)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the transcription evaluation and report whether every case was measured."""
    parser = argparse.ArgumentParser(description="Run the Clipah transcription evaluation")
    parser.add_argument("--adapter", default=FAKE_ADAPTER, choices=ADAPTERS)
    parser.add_argument("--cases-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--audio-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args(argv)

    manifest_version, cases = load_transcription_cases(arguments.cases_dir)
    adapter = build_adapter(arguments.adapter, arguments.audio_dir)
    observations = tuple(adapter.observe(case) for case in cases)
    report = build_transcription_report(
        manifest_version=manifest_version,
        adapter=adapter.name,
        provider=adapter.provider,
        provider_version=adapter.provider_version,
        model=adapter.model,
        cases=cases,
        observations=observations,
    )
    document = json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(document, encoding="utf-8")
    sys.stdout.write(document)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
