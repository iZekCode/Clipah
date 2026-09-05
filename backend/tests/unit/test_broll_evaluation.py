"""Contracts for the B-roll evaluation harness and its checked-in labeled corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from clipah.broll.evaluation import (
    GATE_PROVENANCE_COMPLETENESS,
    GATE_TOP_THREE_RELEVANCE,
    GATE_UNSAFE_SELECTIONS,
    BrollObservation,
    CandidateLabel,
    aggregate,
    build_report,
    fake_vision_for,
    load_cases,
    score_case,
    select_with_reranker,
)
from clipah.broll.reranker import DeterministicVisualReranker
from clipah.transcripts.evaluation import EvaluationFixtureError

CASES_DIRECTORY = Path(__file__).resolve().parents[2] / "evals" / "broll"


def _cases() -> Any:
    """Load the checked-in corpus once per test."""
    return load_cases(CASES_DIRECTORY)


def select_with_production_reranker(case: Any) -> Any:
    """Rank one case exactly as the shipped runner does."""
    return select_with_reranker(case, reranker=DeterministicVisualReranker())


@pytest.mark.unit
def test_the_checked_in_corpus_covers_thirty_indonesian_and_english_intents() -> None:
    """The plan asks for at least thirty labeled intents across both languages."""
    _, cases = _cases()

    assert len(cases) >= 30
    languages = {case.language for case in cases}
    assert languages == {"id", "en"}


@pytest.mark.unit
def test_every_case_offers_each_kind_of_awkward_candidate() -> None:
    """A corpus of only good candidates would measure nothing about the ranking."""
    _, cases = _cases()

    for case in cases:
        labels = {labeled.label for labeled in case.candidates}
        assert labels == set(CandidateLabel), case.case_id


@pytest.mark.unit
def test_the_shipped_reranker_meets_every_release_gate_on_the_corpus() -> None:
    """These are the gates the plan requires before B-roll reaches the editor."""
    version, cases = _cases()

    report = build_report(
        cases=cases,
        observations=[
            BrollObservation(case_id=case.case_id, selected=select_with_production_reranker(case))
            for case in cases
        ],
        manifest_version=version,
        adapter="fake",
    )

    assert report.passed
    assert report.metrics.provenance_completeness == 1.0
    assert report.metrics.unsafe_selections == 0
    assert report.metrics.top_three_relevance >= 0.8


@pytest.mark.unit
def test_an_unsafe_selection_fails_its_gate() -> None:
    """Zero is the only acceptable number of unsafe pictures in a published clip."""
    _, cases = _cases()
    case = cases[0]
    unsafe = next(
        labeled.candidate for labeled in case.candidates if labeled.label is CandidateLabel.UNSAFE
    )

    metrics = score_case(case, BrollObservation(case_id=case.case_id, selected=(unsafe,)))

    assert metrics.unsafe_selections == 1
    assert GATE_UNSAFE_SELECTIONS in metrics.violations


@pytest.mark.unit
def test_an_untraceable_selection_fails_its_gate() -> None:
    """A picture whose provenance cannot be completed must never have been chosen."""
    _, cases = _cases()
    case = cases[0]
    traceable = case.candidates[0].candidate
    from dataclasses import replace

    metrics = score_case(
        case,
        BrollObservation(case_id=case.case_id, selected=(replace(traceable, author=""),)),
    )

    assert metrics.provenance_completeness == 0.0
    assert GATE_PROVENANCE_COMPLETENESS in metrics.violations


@pytest.mark.unit
def test_irrelevant_selections_fail_the_relevance_gate() -> None:
    """A ranking that fills the top three with the wrong pictures is a regression."""
    _, cases = _cases()
    case = cases[0]
    wrong = tuple(
        labeled.candidate
        for labeled in case.candidates
        if labeled.label is CandidateLabel.IRRELEVANT
    )

    metrics = score_case(case, BrollObservation(case_id=case.case_id, selected=wrong))

    assert metrics.top_three_relevance == 0.0
    assert GATE_TOP_THREE_RELEVANCE in metrics.violations


@pytest.mark.unit
def test_only_the_top_three_selections_are_scored() -> None:
    """A member is offered three pictures, so a fourth is not what the gate is about."""
    _, cases = _cases()
    case = cases[0]
    selected = tuple(labeled.candidate for labeled in case.candidates)

    metrics = score_case(case, BrollObservation(case_id=case.case_id, selected=selected))

    assert metrics.selection_count == 3


@pytest.mark.unit
def test_selecting_nothing_is_a_clean_case_rather_than_a_failed_one() -> None:
    """A concept nothing illustrates must not be scored as a ranking failure."""
    _, cases = _cases()

    metrics = score_case(cases[0], BrollObservation(case_id=cases[0].case_id, selected=()))

    assert metrics.violations == ()


@pytest.mark.unit
def test_aggregation_weights_rates_by_the_selections_they_were_measured_over() -> None:
    """A case that offered one picture must not outvote one that offered three."""
    _, cases = _cases()
    case = cases[0]
    relevant = [
        labeled.candidate for labeled in case.candidates if labeled.label is CandidateLabel.RELEVANT
    ]
    irrelevant = next(
        labeled.candidate
        for labeled in case.candidates
        if labeled.label is CandidateLabel.IRRELEVANT
    )

    combined = aggregate(
        [
            score_case(case, BrollObservation(case_id=case.case_id, selected=tuple(relevant))),
            score_case(case, BrollObservation(case_id=case.case_id, selected=(irrelevant,))),
        ]
    )

    assert combined.selection_count == 4
    assert combined.top_three_relevance == pytest.approx(0.75)


@pytest.mark.unit
def test_aggregating_nothing_reports_a_clean_empty_run() -> None:
    """An empty run has violated nothing, which is different from having passed nothing."""
    empty = aggregate([])

    assert empty.case_count == 0
    assert empty.violations == ()


@pytest.mark.unit
def test_a_case_with_no_observation_is_refused_rather_than_skipped() -> None:
    """Silently scoring fewer cases than the corpus holds would weaken every gate."""
    version, cases = _cases()

    with pytest.raises(EvaluationFixtureError):
        build_report(cases=cases, observations=[], manifest_version=version, adapter="fake")


@pytest.mark.unit
def test_a_case_whose_bytes_were_altered_is_refused(tmp_path: Path) -> None:
    """The corpus is a baseline, so it must be hard to move without saying so."""
    manifest = json.loads((CASES_DIRECTORY / "manifest.json").read_text())
    entry = manifest["cases"][0]
    (tmp_path / "cases").mkdir()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / entry["path"]).write_text('{"case_id": "tampered"}')

    with pytest.raises(EvaluationFixtureError):
        load_cases(tmp_path)


@pytest.mark.unit
def test_the_vision_backed_selection_also_meets_every_gate() -> None:
    """The path that consults a vision model must not be the one nobody measured."""
    version, cases = _cases()

    report = build_report(
        cases=cases,
        observations=[
            BrollObservation(
                case_id=case.case_id,
                selected=select_with_reranker(
                    case,
                    reranker=DeterministicVisualReranker(frames=fake_vision_for(case)),
                ),
            )
            for case in cases
        ],
        manifest_version=version,
        adapter="fake-vision",
        reranker_model="fake-vision",
        reranker_model_version="1",
    )

    assert report.passed
    assert report.as_document()["reranker_model"] == "fake-vision"


@pytest.mark.unit
def test_a_report_names_every_gate_it_violated() -> None:
    """A failing run must say which promise it broke, not merely that it failed."""
    version, cases = _cases()
    case = cases[0]
    wrong = next(
        labeled.candidate
        for labeled in case.candidates
        if labeled.label is CandidateLabel.IRRELEVANT
    )

    report = build_report(
        cases=[case],
        observations=[BrollObservation(case_id=case.case_id, selected=(wrong,))],
        manifest_version=version,
        adapter="fake",
    )

    assert report.passed is False
    assert report.as_document()["violations"] == [GATE_TOP_THREE_RELEVANCE]


@pytest.mark.unit
def test_the_generator_reproduces_the_checked_in_corpus_exactly() -> None:
    """A corpus nobody can regenerate is a corpus nobody can review."""
    import sys
    from hashlib import sha256

    sys.path.insert(0, str(CASES_DIRECTORY.parents[1]))
    from evals.broll.generate import build_cases

    manifest = json.loads((CASES_DIRECTORY / "manifest.json").read_text())
    digests = {entry["case_id"]: entry["sha256"] for entry in manifest["cases"]}

    for case in build_cases():
        body = json.dumps(case, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        assert sha256(body.encode("utf-8")).hexdigest() == digests[case["case_id"]]


@pytest.mark.unit
def test_a_selection_outside_the_case_pool_is_neither_relevant_nor_unsafe() -> None:
    """A picture the reviewer never saw cannot be scored as one they approved."""
    _, cases = _cases()
    stranger = cases[1].candidates[0].candidate

    metrics = score_case(cases[0], BrollObservation(case_id=cases[0].case_id, selected=(stranger,)))

    assert metrics.top_three_relevance == 0.0
    assert metrics.unsafe_selections == 0


@pytest.mark.unit
def test_a_manifest_with_no_version_is_refused(tmp_path: Path) -> None:
    """An unversioned baseline cannot be compared against a later run."""
    (tmp_path / "manifest.json").write_text(json.dumps({"cases": [{"path": "x", "sha256": "y"}]}))

    with pytest.raises(EvaluationFixtureError):
        load_cases(tmp_path)


@pytest.mark.unit
def test_a_manifest_with_no_cases_is_refused(tmp_path: Path) -> None:
    """An empty corpus would pass every gate while measuring nothing."""
    (tmp_path / "manifest.json").write_text(json.dumps({"version": "v1", "cases": []}))

    with pytest.raises(EvaluationFixtureError):
        load_cases(tmp_path)


@pytest.mark.unit
def test_a_manifest_entry_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    """Silently skipping a malformed entry would quietly shrink the corpus."""
    manifest = json.loads((CASES_DIRECTORY / "manifest.json").read_text())
    manifest["cases"] = [manifest["cases"][0], "not an entry"]
    (tmp_path / "cases").mkdir()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    entry = manifest["cases"][0]
    (tmp_path / entry["path"]).write_bytes((CASES_DIRECTORY / entry["path"]).read_bytes())

    with pytest.raises(EvaluationFixtureError):
        load_cases(tmp_path)


@pytest.mark.unit
def test_a_malformed_case_document_is_refused(tmp_path: Path) -> None:
    """A case missing its intent cannot be scored, so it must not be scored as clean."""
    from hashlib import sha256

    body = json.dumps({"case_id": "broken", "language": "en"})
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "broken.json").write_text(body)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "cases": [
                    {
                        "path": "cases/broken.json",
                        "sha256": sha256(body.encode()).hexdigest(),
                    }
                ],
            }
        )
    )

    with pytest.raises(EvaluationFixtureError):
        load_cases(tmp_path)


@pytest.mark.unit
def test_a_run_in_which_nothing_was_selected_reports_a_clean_rate() -> None:
    """Weighting by selections must not divide by zero when there were none."""
    _, cases = _cases()

    combined = aggregate(
        [
            score_case(case, BrollObservation(case_id=case.case_id, selected=()))
            for case in cases[:2]
        ]
    )

    assert combined.selection_count == 0
    assert combined.provenance_completeness == 1.0
    assert combined.violations == ()


@pytest.mark.unit
def test_observations_are_produced_for_every_case_a_runner_is_given() -> None:
    """The runner's one job is to observe each case exactly once."""
    from clipah.broll.evaluation import observations_from

    _, cases = _cases()

    observations = observations_from(cases, select=select_with_production_reranker)

    assert [observation.case_id for observation in observations] == [case.case_id for case in cases]
