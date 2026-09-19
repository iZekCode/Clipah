"""Suggesting a Project name from a YouTube video's public title."""

from __future__ import annotations

import httpx

from clipah.source_imports.titles import MAX_TITLE_LENGTH, oembed_title

VIDEO_ID = "dQw4w9WgXcQ"


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_the_title_comes_from_youtubes_public_oembed_endpoint() -> None:
    """Only YouTube's own host is ever asked, about the canonical watch address."""
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"title": "  Episode 42: the interview  "})

    title = oembed_title(VIDEO_ID, client=_client(httpx.MockTransport(answer)))

    assert title == "Episode 42: the interview"
    assert seen[0].url.host == "www.youtube.com"
    assert seen[0].url.path == "/oembed"
    assert seen[0].url.params["url"] == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert seen[0].url.params["format"] == "json"


def test_a_video_youtube_will_not_describe_has_no_title() -> None:
    """Private, removed, and embedding-disabled videos answer 401, 403, or 404."""
    for status in (401, 403, 404, 500):
        client = _client(httpx.MockTransport(lambda _, status=status: httpx.Response(status)))
        assert oembed_title(VIDEO_ID, client=client) is None


def test_an_unreadable_or_empty_answer_has_no_title() -> None:
    """Anything other than a non-blank string title is treated as no suggestion."""
    for body in ({"title": ""}, {"title": "   "}, {"title": 42}, {}, ["title"]):
        client = _client(httpx.MockTransport(lambda _, body=body: httpx.Response(200, json=body)))
        assert oembed_title(VIDEO_ID, client=client) is None
    garbled = _client(httpx.MockTransport(lambda _: httpx.Response(200, content=b"<html>")))
    assert oembed_title(VIDEO_ID, client=garbled) is None


def test_an_unreachable_youtube_has_no_title() -> None:
    """A suggestion is a convenience, so a network failure never becomes an error."""

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    assert oembed_title(VIDEO_ID, client=_client(httpx.MockTransport(fail))) is None


def test_a_very_long_title_is_cut_to_what_a_project_name_holds() -> None:
    """The name field accepts at most this many characters."""
    client = _client(httpx.MockTransport(lambda _: httpx.Response(200, json={"title": "a" * 500})))

    title = oembed_title(VIDEO_ID, client=client)

    assert title is not None
    assert len(title) == MAX_TITLE_LENGTH
