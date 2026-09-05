"""Validate an uploaded cookie jar before any part of it is stored or used.

A cookie file is a live credential for somebody's Google account, and this module is the
only place one is ever read. Four rules hold throughout.

* **Parse, never trust.** Only well-formed Netscape rows survive, and only for the domains
  and names YouTube authentication actually needs. A browser export of a whole browsing
  life becomes a handful of rows for one site.
* **Never name a value.** No exception, message, or log line here carries a cookie value.
  A refusal is not a reason to write a credential down.
* **Refuse a file this parser cannot understand whole.** A jar half read is a jar whose
  meaning nobody knows, and guessing at the rest of it is guessing with a credential.
* **Streaming and bounded.** The file is size-checked before it is decoded, so an upload
  claiming to be a cookie jar cannot become a memory problem.

Nothing here reads a database, a clock, or a provider: the caller supplies the instant the
jar is judged against, so the same bytes always produce the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass

# A real jar is a few kilobytes. Anything much larger is somebody sending something else.
MAX_COOKIE_FILE_BYTES = 256 * 1024
NETSCAPE_HEADER = "# Netscape HTTP Cookie File"
HTTP_ONLY_PREFIX = "#HttpOnly_"
NETSCAPE_FIELDS = 7

# The domains a YouTube authentication cookie may belong to. Anything else is another
# site's credential and is dropped rather than stored.
ALLOWED_DOMAINS: frozenset[str] = frozenset(
    {".youtube.com", "youtube.com", ".google.com", "google.com"}
)
# The cookies YouTube needs before it will recognise an account at all.
REQUIRED_COOKIE_NAMES: frozenset[str] = frozenset(
    {
        "SID",
        "HSID",
        "SSID",
        "APISID",
        "SAPISID",
        "__Secure-1PSID",
        "__Secure-3PSID",
    }
)
# Cookies that are kept when they are present because they keep a session working, but
# whose absence is not a reason to refuse a jar.
OPTIONAL_COOKIE_NAMES: frozenset[str] = frozenset(
    {
        "__Secure-1PAPISID",
        "__Secure-3PAPISID",
        "__Secure-1PSIDTS",
        "__Secure-3PSIDTS",
        "__Secure-1PSIDCC",
        "__Secure-3PSIDCC",
        "SIDCC",
        "LOGIN_INFO",
        "PREF",
        "VISITOR_INFO1_LIVE",
        "YSC",
    }
)
ACCEPTED_COOKIE_NAMES: frozenset[str] = REQUIRED_COOKIE_NAMES | OPTIONAL_COOKIE_NAMES

COOKIE_FILE_TOO_LARGE = "COOKIE_FILE_TOO_LARGE"
COOKIE_FILE_MALFORMED = "COOKIE_FILE_MALFORMED"
COOKIE_FILE_EXPIRED = "COOKIE_FILE_EXPIRED"
COOKIE_FILE_INSUFFICIENT = "COOKIE_FILE_INSUFFICIENT"

_ALLOWED_CONTROL = {"\t", "\n", "\r"}


class CookieValidationError(Exception):
    """A cookie jar this system will not store, named by a stable code and nothing else."""

    def __init__(self, code: str) -> None:
        """Carry the code alone: an explanation could only be made of the file itself."""
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CookieRecord:
    """One accepted cookie, normalized into the fields the Netscape format defines."""

    domain: str
    include_subdomains: bool
    path: str
    secure: bool
    expires_at_epoch: int
    name: str
    value: str
    http_only: bool


@dataclass(frozen=True, slots=True)
class ValidatedCookieJar:
    """Every accepted cookie, and the instant the jar itself stops being usable."""

    cookies: tuple[CookieRecord, ...]
    expires_at_epoch: int


def validate_cookie_jar(document: bytes, *, now_epoch: int) -> ValidatedCookieJar:
    """Reduce one uploaded jar to the rows YouTube authentication needs, or refuse it.

    The result is exactly what will be encrypted, stored, and later handed to the
    provider: no row this function did not accept survives anywhere.
    """
    if len(document) > MAX_COOKIE_FILE_BYTES:
        raise CookieValidationError(COOKIE_FILE_TOO_LARGE)
    text = _decoded(document)
    accepted: dict[tuple[str, str, str], CookieRecord] = {}
    for line in text.splitlines():
        record = _record(line)
        if record is None:
            continue
        if record.domain not in ALLOWED_DOMAINS or record.name not in ACCEPTED_COOKIE_NAMES:
            continue
        if 0 < record.expires_at_epoch <= now_epoch:
            continue
        accepted[(record.domain, record.path, record.name)] = record
    cookies = tuple(accepted.values())
    names = {cookie.name for cookie in cookies}
    if not names >= REQUIRED_COOKIE_NAMES:
        raise CookieValidationError(
            COOKIE_FILE_EXPIRED
            if _held_expired_credentials(text, now_epoch)
            else COOKIE_FILE_INSUFFICIENT
        )
    return ValidatedCookieJar(cookies=cookies, expires_at_epoch=_soonest_expiry(cookies))


def serialize_cookie_jar(jar: ValidatedCookieJar) -> str:
    """Write the accepted rows back out in the format the provider reads.

    What the provider is handed is exactly what this parser agreed to, which is why the
    result round-trips through :func:`validate_cookie_jar` unchanged.
    """
    lines = [NETSCAPE_HEADER, "# This file was written by Clipah for one import."]
    for cookie in jar.cookies:
        prefix = HTTP_ONLY_PREFIX if cookie.http_only else ""
        lines.append(
            "\t".join(
                [
                    f"{prefix}{cookie.domain}",
                    "TRUE" if cookie.include_subdomains else "FALSE",
                    cookie.path,
                    "TRUE" if cookie.secure else "FALSE",
                    str(cookie.expires_at_epoch),
                    cookie.name,
                    cookie.value,
                ]
            )
        )
    return "\n".join([*lines, ""])


def _decoded(document: bytes) -> str:
    """Decode the upload as UTF-8, refusing anything that is not text at all."""
    try:
        text = document.decode("utf-8")
    except UnicodeDecodeError:
        raise CookieValidationError(COOKIE_FILE_MALFORMED) from None
    if any(character < " " and character not in _ALLOWED_CONTROL for character in text):
        raise CookieValidationError(COOKIE_FILE_MALFORMED)
    if "\x7f" in text or "\x1b" in text:
        raise CookieValidationError(COOKIE_FILE_MALFORMED)
    return text


def _record(line: str) -> CookieRecord | None:
    """Read one line as a cookie, or as a comment, or refuse the file it came from."""
    stripped = line.rstrip("\r")
    if not stripped.strip():
        return None
    http_only = stripped.startswith(HTTP_ONLY_PREFIX)
    if http_only:
        stripped = stripped[len(HTTP_ONLY_PREFIX) :]
    elif stripped.startswith("#"):
        return None
    fields = stripped.split("\t")
    if len(fields) != NETSCAPE_FIELDS:
        raise CookieValidationError(COOKIE_FILE_MALFORMED)
    domain, include_subdomains, path, secure, expiry, name, value = fields
    return CookieRecord(
        domain=domain.strip().lower(),
        include_subdomains=_flag(include_subdomains),
        path=path.strip() or "/",
        secure=_flag(secure),
        expires_at_epoch=_expiry(expiry),
        name=name.strip(),
        value=value,
        http_only=http_only,
    )


def _flag(value: str) -> bool:
    """Read one Netscape boolean, refusing anything that is not one."""
    normalized = value.strip().upper()
    if normalized not in {"TRUE", "FALSE"}:
        raise CookieValidationError(COOKIE_FILE_MALFORMED)
    return normalized == "TRUE"


def _expiry(value: str) -> int:
    """Read one expiry, where zero means the cookie lives only for a browser session."""
    try:
        expiry = int(value.strip())
    except ValueError:
        raise CookieValidationError(COOKIE_FILE_MALFORMED) from None
    if expiry < 0:
        raise CookieValidationError(COOKIE_FILE_MALFORMED)
    return expiry


def _held_expired_credentials(text: str, now_epoch: int) -> bool:
    """Whether the jar carried the right cookies and they had simply run out."""
    names: set[str] = set()
    for line in text.splitlines():
        record = _record(line)
        if record is None or record.domain not in ALLOWED_DOMAINS:
            continue
        if record.name in REQUIRED_COOKIE_NAMES and 0 < record.expires_at_epoch <= now_epoch:
            names.add(record.name)
    return names >= REQUIRED_COOKIE_NAMES


def _soonest_expiry(cookies: tuple[CookieRecord, ...]) -> int:
    """When this jar stops working, which is when its shortest-lived credential does.

    A zero expiry is a session cookie rather than an expired one, so it is not allowed to
    decide that the whole connection has already ended.
    """
    expiries = [
        cookie.expires_at_epoch
        for cookie in cookies
        if cookie.expires_at_epoch > 0 and cookie.name in REQUIRED_COOKIE_NAMES
    ]
    return min(expiries)
