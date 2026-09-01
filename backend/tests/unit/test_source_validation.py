"""Unit contracts for strict YouTube URL and network-destination validation."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable

import pytest

from clipah.assets.source_validation import (
    revalidate_youtube_url,
    validate_redirect_chain,
    validate_youtube_url,
)
from clipah.assets.youtube import SourceUnsupportedError

VIDEO_ID = "dQw4w9WgXcQ"
OTHER_VIDEO_ID = "aqz-KE-bpKQ"
PUBLIC_IPV4 = "8.8.8.8"
PUBLIC_IPV6 = "2606:4700:4700::1111"


def resolver_with(*addresses: str):
    """Return a deterministic resolver containing exactly the supplied addresses."""

    def resolve(_: str) -> Iterable[str]:
        """Return exactly the DNS answers selected by this test."""
        return addresses

    return resolve


PUBLIC_RESOLVER = resolver_with(PUBLIC_IPV4, PUBLIC_IPV6)


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        f"https://youtube.com/watch?v={VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://m.youtube.com/watch?v={VIDEO_ID}",
        f"https://www.youtube.com/shorts/{VIDEO_ID}",
        f"https://www.youtube.com/embed/{VIDEO_ID}",
        f"https://www.youtube.com/live/{VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://WWW.YOUTUBE.COM./watch?v={VIDEO_ID}",
        f"https://www.youtube.com:443/watch?v={VIDEO_ID}",
    ],
)
def test_allowed_video_urls_are_canonicalized(url: str) -> None:
    """Every supported public single-video form must collapse to one canonical URL."""
    normalized = validate_youtube_url(url, resolver=PUBLIC_RESOLVER)

    assert normalized.canonical_url == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert normalized.video_id == VIDEO_ID
    assert normalized.host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
    assert {str(address) for address in normalized.addresses} == {PUBLIC_IPV4, PUBLIC_IPV6}


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        f"http://www.youtube.com/watch?v={VIDEO_ID}",
        f"ftp://www.youtube.com/watch?v={VIDEO_ID}",
        f"www.youtube.com/watch?v={VIDEO_ID}",
        f"https://user@www.youtube.com/watch?v={VIDEO_ID}",
        f"https://user:secret@www.youtube.com/watch?v={VIDEO_ID}",
        f"https://www.youtube.com:444/watch?v={VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}#fragment",
        f"https://www.youtube.com.evil.example/watch?v={VIDEO_ID}",
        f"https://youtube.com.evil.example/watch?v={VIDEO_ID}",
        f"https://notyoutube.com/watch?v={VIDEO_ID}",
        f"https://youtube.com@evil.example/watch?v={VIDEO_ID}",
        f"https://www.youtubе.com/watch?v={VIDEO_ID}",  # noqa: RUF001 - intentional lookalike
        f"https://www.youtube.com/watch?list=PL123&v={VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PL123",
        f"https://www.youtube.com/playlist?list={VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&v={OTHER_VIDEO_ID}",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?v=short",
        f"https://www.youtube.com/shorts/{VIDEO_ID}/extra",
        f"https://youtu.be/{VIDEO_ID}/extra",
        f"https://www.youtube.com/channel/{VIDEO_ID}",
        f"https://www.youtube.com/watch#v={VIDEO_ID}",
    ],
)
def test_unsafe_or_ambiguous_urls_are_rejected(url: str) -> None:
    """Syntax tricks must fail before any provider command can see the source."""
    with pytest.raises(SourceUnsupportedError, match=r"^SOURCE_UNSUPPORTED$"):
        validate_youtube_url(url, resolver=PUBLIC_RESOLVER)


@pytest.mark.unit
@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "192.0.2.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "ff02::1",
        "2001:db8::1",
        "::",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
    ],
)
def test_non_global_dns_addresses_are_rejected(address: str) -> None:
    """One unsafe result poisons the whole DNS answer set."""
    with pytest.raises(SourceUnsupportedError, match=r"^SOURCE_UNSUPPORTED$"):
        validate_youtube_url(
            f"https://youtu.be/{VIDEO_ID}", resolver=resolver_with(PUBLIC_IPV4, address)
        )


@pytest.mark.unit
def test_empty_or_malformed_dns_answers_are_rejected() -> None:
    """A hostname is unusable unless every resolver result is a valid public IP literal."""
    for resolver in (resolver_with(), resolver_with("not-an-ip")):
        with pytest.raises(SourceUnsupportedError, match=r"^SOURCE_UNSUPPORTED$"):
            validate_youtube_url(f"https://youtu.be/{VIDEO_ID}", resolver=resolver)


@pytest.mark.unit
def test_resolver_failures_are_sanitized() -> None:
    """Raw resolver diagnostics must not cross the source-domain boundary."""

    def failing_resolver(_: str) -> Iterable[str]:
        """Model a resolver diagnostic that the domain must discard."""
        raise OSError("secret resolver diagnostic")

    with pytest.raises(SourceUnsupportedError) as captured:
        validate_youtube_url(f"https://youtu.be/{VIDEO_ID}", resolver=failing_resolver)

    assert str(captured.value) == "SOURCE_UNSUPPORTED"


@pytest.mark.unit
def test_revalidation_accepts_address_reordering() -> None:
    """DNS answer order is irrelevant when the complete address set is unchanged."""
    source = validate_youtube_url(
        f"https://youtu.be/{VIDEO_ID}", resolver=resolver_with(PUBLIC_IPV4, PUBLIC_IPV6)
    )

    repeated = revalidate_youtube_url(
        source, resolver=resolver_with(PUBLIC_IPV6, PUBLIC_IPV4, PUBLIC_IPV4)
    )

    assert repeated.addresses == source.addresses


@pytest.mark.unit
def test_rebinding_between_validation_and_download_is_rejected() -> None:
    """Any addition or removal from the validated DNS set must stop provider access."""
    source = validate_youtube_url(
        f"https://youtu.be/{VIDEO_ID}", resolver=resolver_with(PUBLIC_IPV4, PUBLIC_IPV6)
    )

    with pytest.raises(SourceUnsupportedError, match=r"^SOURCE_UNSUPPORTED$"):
        revalidate_youtube_url(source, resolver=resolver_with(PUBLIC_IPV4))


@pytest.mark.unit
def test_revalidation_resolves_the_retained_source_host_not_the_canonical_host() -> None:
    """Display canonicalization must not replace the host whose DNS snapshot was trusted."""

    def per_host_resolver(host: str) -> Iterable[str]:
        """Give the short and canonical hosts distinct safe address sets."""
        return (PUBLIC_IPV4,) if host == "youtu.be" else (PUBLIC_IPV6,)

    source = validate_youtube_url(f"https://youtu.be/{VIDEO_ID}", resolver=per_host_resolver)

    repeated = revalidate_youtube_url(source, resolver=per_host_resolver)

    assert repeated.host == "youtu.be"
    assert repeated.addresses == frozenset({ipaddress.ip_address(PUBLIC_IPV4)})


@pytest.mark.unit
def test_safe_redirect_chain_retains_the_original_video() -> None:
    """Allowed source redirects may change host form but never the requested video identity."""
    source = validate_youtube_url(f"https://youtu.be/{VIDEO_ID}", resolver=PUBLIC_RESOLVER)

    redirected = validate_redirect_chain(
        source,
        (
            f"https://youtube.com/watch?v={VIDEO_ID}",
            f"https://www.youtube.com/watch?v={VIDEO_ID}",
        ),
        resolver=PUBLIC_RESOLVER,
    )

    assert redirected.canonical_url == source.canonical_url
    assert redirected.video_id == source.video_id


@pytest.mark.unit
@pytest.mark.parametrize(
    "redirects",
    [
        (f"https://evil.example/watch?v={VIDEO_ID}",),
        (f"https://youtu.be/{OTHER_VIDEO_ID}",),
        tuple(f"https://youtu.be/{VIDEO_ID}?hop={hop}" for hop in range(6)),
    ],
)
def test_unsafe_redirect_chains_are_rejected(redirects: tuple[str, ...]) -> None:
    """Redirects cannot widen host, video, or hop-count authority."""
    source = validate_youtube_url(f"https://youtu.be/{VIDEO_ID}", resolver=PUBLIC_RESOLVER)

    with pytest.raises(SourceUnsupportedError, match=r"^SOURCE_UNSUPPORTED$"):
        validate_redirect_chain(source, redirects, resolver=PUBLIC_RESOLVER)


@pytest.mark.unit
def test_redirect_dns_is_validated_independently() -> None:
    """An allowlisted redirect host is still unsafe if it resolves privately."""
    source = validate_youtube_url(f"https://youtu.be/{VIDEO_ID}", resolver=PUBLIC_RESOLVER)

    def per_host_resolver(host: str) -> Iterable[str]:
        """Make only the redirected canonical host resolve privately."""
        return ("127.0.0.1",) if host == "www.youtube.com" else (PUBLIC_IPV4,)

    with pytest.raises(SourceUnsupportedError, match=r"^SOURCE_UNSUPPORTED$"):
        validate_redirect_chain(
            source,
            (f"https://www.youtube.com/watch?v={VIDEO_ID}",),
            resolver=per_host_resolver,
        )
