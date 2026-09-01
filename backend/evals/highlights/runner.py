"""Command-line runner for the versioned highlight evaluation.

The runner is wiring only: it chooses an adapter, drives the ordinary analyzer over the
checked-in cases, and writes the report the metrics module produced. Every judgement about
quality lives in ``clipah.highlights.evaluation`` so the gates cannot drift between runs.

The offline ``fake`` adapter needs no credentials and must always pass. A live adapter needs
explicit configuration, and its report is an artifact rather than a required check.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from clipah.config import Settings
from clipah.highlights.analyzer import AnalysisFailedError, HighlightAnalyzer
from clipah.highlights.evaluation import (
    HighlightCase,
    HighlightMetrics,
    build_highlight_report,
    evaluate_highlight_case,
    load_highlight_cases,
)
from clipah.highlights.groq_adapter import GroqHighlightProvider
from clipah.highlights.provider import (
    CANDIDATE_SCHEMA_VERSION,
    DETERMINISTIC_PROMPT_VERSION,
    DETERMINISTIC_PROVIDER,
    DeterministicHighlightProvider,
    HighlightProvider,
)
from clipah.transcripts.models import TranscriptResult

FAKE_ADAPTER = "fake"
GROQ_ADAPTER = "groq"
GROQ_PROMPT_VERSION = "groq-highlight/1"


def build_provider(adapter: str, transcript: TranscriptResult) -> HighlightProvider:
    """Choose the provider one case is analyzed with, offline unless asked otherwise."""
    if adapter == FAKE_ADAPTER:
        return DeterministicHighlightProvider(transcript=transcript)
    settings = Settings()
    return GroqHighlightProvider(
        extraction_model=settings.gpt_oss_extraction_model,
        reranking_model=settings.gpt_oss_reranking_model,
        api_key=settings.groq_api_key.get_secret_value() if settings.groq_api_key else None,
    )


def attribution(adapter: str) -> tuple[str, str, str]:
    """Name the provider, model, and prompt version a report must be attributed to."""
    if adapter == FAKE_ADAPTER:
        return DETERMINISTIC_PROVIDER, DETERMINISTIC_PROVIDER, DETERMINISTIC_PROMPT_VERSION
    settings = Settings()
    return GROQ_ADAPTER, settings.gpt_oss_extraction_model, GROQ_PROMPT_VERSION


def measure(case: HighlightCase, adapter: str) -> HighlightMetrics:
    """Analyze one case and measure the result, scoring a failed analysis as a total miss."""
    analyzer = HighlightAnalyzer(provider=build_provider(adapter, case.transcript))
    try:
        result = analyzer.analyze(transcript=case.transcript)
    except AnalysisFailedError:
        return evaluate_highlight_case(case=case, ranked=())
    return evaluate_highlight_case(case=case, ranked=result.ranked)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the highlight evaluation and report whether every gate held."""
    parser = argparse.ArgumentParser(description="Run the Clipah highlight evaluation")
    parser.add_argument("--adapter", default=FAKE_ADAPTER, choices=(FAKE_ADAPTER, GROQ_ADAPTER))
    parser.add_argument("--cases-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args(argv)

    manifest_version, cases = load_highlight_cases(arguments.cases_dir)
    provider, model, prompt_version = attribution(arguments.adapter)
    report = build_highlight_report(
        manifest_version=manifest_version,
        adapter=arguments.adapter,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        schema_version=CANDIDATE_SCHEMA_VERSION,
        cases=tuple(
            (case.case_id, case.language, measure(case, arguments.adapter)) for case in cases
        ),
    )
    document = json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(document, encoding="utf-8")
    sys.stdout.write(document)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
