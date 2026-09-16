"""Experimental candidate contracts; never imported by the production pipeline.

This spike compares representations, not provider quality or release readiness. Raw reports
contain transcript excerpts and belong in a private local directory, never in version control.
Run from backend with ``uv run python -m evals.highlights.contract_probe --help``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from groq import Groq

from clipah.highlights.deduplicate import deduplicate
from clipah.highlights.extractor import CandidateValidationError, validate_candidate
from clipah.highlights.groq_adapter import (
    _EXTRACTION_SYSTEM_PROMPT,
    _extraction_prompt,
    candidate_json_schema,
)
from clipah.highlights.models import ClipCandidateDraft, TranscriptWindow
from clipah.highlights.windowing import build_windows, single_window, window_text
from clipah.transcripts.models import TranscriptResult, TranscriptWord

VARIANTS = (
    "baseline",
    "proof",
    "segments",
    "sentences",
    "sentences_split",
    "spans_split",
    "pairs_split",
)
SENTENCE_VARIANTS = ("sentences", "sentences_split", "spans_split", "pairs_split")
SPLIT_VARIANTS = ("sentences_split", "spans_split", "pairs_split")
PAIR_VARIANTS = ("spans_split", "pairs_split")
TERMINAL = (".", "!", "?")
CLOSERS = "\"')\u201d\u2019"
# Openers that usually point back at context outside the clip. A flag, never a refusal.
CONNECTORS = {
    "en": {"and", "but", "so", "because", "that", "this", "which", "then", "also", "or"},
    "id": {"dan", "tapi", "jadi", "karena", "itu", "ini", "terus", "juga", "makanya", "nah"},
}
METADATA_SYSTEM_PROMPT = (
    "You describe short-form clips whose boundaries are already fixed. For every clip "
    "position, write metadata from its transcript only, and report every context "
    "dependency or warning, such as an opening that refers to something said earlier "
    "or an ending that stops before the thought is finished."
)


def segments(transcript: TranscriptResult, window: TranscriptWindow) -> list[dict[str, Any]]:
    """Partition every offered word at punctuation, speaker changes, pauses, or 12 words."""
    offered = set(window.word_ids)
    words = tuple(word for word in transcript.words if word.word_id in offered)
    groups: list[dict[str, Any]] = []
    start = 0
    for index, word in enumerate(words):
        following = words[index + 1] if index + 1 < len(words) else None
        if (
            following is None
            or word.punctuation in {".", "!", "?", ";", ":", ","}
            or following.speaker != word.speaker
            or following.start_ms - word.end_ms >= 800
            or index - start + 1 >= 12
        ):
            span = words[start : index + 1]
            groups.append(
                {
                    "id": f"s{len(groups)}",
                    "start_word_id": span[0].word_id,
                    "end_word_id": span[-1].word_id,
                    "start_ms": span[0].start_ms,
                    "end_ms": span[-1].end_ms,
                    "word_count": len(span),
                    "text": window_text(span),
                }
            )
            start = index + 1
    return groups


def sentences(transcript: TranscriptResult, window: TranscriptWindow) -> list[dict[str, Any]]:
    """Partition offered words into sentences and list, per start, every duration-valid end.

    Sentences end at terminal punctuation, a speaker change, or a pause of at least 800 ms.
    Because the ends are computed here, a model choosing from them never does arithmetic.
    """
    offered = set(window.word_ids)
    words = tuple(word for word in transcript.words if word.word_id in offered)
    groups: list[dict[str, Any]] = []
    start = 0
    for index, word in enumerate(words):
        following = words[index + 1] if index + 1 < len(words) else None
        if (
            following is None
            or word.punctuation.rstrip(CLOSERS).endswith(TERMINAL)
            or following.speaker != word.speaker
            or following.start_ms - word.end_ms >= 800
        ):
            span = words[start : index + 1]
            groups.append(
                {
                    "id": f"S{len(groups)}",
                    "start_word_id": span[0].word_id,
                    "end_word_id": span[-1].word_id,
                    "start_ms": span[0].start_ms,
                    "end_ms": span[-1].end_ms,
                    "word_count": len(span),
                    "text": window_text(span),
                }
            )
            start = index + 1
    for group in groups:
        group["ends"] = [
            other["id"]
            for other in groups
            if other["start_ms"] >= group["start_ms"]
            and 20_000 <= other["end_ms"] - group["start_ms"] <= 90_000
        ]
    return groups


def context_flags(draft: ClipCandidateDraft, transcript: TranscriptResult) -> list[str]:
    """Flag spans that open on a connector or end without terminal punctuation."""
    by_id = {word.word_id: word for word in transcript.words}
    first, last = by_id[draft.start_word_id], by_id[draft.end_word_id]
    flags = []
    if tokens(first.text)[:1] and tokens(first.text)[0] in CONNECTORS.get(transcript.language, ()):
        flags.append("opens_with_connector")
    if not last.punctuation.rstrip(CLOSERS).endswith(TERMINAL):
        flags.append("open_ending")
    return flags


def merge_metadata(
    selections: list[dict[str, Any]], metadata: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Attach metadata by position; boundaries always come from the selection, never the reply."""
    by_position: dict[int, dict[str, Any]] = {}
    for item in metadata:
        position = item.get("position")
        if isinstance(position, int) and position not in by_position:
            by_position[position] = item
    merged, refusals = [], Counter()
    for position, selection in enumerate(selections):
        item = by_position.get(position)
        if item is None:
            refusals["PROBE_METADATA_MISSING"] += 1
            continue
        described = {
            key: value
            for key, value in item.items()
            if key not in {"position", "reason", "start_sentence", "end_sentence"}
        }
        merged.append(described | selection)
    return merged, refusals


def tokens(text: str) -> list[str]:
    """Normalize typography without fuzzy matching, dropping, or relocating words."""
    return re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFKC", text)).casefold().split()


def resolve(
    payload: dict[str, Any], variant: str, transcript: TranscriptResult, window: TranscriptWindow
) -> ClipCandidateDraft:
    """Check endpoint evidence before deriving the full canonical excerpt for validation."""
    proposal = dict(payload)
    if variant == "segments":
        lookup = {group["id"]: group for group in segments(transcript, window)}
        start = lookup.get(proposal.pop("start_segment", None))
        end = lookup.get(proposal.pop("end_segment", None))
        if start is None or end is None:
            raise CandidateValidationError("PROBE_UNKNOWN_SEGMENT")
        proposal["start_word_id"] = start["start_word_id"]
        proposal["end_word_id"] = end["end_word_id"]
    if variant in SENTENCE_VARIANTS:
        lookup = {group["id"]: group for group in sentences(transcript, window)}
        start = lookup.get(proposal.pop("start_sentence", None))
        end = lookup.get(proposal.pop("end_sentence", None))
        if start is None or end is None:
            raise CandidateValidationError("PROBE_UNKNOWN_SEGMENT")
        proposal["start_word_id"] = start["start_word_id"]
        proposal["end_word_id"] = end["end_word_id"]
    index = {word.word_id: position for position, word in enumerate(transcript.words)}
    start_id, end_id = proposal.get("start_word_id"), proposal.get("end_word_id")
    if start_id not in window.word_ids or end_id not in window.word_ids:
        raise CandidateValidationError("PROBE_OUTSIDE_WINDOW")
    if index[end_id] < index[start_id]:
        raise CandidateValidationError("CANDIDATE_RANGE_REVERSED")
    span = transcript.words[index[start_id] : index[end_id] + 1]
    if not 20_000 <= span[-1].end_ms - span[0].start_ms <= 90_000:
        raise CandidateValidationError("CANDIDATE_DURATION_OUT_OF_RANGE")
    if variant in SENTENCE_VARIANTS:
        # Labels are a closed set built from stored text, so there is no echo to check.
        # In the split arm the metadata author reads this canonical excerpt afterwards.
        proposal["transcript_excerpt"] = window_text(span)
    elif variant != "baseline":
        for name, boundary in (("first_words", span[:4]), ("last_words", span[-4:])):
            echo = proposal.pop(name, None)
            if not isinstance(echo, str) or tokens(echo) != tokens(window_text(boundary)):
                raise CandidateValidationError("PROBE_BOUNDARY_MISMATCH")
        # This is deliberately an alternative contract, not evidence that the model
        # echoed the whole excerpt. Only the two short boundary proofs were checked.
        proposal["transcript_excerpt"] = window_text(span)
    return validate_candidate(proposal, transcript=transcript)


def whole_window(transcript: TranscriptResult) -> TranscriptWindow:
    """Offer the entire transcript as one window, using the production single-window builder."""
    return single_window(transcript)


def request(
    variant: str,
    transcript: TranscriptResult,
    window: TranscriptWindow,
    target_count: int = 5,
) -> dict[str, Any]:
    """Keep metadata and generation settings fixed while changing the boundary contract."""
    if variant in SENTENCE_VARIANTS:
        return _sentence_request(variant, transcript, window, target_count)
    schema = candidate_json_schema()
    system = _EXTRACTION_SYSTEM_PROMPT
    prompt = _extraction_prompt(window, target_count)
    if variant != "baseline":
        item = schema["properties"]["candidates"]["items"]
        properties = item["properties"]
        del properties["transcript_excerpt"]
        properties.update(first_words={"type": "string"}, last_words={"type": "string"})
        system += (
            f" Select {target_count} distinct complete moments, each between 20 and 90 seconds. "
            "Return first_words and last_words: exactly the first four and last four "
            "transcript words INSIDE the selected inclusive range, copied in order. "
            "Do not return the full excerpt. Report any incomplete thought or context risk."
        )
        if variant == "proof":
            system += " The i-th word ID belongs to the i-th transcript word."
        else:
            del properties["start_word_id"], properties["end_word_id"]
            properties.update(start_segment={"type": "string"}, end_segment={"type": "string"})
            system += (
                " Select by start_segment and end_segment labels. Include ALL segments "
                "between them. Use the supplied times to check duration as end_ms of the "
                "last segment minus start_ms of the first. Short segments may require "
                "reading across adjacent segments to copy four boundary words."
            )
            prompt = json.dumps(
                {
                    "target_count": target_count,
                    "segments": [
                        {key: group[key] for key in ("id", "start_ms", "end_ms", "text")}
                        for group in segments(transcript, window)
                    ],
                },
                ensure_ascii=False,
            )
        item["required"] = sorted(properties)
    return {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "contract_probe", "strict": True, "schema": schema},
        },
        "temperature": 0,
        "reasoning_effort": "low",
        "max_completion_tokens": 4096,
    }


def _sentence_request(
    variant: str, transcript: TranscriptResult, window: TranscriptWindow, target_count: int = 5
) -> dict[str, Any]:
    """Offer sentence labels as schema enums; the split arm asks for boundaries only."""
    groups = sentences(transcript, window)
    schema = candidate_json_schema()
    item = schema["properties"]["candidates"]["items"]
    properties = item["properties"]
    for name in ("start_word_id", "end_word_id", "transcript_excerpt"):
        del properties[name]
    boundaries = {
        "start_sentence": {"type": "string", "enum": [g["id"] for g in groups if g["ends"]]},
        "end_sentence": {"type": "string", "enum": [g["id"] for g in groups]},
    }
    pairs = [f"{g['id']}-{end}" for g in groups for end in g["ends"]]
    if variant == "spans_split":
        # One closed label per duration-valid pair: an out-of-range clip cannot be spelled.
        boundaries = {"span": {"type": "string", "enum": pairs}}
    if variant == "pairs_split":
        # Same listed pairs, but an unlisted pick is refused locally, not the whole reply.
        boundaries = {"span": {"type": "string"}}
    if variant in SPLIT_VARIANTS:
        item["properties"] = properties = boundaries | {"reason": {"type": "string"}}
    else:
        properties.update(boundaries)
    item["required"] = sorted(properties)
    system = (
        "You find complete, self-contained short-form moments in a transcript. Choose each "
        "moment as a start_sentence and an end_sentence; the clip includes every sentence "
        "between them. end_sentence MUST be one of the ends listed for that start_sentence, "
        "which are exactly the ends that give a 20 to 90 second clip. Start where the "
        "thought starts, not on a reference to something said earlier, and end where it "
        f"finishes. Return {target_count} distinct moments."
    )
    if variant in PAIR_VARIANTS:
        system = (
            "You find complete, self-contained short-form moments in a transcript. Choose "
            "each moment as one span label from allowed_spans; span Sx-Sy includes sentence "
            "Sx through sentence Sy. Every allowed span is 20 to 90 seconds long. Start where "
            "the thought starts, not on a reference to something said earlier, and end where "
            f"it finishes. Return {target_count} distinct moments."
        )
    if variant in SPLIT_VARIANTS:
        system += " Return only the boundaries and a short reason."
    else:
        system += " Report every context dependency and warning you notice."
    prompt = json.dumps(
        {
            "target_count": target_count,
            "sentences": [
                {
                    "id": g["id"],
                    "seconds": f"{g['start_ms'] / 1000:.1f}-{g['end_ms'] / 1000:.1f}",
                    "valid_ends": f"{g['ends'][0]}..{g['ends'][-1]}" if g["ends"] else "none",
                    "text": g["text"],
                }
                for g in groups
            ],
        }
        | ({"allowed_spans": " ".join(pairs)} if variant in PAIR_VARIANTS else {}),
        ensure_ascii=False,
    )
    return {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "contract_probe", "strict": True, "schema": schema},
        },
        "temperature": 0,
        "reasoning_effort": "low",
        "max_completion_tokens": 3000 if variant in SPLIT_VARIANTS else 4096,
    }


def metadata_request(
    drafts: list[tuple[str, str]], transcript: TranscriptResult, window: TranscriptWindow
) -> dict[str, Any]:
    """Ask for metadata on fixed spans, keyed by position, showing only canonical text."""
    lookup = {group["id"]: group for group in sentences(transcript, window)}
    index = {word.word_id: position for position, word in enumerate(transcript.words)}
    clips = []
    for position, (start, end) in enumerate(drafts):
        span = transcript.words[
            index[lookup[start]["start_word_id"]] : index[lookup[end]["end_word_id"]] + 1
        ]
        clips.append(
            {
                "position": position,
                "duration_seconds": round((span[-1].end_ms - span[0].start_ms) / 1000, 1),
                "transcript": window_text(span),
            }
        )
    schema = candidate_json_schema()
    item = schema["properties"]["candidates"]["items"]
    properties = item["properties"]
    for name in ("start_word_id", "end_word_id", "transcript_excerpt"):
        del properties[name]
    properties["position"] = {"type": "integer", "enum": list(range(len(drafts)))}
    item["required"] = sorted(properties)
    return {
        "messages": [
            {"role": "system", "content": METADATA_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"clips": clips}, ensure_ascii=False)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "clip_metadata", "strict": True, "schema": schema},
        },
        "temperature": 0,
        "reasoning_effort": "low",
        "max_completion_tokens": 3000,
    }


def measure(proposals, variant, transcript, window, refusals=None):
    """Count first rejection reasons and survivors without pooling independent generations."""
    accepted = []
    refusals = Counter(refusals or {})
    for proposal in proposals:
        try:
            accepted.append(resolve(proposal, variant, transcript, window))
        except CandidateValidationError as error:
            refusals[error.code] += 1
    survivors = deduplicate(accepted)
    return {
        "proposed": len(proposals),
        "accepted": len(accepted),
        "refusals": dict(refusals),
        "survivors": len(survivors),
        "minimum_met": len(survivors) >= 3,
        "context_flags": dict(
            Counter(flag for draft in survivors for flag in context_flags(draft, transcript))
        ),
        "drafts": [draft.model_dump(mode="json") for draft in survivors],
    }


def expand_span(selection: dict[str, Any]) -> dict[str, Any]:
    """Rewrite one ``Sx-Sy`` span label as the start and end sentence labels it names."""
    if "span" not in selection:
        return selection
    rest = {key: value for key, value in selection.items() if key != "span"}
    start, _, end = str(selection["span"]).partition("-")
    return rest | {"start_sentence": start, "end_sentence": end}


def sentence_refusal(selection: dict[str, Any], groups: list[dict[str, Any]]) -> str | None:
    """Apply the boundary refusals before paying for metadata on a span that cannot survive."""
    order = {group["id"]: position for position, group in enumerate(groups)}
    start, end = selection.get("start_sentence"), selection.get("end_sentence")
    if start not in order or end not in order:
        return "PROBE_UNKNOWN_SEGMENT"
    if order[end] < order[start]:
        return "CANDIDATE_RANGE_REVERSED"
    if end not in groups[order[start]]["ends"]:
        return "CANDIDATE_DURATION_OUT_OF_RANGE"
    return None


def _create(client: Groq, model: str, call: dict[str, Any], variant: str) -> Any:
    """Submit one completion, waiting out per-minute limits but never a daily limit."""
    for attempt in range(4):
        try:
            return client.chat.completions.create(model=model, **call)
        except Exception as error:
            if (
                getattr(error, "status_code", None) != 429
                or attempt == 3
                or "tokens per day" in str(error).lower()
                or "tokens per day" in json.dumps(getattr(error, "body", None)).lower()
                or "per-day" in json.dumps(getattr(error, "body", None)).lower()
            ):
                raise
            print(json.dumps({"waiting_for_rate_limit": 60, "variant": variant}), flush=True)
            time.sleep(60)
    raise AssertionError("unreachable")


def _candidates(response: Any) -> list[dict[str, Any]]:
    """Parse the one candidate list a strict-schema reply may carry."""
    proposals = json.loads(response.choices[0].message.content)["candidates"]
    if not isinstance(proposals, list) or not all(isinstance(item, dict) for item in proposals):
        raise ValueError("invalid candidates")
    return proposals


class ProviderError(Exception):
    """A refused OpenRouter call, carrying only its status code and parsed body."""

    def __init__(self, status_code: int, body: Any) -> None:
        """Keep the fields the runner inspects; never the raw text in the message."""
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.body = body


class _Record:
    """Attribute access over one parsed response, shaped like the Groq SDK objects."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        value = self._data.get(name)
        if isinstance(value, dict):
            return _Record(value)
        if isinstance(value, list):
            return [_Record(item) if isinstance(item, dict) else item for item in value]
        return value

    def model_dump(self) -> dict[str, Any]:
        """Return the underlying mapping, as usage reporting expects."""
        return self._data


class OpenRouterClient:
    """Minimal OpenAI-compatible client so the same requests can run on other models.

    With ``tools`` the strict schema is sent as a forced function call, for models whose
    endpoint offers tool calling but no JSON-schema response format. Neither mode is
    trusted: every candidate still goes through the same local refusals.
    """

    def __init__(
        self,
        api_key: str,
        *,
        tools: bool,
        max_tokens: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        # Groq's per-minute allowance forced small ceilings; reasoning models on other hosts
        # can spend most of those on reasoning and return no tool call at all.
        self._max_tokens = max_tokens
        self._tools = tools
        self._http = httpx.Client(
            base_url="https://openrouter.ai/api/v1",
            headers={"Authorization": f"Bearer {api_key}", "X-Title": "Clipah contract probe"},
            timeout=180,
            transport=transport,
        )
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, *, model: str, **call: Any) -> Any:
        """Translate one probe request, submit it, and normalize the reply."""
        body = {key: value for key, value in call.items() if key != "reasoning_effort"}
        body["model"] = model
        body["max_tokens"] = self._max_tokens or body.pop("max_completion_tokens")
        body.pop("max_completion_tokens", None)
        body["reasoning"] = {"effort": call.get("reasoning_effort", "low")}
        if self._tools:
            schema = body.pop("response_format")["json_schema"]
            body["reasoning"] = {"enabled": True}
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": schema["name"], "parameters": schema["schema"]},
                }
            ]
            body["tool_choice"] = {"type": "function", "function": {"name": schema["name"]}}
        response = self._http.post("/chat/completions", json=body)
        data = response.json() if response.content else {}
        if response.status_code != 200 or "error" in data:
            raise ProviderError(data.get("error", {}).get("code", response.status_code), data)
        message = data["choices"][0]["message"]
        if self._tools:
            calls = message.get("tool_calls") or []
            message["content"] = calls[0]["function"]["arguments"] if calls else None
        return _Record(data)


class GeminiClient:
    """Minimal Gemini client exposing the same surface the probe uses for Groq.

    The schema travels as ``responseJsonSchema`` and the key as a header, so no transcript
    or credential ever reaches a URL. Replies are still only proposals: every candidate
    goes through the same local refusals as any other provider's.
    """

    def __init__(
        self,
        api_key: str,
        *,
        max_tokens: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self._max_tokens = max_tokens
        self._http = httpx.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            headers={"x-goog-api-key": api_key},
            timeout=300,
            transport=transport,
        )
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, *, model: str, **call: Any) -> Any:
        """Translate one probe request into generateContent and normalize the reply."""
        system, user = (message["content"] for message in call["messages"])
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": call["temperature"],
                "maxOutputTokens": self._max_tokens or call["max_completion_tokens"],
                "responseMimeType": "application/json",
                "responseJsonSchema": call["response_format"]["json_schema"]["schema"],
            },
        }
        response = self._http.post(f"/models/{model}:generateContent", json=body)
        data = response.json() if response.content else {}
        if response.status_code != 200 or "error" in data:
            raise ProviderError(response.status_code, data)
        candidate = data["candidates"][0]
        text = "".join(part.get("text", "") for part in candidate["content"]["parts"])
        usage = data.get("usageMetadata", {})
        return _Record(
            {
                "id": data.get("responseId", ""),
                "choices": [
                    {"message": {"content": text}, "finish_reason": candidate.get("finishReason")}
                ],
                "usage": {
                    "prompt_tokens": usage.get("promptTokenCount"),
                    "completion_tokens": usage.get("candidatesTokenCount"),
                },
            }
        )


def save(path: Path, report: dict[str, Any]) -> None:
    """Keep raw excerpts and provider responses private and checkpoint after every call."""
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    """Run a bounded comparison on exported transcripts, explicitly opting into live Groq."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcripts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--window-index", type=int, default=0)
    parser.add_argument(
        "--whole-transcript", action="store_true", help="one window over the entire transcript"
    )
    parser.add_argument("--target-count", type=int, default=5)
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--provider", choices=("groq", "openrouter", "gemini"), default="groq")
    parser.add_argument(
        "--tools", action="store_true", help="send the schema as a forced tool call (OpenRouter)"
    )
    parser.add_argument("--max-tokens", type=int, help="override every completion ceiling")
    arguments = parser.parse_args()
    documents = json.loads(arguments.transcripts.read_text())
    settings = dotenv_values(".env")
    client: Any
    if arguments.provider == "gemini":
        client = GeminiClient(settings["GEMINI_API_KEY"], max_tokens=arguments.max_tokens)
    elif arguments.provider == "openrouter":
        client = OpenRouterClient(
            settings["OPENROUTER_API_KEY"], tools=arguments.tools, max_tokens=arguments.max_tokens
        )
    else:
        client = Groq(api_key=settings["CLIPAH_GROQ_API_KEY"], max_retries=0, timeout=120)
    report: dict[str, Any] = {
        "version": "contract-probe/1",
        "model": arguments.model,
        "provider": arguments.provider,
        "tools": arguments.tools,
        "max_tokens_override": arguments.max_tokens,
        "target_count": arguments.target_count,
        "whole_transcript": arguments.whole_transcript,
        "temperature": 0,
        "reasoning_effort": "low",
        "max_completion_tokens": 4096,
        "runs": [],
    }
    if arguments.resume:
        report = json.loads(arguments.output.read_text())
        if report["model"] != arguments.model:
            parser.error("Cannot resume with a different model")
    save(arguments.output, report)
    for document in documents:
        transcript = TranscriptResult(
            **{
                key: document[key]
                for key in (
                    "provider",
                    "provider_version",
                    "model",
                    "language",
                    "full_text",
                    "duration_ms",
                )
            },
            words=tuple(TranscriptWord(**word) for word in document["words"]),
            utterances=(),
            speaker_segments=(),
            raw_result={},
        )
        windows = (
            [whole_window(transcript)] if arguments.whole_transcript else build_windows(transcript)
        )
        if arguments.window_index >= len(windows):
            continue
        window = windows[arguments.window_index]
        for generation in range(arguments.generations):
            # Interleave variants so a service change does not affect just one arm.
            for variant in arguments.variants:
                if any(
                    run["language"] == transcript.language
                    and run["window_index"] == window.index
                    and run["generation"] == generation + 1
                    and run["variant"] == variant
                    and "error_type" not in run
                    for run in report["runs"]
                ):
                    continue
                call = request(variant, transcript, window, arguments.target_count)
                result: dict[str, Any] = {
                    "language": transcript.language,
                    "transcript_sha256": hashlib.sha256(
                        json.dumps(document["words"], sort_keys=True).encode()
                    ).hexdigest(),
                    "word_count": len(window.word_ids),
                    "window_index": window.index,
                    "total_windows": len(windows),
                    "generation": generation + 1,
                    "variant": variant,
                    "request_sha256": hashlib.sha256(
                        json.dumps(call, sort_keys=True).encode()
                    ).hexdigest(),
                }
                started = time.monotonic()
                try:
                    response = _create(client, arguments.model, call, variant)
                    proposals = _candidates(response)
                    usage = [response.usage.model_dump() if response.usage else None]
                    refusals: Counter[str] = Counter()
                    selections = proposals
                    if variant in SPLIT_VARIANTS:
                        groups = sentences(transcript, window)
                        valid = []
                        for selection in map(expand_span, selections):
                            code = sentence_refusal(selection, groups)
                            if code is None:
                                valid.append(selection)
                            else:
                                refusals[code] += 1
                        proposals = []
                        if valid:
                            time.sleep(60)
                            described = _create(
                                client,
                                arguments.model,
                                metadata_request(
                                    [(v["start_sentence"], v["end_sentence"]) for v in valid],
                                    transcript,
                                    window,
                                ),
                                variant,
                            )
                            usage.append(described.usage.model_dump() if described.usage else None)
                            proposals, missing = merge_metadata(valid, _candidates(described))
                            refusals.update(missing)
                    result.update(measure(proposals, variant, transcript, window, refusals))
                    result.update(
                        proposed=len(selections),
                        raw_proposals=selections,
                        usage=usage,
                        request_id=response.id,
                    )
                except Exception as error:
                    # Do not print exception messages: SDK exceptions can carry raw text.
                    result.update(
                        error_type=type(error).__name__,
                        status_code=getattr(error, "status_code", None),
                        error_body=getattr(error, "body", None),
                    )
                result["latency_seconds"] = round(time.monotonic() - started, 3)
                report["runs"].append(result)
                save(arguments.output, report)
                print(
                    json.dumps(
                        {
                            key: value
                            for key, value in result.items()
                            if key not in {"drafts", "raw_proposals", "error_body"}
                        }
                    ),
                    flush=True,
                )
                if result.get("status_code") in {401, 403, 429}:
                    return
                time.sleep(60)


if __name__ == "__main__":
    main()
