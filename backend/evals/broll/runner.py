"""Score one B-roll ranking adapter against the checked-in labeled cases.

    scripts/run-broll-eval.sh --adapter=fake
    scripts/run-broll-eval.sh --adapter=fake --output=reports/broll.json

The runner is wiring only: it selects an adapter, takes one observation per case, writes
the report, and exits non-zero when a release gate is violated. Every judgement about what
a good selection is lives in `clipah.broll.evaluation`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from clipah.broll.evaluation import (
    BrollCase,
    BrollReport,
    build_report,
    fake_vision_for,
    load_cases,
    observations_from,
    select_with_reranker,
)
from clipah.broll.reranker import DeterministicVisualReranker
from clipah.broll.retriever import ExternalAssetCandidate

CASES_DIRECTORY = Path(__file__).resolve().parent


def _shipped(case: BrollCase) -> tuple[ExternalAssetCandidate, ...]:
    """Rank one case's pool with the shipped reranker and nothing else."""
    return select_with_reranker(case, reranker=DeterministicVisualReranker())


def _with_vision(case: BrollCase) -> tuple[ExternalAssetCandidate, ...]:
    """Rank with a deterministic stand-in for a vision model, to exercise that path."""
    return select_with_reranker(
        case, reranker=DeterministicVisualReranker(frames=fake_vision_for(case))
    )


ADAPTERS = {
    "fake": (_shipped, "", ""),
    "fake-vision": (_with_vision, "fake-vision", "1"),
}


def run(argv: Sequence[str] | None = None) -> int:
    """Score the checked-in cases and report whether every release gate held."""
    parser = argparse.ArgumentParser(description="Run the B-roll retrieval evaluation.")
    parser.add_argument("--adapter", default="fake", choices=sorted(ADAPTERS))
    parser.add_argument("--output", default=None)
    arguments = parser.parse_args(argv)

    version, cases = load_cases(CASES_DIRECTORY)
    select, model, model_version = ADAPTERS[arguments.adapter]
    report = build_report(
        cases=cases,
        observations=observations_from(cases, select=select),
        manifest_version=version,
        adapter=arguments.adapter,
        reranker_model=model,
        reranker_model_version=model_version,
    )
    _emit(report, output=arguments.output)
    return 0 if report.passed else 1


def _emit(report: BrollReport, *, output: str | None) -> None:
    """Print the report, and keep it as an artifact when one was asked for."""
    document = json.dumps(report.as_document(), ensure_ascii=False, indent=2)
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document + "\n", encoding="utf-8")
    print(document)


if __name__ == "__main__":
    sys.exit(run())
