"""A provider tells us where to fetch media, and a provider can be wrong or compromised.

Stock search results and generative provider outputs both arrive as a URL that a worker
then fetches. That worker sits inside the deployment's own network, so an attacker who can
influence one of those URLs — through a compromised provider, a poisoned catalogue entry,
or a provider account that is not ours — is asking our infrastructure to make a request on
their behalf. The metadata service of every major cloud is one such request.

This suite is about the destination policy that answer must pass before anything is
fetched: the scheme, the port, the credentials, and above all the address the host
resolves to.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import fields
from types import SimpleNamespace
from typing import Any

import pytest

from clipah.assets.provider_fetch import (
    UnsafeProviderUrlError,
    validate_provider_media_url,
)

PUBLIC_ANSWER = ("93.184.216.34",)


def public_resolver(host: str) -> Iterable[str]:
    """Answer every lookup with one globally routable address."""
    del host
    return PUBLIC_ANSWER


def local_resolver(host: str) -> Iterable[str]:
    """Answer every lookup the way a rebinding or an internal name would."""
    del host
    return ("127.0.0.1",)


@pytest.mark.unit
def test_an_ordinary_provider_url_is_accepted() -> None:
    """The policy has to let real stock media through or the feature does not exist."""
    url = "https://videos.pexels.com/video-files/12345/hd.mp4"

    assert validate_provider_media_url(url, resolver=public_resolver) == url


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://videos.pexels.com/hd.mp4",
        "file:///etc/passwd",
        "gopher://videos.pexels.com/hd.mp4",
        "ftp://videos.pexels.com/hd.mp4",
        "//videos.pexels.com/hd.mp4",
        "",
    ],
)
def test_only_https_is_fetched(url: str) -> None:
    """Anything but HTTPS is either unencrypted or not a fetch at all."""
    with pytest.raises(UnsafeProviderUrlError):
        validate_provider_media_url(url, resolver=public_resolver)


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "https://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "https://127.0.0.1/hd.mp4",
        "https://10.0.0.5/hd.mp4",
        "https://192.168.1.10/hd.mp4",
        "https://[::1]/hd.mp4",
        "https://[::ffff:127.0.0.1]/hd.mp4",
        "https://0.0.0.0/hd.mp4",
    ],
)
def test_an_address_inside_the_deployment_is_never_fetched(url: str) -> None:
    """These are the addresses an SSRF is actually aimed at."""
    with pytest.raises(UnsafeProviderUrlError):
        validate_provider_media_url(url, resolver=public_resolver)


@pytest.mark.unit
def test_a_public_name_that_resolves_inward_is_never_fetched() -> None:
    """A name is only as safe as what it resolves to, which the provider does not choose."""
    with pytest.raises(UnsafeProviderUrlError):
        validate_provider_media_url("https://videos.pexels.com/hd.mp4", resolver=local_resolver)


@pytest.mark.unit
def test_a_name_that_resolves_to_nothing_is_never_fetched() -> None:
    """An empty answer proves nothing about where the connection would go."""
    with pytest.raises(UnsafeProviderUrlError):
        validate_provider_media_url("https://videos.pexels.com/hd.mp4", resolver=lambda host: ())


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@videos.pexels.com/hd.mp4",
        "https://videos.pexels.com:8443/hd.mp4",
        "https://videos.pexels.com:22/hd.mp4",
    ],
)
def test_credentials_and_unusual_ports_are_refused(url: str) -> None:
    """Both are how a fetch is redirected at something that is not a media endpoint."""
    with pytest.raises(UnsafeProviderUrlError):
        validate_provider_media_url(url, resolver=public_resolver)


@pytest.mark.unit
def test_the_explicit_https_port_is_still_https() -> None:
    """A provider spelling the default port out loud is not doing anything unusual."""
    url = "https://videos.pexels.com:443/hd.mp4"

    assert validate_provider_media_url(url, resolver=public_resolver) == url


@pytest.mark.unit
def test_a_refusal_describes_neither_the_url_nor_the_address_it_resolved_to() -> None:
    """These refusals are logged and reported, and both values are the attacker's payload."""
    url = "https://169.254.169.254/latest/meta-data/"

    with pytest.raises(UnsafeProviderUrlError) as refusal:
        validate_provider_media_url(url, resolver=public_resolver)

    assert url not in str(refusal.value)
    assert "169.254.169.254" not in str(refusal.value)


@pytest.mark.unit
def test_both_worker_paths_that_fetch_provider_media_default_to_this_policy() -> None:
    """The two tasks that fetch a provider-named URL must not each decide for themselves."""
    from clipah.jobs.broll_generate_task import GenerationDependencies
    from clipah.jobs.broll_retrieve_task import RetrievalDependencies

    assert _default_policy(RetrievalDependencies) is validate_provider_media_url
    assert _default_policy(GenerationDependencies) is validate_provider_media_url


@pytest.mark.unit
def test_a_retrieval_refuses_an_unsafe_catalogue_url_terminally() -> None:
    """Retrying a fetch at the metadata service would only repeat the attacker's request."""
    from clipah.broll.retriever import ExternalAssetCandidate, LicenseTerms, MediaKind
    from clipah.jobs.broll_retrieve_task import (
        MEDIA_URL_UNSAFE_CODE,
        RetrievalDependencies,
        _safe_download_url,
    )
    from clipah.jobs.models import TerminalJobError

    candidate = ExternalAssetCandidate(
        provider="pexels",
        provider_asset_id="12345",
        media_kind=MediaKind.VIDEO,
        source_url="https://www.pexels.com/video/12345/",
        download_url="https://169.254.169.254/latest/meta-data/",
        author="Ana Rahma",
        author_url="https://www.pexels.com/@ana-rahma",
        license=LicenseTerms(
            name="Pexels License",
            url="https://www.pexels.com/license/",
            attribution_required=False,
            snapshot="Free to use.",
        ),
        width=1080,
        height=1920,
        duration_ms=9_000,
        attribution_text="Video by Ana Rahma on Pexels",
        query="signup form",
        safe=True,
        description="a signup form",
        tags=("signup",),
    )
    dependencies = _dependencies_with_default_policy(RetrievalDependencies)

    with pytest.raises(TerminalJobError) as refusal:
        _safe_download_url(candidate, dependencies=dependencies)

    assert str(refusal.value) == MEDIA_URL_UNSAFE_CODE
    assert "169.254.169.254" not in str(refusal.value)


@pytest.mark.unit
def test_a_generation_refuses_an_unsafe_output_url_terminally() -> None:
    """A provider account somebody else reached must not choose our outbound request."""
    from clipah.jobs.broll_generate_task import (
        MEDIA_URL_UNSAFE_CODE,
        GenerationDependencies,
        _safe_output_url,
    )
    from clipah.jobs.models import TerminalJobError

    dependencies = _dependencies_with_default_policy(GenerationDependencies)

    with pytest.raises(TerminalJobError) as refusal:
        _safe_output_url("http://127.0.0.1:6379/", dependencies)

    assert str(refusal.value) == MEDIA_URL_UNSAFE_CODE
    assert "127.0.0.1" not in str(refusal.value)


def _default_policy(kind: type) -> Any:
    """Read the policy one dependency set uses when nothing was injected."""
    field = next(entry for entry in fields(kind) if entry.name == "media_url_policy")
    return field.default


def _dependencies_with_default_policy(kind: type) -> Any:
    """Carry just the policy each task reads, since a destination needs nothing else.

    The real dependency set is a stock catalogue, an object store, and the pinned media
    toolchain, none of which a decision about a destination depends on.
    """
    return SimpleNamespace(media_url_policy=_default_policy(kind))
