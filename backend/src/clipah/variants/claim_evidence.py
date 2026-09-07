"""Validation for User-provided claim evidence.

Everything here is about one asymmetry: a member is telling Clipah where a claim came
from, and Clipah is going to show that source beside the claim to other people. So the URL
is normalized and bounded before it is stored, the text fields are bounded and refused
rather than sanitized, and nothing in this module ever decides that a claim is true.

The URL is validated syntactically and never resolved. A citation is displayed, not
fetched: resolving it would turn opening a review page into an outbound request to a
member-supplied host, which is a signal nobody asked to send.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 2_048
MAX_TITLE_LENGTH = 300
MAX_PUBLISHER_LENGTH = 200
MAX_CLAIM_LENGTH = 1_000

#: Markup is refused rather than stripped. A member who pasted a tag meant something by
#: it, and silently rewriting their words is worse than telling them no.
MARKUP_MARKERS = ("<", ">")


class ClaimEvidenceInvalidError(Exception):
    """Refuse one piece of evidence through a stable, public-safe code."""

    def __init__(self, code: str) -> None:
        """Retain only the code a member may be shown."""
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class NormalizedEvidence:
    """One accepted citation, in the exact form it will be stored and displayed."""

    source_url: str
    source_title: str
    publisher: str
    claim_text: str
    display_host: str


def normalize_evidence(
    *,
    source_url: str,
    source_title: str,
    publisher: str,
    claim_text: str,
) -> NormalizedEvidence:
    """Accept one citation only when every part of it is safe to store and to show."""
    url, host = _normalized_url(source_url)
    return NormalizedEvidence(
        source_url=url,
        source_title=_bounded(source_title, MAX_TITLE_LENGTH, "EVIDENCE_TITLE_INVALID"),
        publisher=_bounded(publisher, MAX_PUBLISHER_LENGTH, "EVIDENCE_PUBLISHER_INVALID"),
        claim_text=_bounded(claim_text, MAX_CLAIM_LENGTH, "EVIDENCE_CLAIM_INVALID"),
        display_host=host,
    )


def _normalized_url(value: str) -> tuple[str, str]:
    """Return one HTTPS URL and the host a reader will be shown, or refuse it."""
    if not value or len(value) > MAX_URL_LENGTH or value != value.strip():
        raise ClaimEvidenceInvalidError("EVIDENCE_URL_INVALID")
    if any(character.isspace() or not character.isprintable() for character in value):
        raise ClaimEvidenceInvalidError("EVIDENCE_URL_INVALID")

    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ClaimEvidenceInvalidError("EVIDENCE_URL_SCHEME_UNSUPPORTED")
    if parts.username is not None or parts.password is not None:
        raise ClaimEvidenceInvalidError("EVIDENCE_URL_CREDENTIALS_PRESENT")
    host = parts.hostname
    if not host:
        raise ClaimEvidenceInvalidError("EVIDENCE_URL_INVALID")
    if _is_private_host(host):
        raise ClaimEvidenceInvalidError("EVIDENCE_URL_PRIVATE_HOST")

    normalized = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    return normalized, host


def _is_private_host(host: str) -> bool:
    """Refuse a host that names this deployment's own network rather than the web.

    Only literal addresses can be classified without a lookup, which is deliberate: a
    name is left to the reader's browser, and the reader's browser is not Clipah.
    """
    if host in {"localhost", "localhost."} or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    classified: ipaddress.IPv4Address | ipaddress.IPv6Address = address
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        classified = address.ipv4_mapped
    return not classified.is_global or classified.is_private or classified.is_loopback


def _bounded(value: str, limit: int, code: str) -> str:
    """Accept one bounded, markup-free line of member text."""
    text = value.strip()
    if not text or len(text) > limit:
        raise ClaimEvidenceInvalidError(code)
    if any(marker in text for marker in MARKUP_MARKERS):
        raise ClaimEvidenceInvalidError(code)
    return text
