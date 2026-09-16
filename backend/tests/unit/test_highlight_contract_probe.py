"""Negative controls for the experimental boundary resolver, never the live pipeline."""

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_candidate_validation import TRANSCRIPT, _payload

from clipah.highlights.extractor import CandidateValidationError
from clipah.highlights.windowing import build_windows

# Evaluation tools are deliberately outside the installed production package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.highlights.contract_probe import (
    context_flags,
    expand_span,
    measure,
    merge_metadata,
    request,
    resolve,
    segments,
    sentence_refusal,
    sentences,
)


def _proof(**changes):
    """Hand-check a thirty-second range on the existing eight-word fixture."""
    payload = _payload()
    del payload["transcript_excerpt"]
    payload.update(first_words="Growth stalled until we", last_words="Growth stalled until we")
    return payload | changes


def test_short_proof_resolves_full_canonical_excerpt():
    """A proof authorizes only the selected span, whose full text comes from storage."""
    draft = resolve(_proof(), "proof", TRANSCRIPT, build_windows(TRANSCRIPT)[0])
    assert (draft.start_ms, draft.end_ms) == (0, 30_000)
    assert draft.transcript_excerpt == _payload()["transcript_excerpt"]


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"first_words": "stalled until we cut"}, "PROBE_BOUNDARY_MISMATCH"),
        ({"last_words": "we cut the onboarding"}, "PROBE_BOUNDARY_MISMATCH"),
        ({"first_words": ""}, "PROBE_BOUNDARY_MISMATCH"),
        ({"start_word_id": "missing"}, "PROBE_OUTSIDE_WINDOW"),
        ({"start_word_id": "w000061"}, "CANDIDATE_RANGE_REVERSED"),
        ({"end_word_id": "w000038"}, "CANDIDATE_DURATION_OUT_OF_RANGE"),
        ({"end_word_id": "w000182"}, "CANDIDATE_DURATION_OUT_OF_RANGE"),
    ],
)
def test_short_proof_rejects_inconsistent_boundaries(changes, code):
    """Matching words elsewhere cannot repair a wrong endpoint or duration."""
    with pytest.raises(CandidateValidationError, match=code):
        resolve(_proof(**changes), "proof", TRANSCRIPT, build_windows(TRANSCRIPT)[0])


def test_proof_cannot_select_a_word_outside_its_window():
    """A real transcript ID outside the offered window is still forbidden."""
    window = replace(build_windows(TRANSCRIPT)[0], word_ids=("w000001", "w000002"))
    with pytest.raises(CandidateValidationError, match="PROBE_OUTSIDE_WINDOW"):
        resolve(_proof(), "proof", TRANSCRIPT, window)


def test_segments_cover_every_word_once_at_natural_boundaries():
    """Segment labels must map to an exhaustive partition, without gaps or overlap."""
    groups = segments(TRANSCRIPT, build_windows(TRANSCRIPT)[0])
    assert (groups[0]["start_word_id"], groups[0]["end_word_id"]) == ("w000001", "w000008")
    assert sum(group["word_count"] for group in groups) == 360
    assert groups[-1]["end_word_id"] == "w000360"


def test_segment_resolution_checks_proof_before_building_a_candidate():
    """Segment selection must not bypass the independent endpoint cross-check."""
    payload = _proof()
    del payload["start_word_id"], payload["end_word_id"]
    payload.update(start_segment="s0", end_segment="s7", last_words="cut the onboarding form")
    draft = resolve(payload, "segments", TRANSCRIPT, build_windows(TRANSCRIPT)[0])
    assert (draft.start_word_id, draft.end_word_id, draft.duration_ms) == (
        "w000001",
        "w000064",
        32_000,
    )
    with pytest.raises(CandidateValidationError, match="PROBE_BOUNDARY_MISMATCH"):
        resolve(
            payload | {"last_words": "Growth stalled until we"},
            "segments",
            TRANSCRIPT,
            build_windows(TRANSCRIPT)[0],
        )


def test_unknown_segment_is_refused():
    """A model cannot invent a segment identifier."""
    with pytest.raises(CandidateValidationError, match="PROBE_UNKNOWN_SEGMENT"):
        resolve(
            {"start_segment": "no", "end_segment": "s0"},
            "segments",
            TRANSCRIPT,
            build_windows(TRANSCRIPT)[0],
        )


def test_duplicate_candidates_do_not_inflate_survivor_count():
    """Repeated proposals cannot satisfy the three-survivor analysis requirement."""
    result = measure([_proof()] * 5, "proof", TRANSCRIPT, build_windows(TRANSCRIPT)[0])
    assert (result["proposed"], result["accepted"], result["survivors"]) == (5, 5, 1)
    assert not result["minimum_met"]


def test_baseline_still_rejects_a_wrong_full_excerpt():
    """The control arm must continue exercising the production quote guard."""
    with pytest.raises(CandidateValidationError, match="CANDIDATE_EXCERPT_MISMATCH"):
        resolve(
            _payload(transcript_excerpt="Growth stalled"),
            "baseline",
            TRANSCRIPT,
            build_windows(TRANSCRIPT)[0],
        )


@pytest.mark.parametrize("boundary", ["speaker", "silence", "cap"])
def test_segments_split_unpunctuated_speech_without_losing_words(boundary):
    """No punctuation must not yield an unlimited segment or skip a speaker change."""
    words = tuple(replace(word, punctuation="") for word in TRANSCRIPT.words[:25])
    if boundary == "speaker":
        words = tuple(replace(word, speaker="B" if i >= 5 else "A") for i, word in enumerate(words))
    if boundary == "silence":
        words = tuple(
            replace(
                word,
                start_ms=word.start_ms + (1000 if i >= 5 else 0),
                end_ms=word.end_ms + (1000 if i >= 5 else 0),
            )
            for i, word in enumerate(words)
        )
    transcript = replace(TRANSCRIPT, words=words)
    groups = segments(transcript, build_windows(transcript)[0])
    expected = "w000012" if boundary == "cap" else "w000005"
    assert groups[0]["end_word_id"] == expected
    assert groups[-1]["end_word_id"] == "w000025"
    assert sum(group["word_count"] for group in groups) == 25


def _window():
    """The fixture's first window."""
    return build_windows(TRANSCRIPT)[0]


def test_sentences_offer_only_ends_that_make_a_valid_duration():
    """Each start lists exactly the ends that keep a clip within 20 to 90 seconds."""
    groups = sentences(TRANSCRIPT, _window())
    assert (groups[0]["start_word_id"], groups[0]["end_word_id"]) == ("w000001", "w000008")
    assert sum(group["word_count"] for group in groups) == 360
    # Every fixture sentence lasts four seconds: S4 ends at 20 s, S21 at 88 s, S22 at 92 s.
    assert groups[0]["ends"] == [f"S{index}" for index in range(4, 22)]
    assert groups[-1]["ends"] == []


def test_sentences_end_at_quoted_terminal_punctuation_but_not_commas():
    """A closing quote after a full stop still ends a sentence; a comma never does."""
    words = tuple(
        replace(word, punctuation='."' if i == 2 else "," if i == 5 else "")
        for i, word in enumerate(TRANSCRIPT.words[:25])
    )
    transcript = replace(TRANSCRIPT, words=words)
    groups = sentences(transcript, build_windows(transcript)[0])
    assert [group["end_word_id"] for group in groups] == ["w000003", "w000025"]


def test_sentence_selection_resolves_canonical_span_without_an_echo():
    """Closed labels are resolved locally; the excerpt always comes from storage."""
    payload = _payload()
    for name in ("start_word_id", "end_word_id", "transcript_excerpt"):
        del payload[name]
    payload.update(start_sentence="S0", end_sentence="S7")
    for variant in ("sentences", "sentences_split"):
        draft = resolve(payload, variant, TRANSCRIPT, _window())
        assert (draft.start_word_id, draft.end_word_id, draft.duration_ms) == (
            "w000001",
            "w000064",
            32_000,
        )
        assert draft.transcript_excerpt.startswith("Growth stalled until we cut")


@pytest.mark.parametrize(
    "start,end,code",
    [
        ("S99", "S7", "PROBE_UNKNOWN_SEGMENT"),
        ("S7", "S0", "CANDIDATE_RANGE_REVERSED"),
        ("S0", "S2", "CANDIDATE_DURATION_OUT_OF_RANGE"),
        ("S0", "S30", "CANDIDATE_DURATION_OUT_OF_RANGE"),
    ],
)
def test_sentence_selection_keeps_every_local_refusal(start, end, code):
    """A closed label set does not excuse an invalid pairing of two labels."""
    with pytest.raises(CandidateValidationError, match=code):
        resolve(
            {"start_sentence": start, "end_sentence": end},
            "sentences",
            TRANSCRIPT,
            _window(),
        )


def test_sentence_requests_constrain_labels_to_offered_sentences():
    """Start labels are only sentences with a valid end; the split call carries no metadata."""
    single = request("sentences", TRANSCRIPT, _window())
    item = single["response_format"]["json_schema"]["schema"]["properties"]["candidates"]["items"]
    starts = item["properties"]["start_sentence"]["enum"]
    assert "S0" in starts and "S44" not in starts
    assert "transcript_excerpt" not in item["properties"]
    assert "hook" in item["properties"]
    split = request("sentences_split", TRANSCRIPT, _window())
    item = split["response_format"]["json_schema"]["schema"]["properties"]["candidates"]["items"]
    assert sorted(item["properties"]) == ["end_sentence", "reason", "start_sentence"]


def test_metadata_is_merged_by_position_and_missing_metadata_is_refused():
    """A metadata reply cannot move a span, and an unanswered span does not survive."""
    selections = [
        {"start_sentence": "S0", "end_sentence": "S7", "reason": "a"},
        {"start_sentence": "S10", "end_sentence": "S17", "reason": "b"},
    ]
    metadata = _metadata()
    merged, refusals = merge_metadata(
        selections,
        [metadata | {"position": 1, "start_sentence": "S0"}, metadata | {"position": 7}],
    )
    assert len(merged) == 1
    assert (merged[0]["start_sentence"], merged[0]["end_sentence"]) == ("S10", "S17")
    assert "position" not in merged[0]
    assert refusals == {"PROBE_METADATA_MISSING": 1}


def test_context_flags_mark_connector_openings_and_open_endings():
    """Mechanically valid spans are still flagged when they read as partial thoughts."""
    draft = resolve(
        {"start_sentence": "S0", "end_sentence": "S7"} | _metadata(),
        "sentences",
        TRANSCRIPT,
        _window(),
    )
    assert context_flags(draft, TRANSCRIPT) == []
    words = tuple(
        replace(
            word,
            text="And" if i == 0 else word.text,
            punctuation="" if i == 63 else word.punctuation,
        )
        for i, word in enumerate(TRANSCRIPT.words)
    )
    assert context_flags(draft, replace(TRANSCRIPT, words=words)) == [
        "opens_with_connector",
        "open_ending",
    ]


def _metadata():
    """Every proposal field except the boundary contract."""
    return {k: v for k, v in _payload().items() if not k.endswith(("_id", "_excerpt"))}


@pytest.mark.parametrize(
    "start,end,code",
    [
        ("S0", "S7", None),
        ("S0", "S99", "PROBE_UNKNOWN_SEGMENT"),
        ("S7", "S0", "CANDIDATE_RANGE_REVERSED"),
        ("S0", "S2", "CANDIDATE_DURATION_OUT_OF_RANGE"),
        ("S0", "S22", "CANDIDATE_DURATION_OUT_OF_RANGE"),
    ],
)
def test_split_arm_refuses_a_span_before_asking_for_its_metadata(start, end, code):
    """The pre-metadata check must agree with the resolver, so no refusal is paid for twice."""
    selection = {"start_sentence": start, "end_sentence": end}
    assert sentence_refusal(selection, sentences(TRANSCRIPT, _window())) == code


def test_span_labels_are_exactly_the_duration_valid_pairs():
    """The spans arm offers no pair that could fail duration, and expands to both labels."""
    call = request("spans_split", TRANSCRIPT, _window())
    item = call["response_format"]["json_schema"]["schema"]["properties"]["candidates"]["items"]
    labels = item["properties"]["span"]["enum"]
    groups = sentences(TRANSCRIPT, _window())
    assert sorted(item["properties"]) == ["reason", "span"]
    assert len(labels) == sum(len(group["ends"]) for group in groups)
    assert "S0-S4" in labels and "S0-S3" not in labels and "S0-S22" not in labels
    for label in labels:
        assert sentence_refusal(expand_span({"span": label}), groups) is None
    assert expand_span({"span": "S0-S7", "reason": "r"}) == {
        "reason": "r",
        "start_sentence": "S0",
        "end_sentence": "S7",
    }


def test_plain_pair_field_lists_pairs_in_prompt_but_leaves_checking_local():
    """An unlisted pair is not a schema refusal of the whole reply; it is dropped locally."""
    call = request("pairs_split", TRANSCRIPT, _window())
    item = call["response_format"]["json_schema"]["schema"]["properties"]["candidates"]["items"]
    assert item["properties"]["span"] == {"type": "string"}
    assert "S0-S4" in call["messages"][1]["content"]
    groups = sentences(TRANSCRIPT, _window())
    assert sentence_refusal(expand_span({"span": "S0-S22"}), groups) == (
        "CANDIDATE_DURATION_OUT_OF_RANGE"
    )
    assert sentence_refusal(expand_span({"span": "S0 to S7"}), groups) == "PROBE_UNKNOWN_SEGMENT"


def test_openrouter_client_sends_the_schema_as_a_required_tool_when_asked():
    """Models without JSON-schema mode get the same schema as a forced tool call."""
    import httpx
    from evals.highlights.contract_probe import OpenRouterClient

    sent = {}

    def handler(http_request):
        sent.update(json.loads(http_request.content))
        return httpx.Response(
            200,
            json={
                "id": "gen-1",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [{"function": {"arguments": '{"candidates": []}'}}],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    client = OpenRouterClient("key", tools=True, transport=httpx.MockTransport(handler))
    call = request("pairs_split", TRANSCRIPT, _window())
    response = client.chat.completions.create(model="m", **call)
    assert "response_format" not in sent
    assert sent["tool_choice"] == {"type": "function", "function": {"name": "contract_probe"}}
    assert (
        sent["tools"][0]["function"]["parameters"]
        == call["response_format"]["json_schema"]["schema"]
    )
    assert sent["max_tokens"] == call["max_completion_tokens"]
    assert "reasoning_effort" not in sent
    assert "max_completion_tokens" not in sent
    OpenRouterClient(
        "key", tools=True, max_tokens=8000, transport=httpx.MockTransport(handler)
    ).chat.completions.create(model="m", **call)
    assert sent["max_tokens"] == 8000
    assert response.choices[0].message.content == '{"candidates": []}'
    assert response.usage.model_dump() == {"prompt_tokens": 3, "completion_tokens": 2}


def test_openrouter_client_exposes_the_status_of_a_refused_call():
    """Rate limits must surface as a status code so the runner stops or waits."""
    import httpx
    from evals.highlights.contract_probe import OpenRouterClient

    client = OpenRouterClient(
        "key",
        tools=False,
        transport=httpx.MockTransport(lambda _: httpx.Response(429, json={"error": {}})),
    )
    with pytest.raises(Exception) as caught:
        client.chat.completions.create(model="m", **request("pairs_split", TRANSCRIPT, _window()))
    assert caught.value.status_code == 429


def test_gemini_client_maps_one_request_and_reply_to_the_probe_shape():
    """The system prompt, schema, ceiling, and reply text must survive the translation."""
    import httpx
    from evals.highlights.contract_probe import GeminiClient

    sent = {}

    def handler(http_request):
        sent.update(json.loads(http_request.content))
        sent["url"] = str(http_request.url)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": '{"candi'}, {"text": 'dates": []}'}]}}
                ],
                "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 5},
            },
        )

    client = GeminiClient("key", max_tokens=8000, transport=httpx.MockTransport(handler))
    call = request("pairs_split", TRANSCRIPT, _window())
    response = client.chat.completions.create(model="gemini-3.5-flash-lite", **call)
    assert "gemini-3.5-flash-lite:generateContent" in sent["url"]
    assert "key=" not in sent["url"]  # the key travels in a header, not the query string
    assert sent["systemInstruction"]["parts"][0]["text"] == call["messages"][0]["content"]
    assert sent["contents"][0]["parts"][0]["text"] == call["messages"][1]["content"]
    config = sent["generationConfig"]
    assert config["maxOutputTokens"] == 8000
    assert config["temperature"] == 0
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == call["response_format"]["json_schema"]["schema"]
    assert response.choices[0].message.content == '{"candidates": []}'
    assert response.usage.model_dump() == {"prompt_tokens": 7, "completion_tokens": 5}


def test_gemini_client_reports_a_refusal_with_its_status():
    """A quota refusal must carry a status the runner can stop on."""
    import httpx
    from evals.highlights.contract_probe import GeminiClient

    client = GeminiClient(
        "key",
        transport=httpx.MockTransport(lambda _: httpx.Response(429, json={"error": {"code": 429}})),
    )
    with pytest.raises(Exception) as caught:
        client.chat.completions.create(model="m", **request("pairs_split", TRANSCRIPT, _window()))
    assert caught.value.status_code == 429


def test_whole_transcript_is_one_window_over_every_word():
    """Without chunking, one window must carry every word once, in order."""
    from evals.highlights.contract_probe import whole_window

    window = whole_window(TRANSCRIPT)
    assert window.index == 0
    assert window.word_ids == tuple(word.word_id for word in TRANSCRIPT.words)
    assert (window.start_ms, window.end_ms) == (
        TRANSCRIPT.words[0].start_ms,
        TRANSCRIPT.words[-1].end_ms,
    )
    assert window.text.startswith("Growth stalled until we")
    groups = sentences(TRANSCRIPT, window)
    assert sum(group["word_count"] for group in groups) == len(TRANSCRIPT.words)


def test_target_count_reaches_every_arm_of_the_request():
    """A request must ask for the number of moments the run was configured for."""
    for variant in ("baseline", "sentences", "pairs_split"):
        call = request(variant, TRANSCRIPT, _window(), target_count=12)
        assert '"target_count": 12' in call["messages"][1]["content"]
