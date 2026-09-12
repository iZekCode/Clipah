"""Where a worker is willing to fetch provider-named media from.

Stock search results and generative outputs both arrive as a URL that a worker then
fetches from inside the deployment's own network. A provider that is compromised, or a
catalogue entry that somebody else wrote, therefore gets to choose one outbound request —
which is the whole of a server-side request forgery. This module is the policy that answer
has to pass first, and it is deliberately the same destination policy the YouTube source
validator applies: HTTPS only, no credentials in the URL, the usual port, and every
address the host resolves to globally routable.

The check resolves the name and then the fetch resolves it again, so a name that changes
answers between the two can still move. That residual rebinding window is the same one the
source-import path carries, and closing it needs a transport that connects to an address
this module has already approved.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit

from clipah.assets.source_validation import Resolver, is_public_address, resolve_host

MEDIA_URL_UNSAFE_CODE = "PROVIDER_MEDIA_URL_UNSAFE"


class UnsafeProviderUrlError(Exception):
    """A provider named a destination this deployment will not fetch from.

    The message carries a stable code and nothing else: the URL and the address it
    resolved to are the attacker's own payload, and both would otherwise be copied into
    logs, traces, and durable Job events.
    """

    def __init__(self) -> None:
        """Refuse without describing what was refused."""
        super().__init__(MEDIA_URL_UNSAFE_CODE)
        self.code = MEDIA_URL_UNSAFE_CODE


def validate_provider_media_url(url: str, *, resolver: Resolver | None = None) -> str:
    """Return the URL unchanged once its destination has been proven safe to fetch."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise UnsafeProviderUrlError from error
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise UnsafeProviderUrlError
    _require_public_destination(host, resolver=resolver or resolve_host)
    return url


def _require_public_destination(host: str, *, resolver: Resolver) -> None:
    """Require a non-empty answer in which every address is globally routable.

    A host that already is an address is judged directly: there is nothing to resolve, and
    reaching for a resolver would let one answer stand in for the literal that was named.
    """
    if _is_address_literal(host):
        if not is_public_address(host):
            raise UnsafeProviderUrlError
        return
    try:
        answers: Iterable[str] = tuple(resolver(host))
    except (OSError, TypeError, ValueError) as error:
        raise UnsafeProviderUrlError from error
    if not answers:
        raise UnsafeProviderUrlError
    for answer in answers:
        if not is_public_address(answer):
            raise UnsafeProviderUrlError


def _is_address_literal(host: str) -> bool:
    """Say whether the host is already an address rather than a name to be resolved."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
