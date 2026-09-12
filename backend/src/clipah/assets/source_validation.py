"""Strict YouTube URL normalization and DNS destination policy."""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable, Sequence
from urllib.parse import parse_qs, urlsplit

from clipah.assets.youtube import NormalizedYouTubeUrl, SourceUnsupportedError

Resolver = Callable[[str], Iterable[str]]
ALLOWED_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$", flags=re.ASCII)
MAX_SOURCE_REDIRECTS = 5


def validate_youtube_url(url: str, *, resolver: Resolver | None = None) -> NormalizedYouTubeUrl:
    """Normalize one allowlisted single-video URL after validating all DNS answers."""
    host, video_id = normalize_youtube_syntax(url)
    addresses = _resolve_public_addresses(host, resolver=resolver or resolve_host)
    return NormalizedYouTubeUrl(
        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        video_id=video_id,
        host=host,
        addresses=addresses,
    )


def revalidate_youtube_url(
    source: NormalizedYouTubeUrl, *, resolver: Resolver | None = None
) -> NormalizedYouTubeUrl:
    """Resolve a validated source again and reject any complete-set DNS change."""
    _, video_id = normalize_youtube_syntax(source.canonical_url)
    if source.host not in ALLOWED_YOUTUBE_HOSTS or video_id != source.video_id:
        raise SourceUnsupportedError
    addresses = _resolve_public_addresses(source.host, resolver=resolver or resolve_host)
    if addresses != source.addresses:
        raise SourceUnsupportedError
    return NormalizedYouTubeUrl(
        canonical_url=source.canonical_url,
        video_id=source.video_id,
        host=source.host,
        addresses=addresses,
    )


def validate_redirect_chain(
    source: NormalizedYouTubeUrl,
    redirects: Sequence[str],
    *,
    resolver: Resolver | None = None,
) -> NormalizedYouTubeUrl:
    """Validate every bounded source redirect without allowing video identity changes."""
    if len(redirects) > MAX_SOURCE_REDIRECTS:
        raise SourceUnsupportedError
    current = source
    for redirect in redirects:
        current = validate_youtube_url(redirect, resolver=resolver)
        if current.video_id != source.video_id:
            raise SourceUnsupportedError
    return current


def normalize_youtube_syntax(url: str) -> tuple[str, str]:
    """Extract an exact allowlisted host and unambiguous eleven-character video ID."""
    try:
        parsed = urlsplit(url)
        host_value = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise SourceUnsupportedError from error
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or host_value is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise SourceUnsupportedError
    if not host_value.isascii():
        raise SourceUnsupportedError
    host = host_value.lower().removesuffix(".")
    if host not in ALLOWED_YOUTUBE_HOSTS:
        raise SourceUnsupportedError

    query = parse_qs(parsed.query, keep_blank_values=True)
    if "list" in query:
        raise SourceUnsupportedError
    path_parts = tuple(part for part in parsed.path.split("/") if part)
    if host == "youtu.be":
        if len(path_parts) != 1 or "v" in query:
            raise SourceUnsupportedError
        video_id = path_parts[0]
    elif parsed.path == "/watch":
        values = query.get("v", [])
        if len(values) != 1:
            raise SourceUnsupportedError
        video_id = values[0]
    elif len(path_parts) == 2 and path_parts[0] in {"shorts", "embed", "live"}:
        if "v" in query:
            raise SourceUnsupportedError
        video_id = path_parts[1]
    else:
        raise SourceUnsupportedError
    if VIDEO_ID_PATTERN.fullmatch(video_id) is None:
        raise SourceUnsupportedError
    return host, video_id


def is_public_address(value: str) -> bool:
    """Say whether one textual address is globally routable, for any caller that fetches.

    The provider-media policy in `provider_fetch` judges its destinations by exactly this
    rule, so the rule lives once rather than being restated where it would drift.
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return _is_public_address(address)


def resolve_host(host: str) -> Iterable[str]:
    """Resolve one HTTPS host into unique numeric addresses without provider I/O."""
    return {
        str(sockaddr[0])
        for _, _, _, _, sockaddr in socket.getaddrinfo(
            host, 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    }


def _resolve_public_addresses(
    host: str, *, resolver: Resolver
) -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Require a non-empty DNS answer containing only globally routable addresses."""
    try:
        raw_addresses = tuple(resolver(host))
        addresses = frozenset(ipaddress.ip_address(value) for value in raw_addresses)
    except (OSError, TypeError, ValueError) as error:
        raise SourceUnsupportedError from error
    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise SourceUnsupportedError
    return addresses


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject every special-use category, including private IPv4 mapped into IPv6."""
    classified: ipaddress.IPv4Address | ipaddress.IPv6Address = address
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        classified = address.ipv4_mapped
    return bool(
        classified.is_global
        and not classified.is_private
        and not classified.is_loopback
        and not classified.is_link_local
        and not classified.is_multicast
        and not classified.is_reserved
        and not classified.is_unspecified
    )
