"""Validating an uploaded cookie jar before any of it is stored or used.

A cookie file is the most dangerous thing a member can hand this system: it is a live
credential for their Google account. Everything here follows from that.

* **The file is parsed, never trusted.** Only the Netscape rows this parser understands
  survive, and only for the domains and names YouTube authentication actually needs.
  Everything else is dropped, so a browser export of somebody's whole life becomes a
  handful of rows for one site.
* **A value is never named.** No error message, no log line, and no exception here carries
  a cookie value, because a refusal is not a reason to write a credential down.
* **A file that cannot be parsed is refused whole.** A jar half understood is a jar whose
  meaning nobody knows.
"""

from __future__ import annotations

import pytest

from clipah.source_connectors.cookies import (
    COOKIE_FILE_EXPIRED,
    COOKIE_FILE_INSUFFICIENT,
    COOKIE_FILE_MALFORMED,
    COOKIE_FILE_TOO_LARGE,
    MAX_COOKIE_FILE_BYTES,
    REQUIRED_COOKIE_NAMES,
    CookieValidationError,
    serialize_cookie_jar,
    validate_cookie_jar,
)

NOW_EPOCH = 1_800_000_000
LATER = NOW_EPOCH + 60 * 60 * 24 * 30


def row(
    *,
    domain: str = ".youtube.com",
    include_subdomains: str = "TRUE",
    path: str = "/",
    secure: str = "TRUE",
    expiry: int = LATER,
    name: str = "SID",
    value: str = "cookie-value",
    http_only: bool = False,
) -> str:
    """One Netscape row, in the seven tab-separated fields the format defines."""
    prefix = "#HttpOnly_" if http_only else ""
    return "\t".join(
        [f"{prefix}{domain}", include_subdomains, path, secure, str(expiry), name, value]
    )


def jar(*rows: str, header: str = "# Netscape HTTP Cookie File", newline: str = "\n") -> bytes:
    """One cookie file, with the header a browser export carries."""
    return newline.join([header, *rows, ""]).encode("utf-8")


def authenticated(**overrides: object) -> bytes:
    """One jar carrying enough cookies for YouTube to recognise an account."""
    rows = [row(name=name, **overrides) for name in sorted(REQUIRED_COOKIE_NAMES)]  # type: ignore[arg-type]
    return jar(*rows)


def refusal(document: bytes, *, now: int = NOW_EPOCH) -> str:
    """Assert one jar is refused and return the stable code it carries."""
    with pytest.raises(CookieValidationError) as error:
        validate_cookie_jar(document, now_epoch=now)
    return error.value.code


@pytest.mark.unit
def test_a_browser_export_is_reduced_to_the_rows_youtube_authentication_needs() -> None:
    """A jar is a credential, so only what the provider needs is ever kept."""
    document = jar(
        row(name="SID"),
        row(name="HSID", http_only=True),
        row(name="SSID"),
        row(name="APISID"),
        row(name="SAPISID"),
        row(name="__Secure-1PSID"),
        row(name="__Secure-3PSID"),
        row(name="TRACKING_ID", domain=".doubleclick.net"),
        row(name="analytics", domain=".example.com"),
    )

    validated = validate_cookie_jar(document, now_epoch=NOW_EPOCH)

    assert {cookie.name for cookie in validated.cookies} >= REQUIRED_COOKIE_NAMES
    assert {cookie.domain for cookie in validated.cookies} <= {".youtube.com", ".google.com"}
    assert all(cookie.name != "TRACKING_ID" for cookie in validated.cookies)
    assert all(cookie.name != "analytics" for cookie in validated.cookies)


@pytest.mark.unit
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_both_line_endings_a_browser_writes_are_understood(newline: str) -> None:
    """A file exported on Windows is the same file, and refusing it would be a bug."""
    document = jar(*[row(name=name) for name in sorted(REQUIRED_COOKIE_NAMES)], newline=newline)

    validated = validate_cookie_jar(document, now_epoch=NOW_EPOCH)

    assert {cookie.name for cookie in validated.cookies} == REQUIRED_COOKIE_NAMES


@pytest.mark.unit
def test_the_http_only_prefix_is_read_as_a_flag_rather_than_as_a_domain() -> None:
    """`#HttpOnly_` is part of the format, and reading it as a comment would drop the row."""
    validated = validate_cookie_jar(authenticated(http_only=True), now_epoch=NOW_EPOCH)

    assert all(cookie.http_only for cookie in validated.cookies)
    assert {cookie.domain for cookie in validated.cookies} == {".youtube.com"}


@pytest.mark.unit
def test_a_jar_that_expires_soonest_decides_when_the_connection_expires() -> None:
    """A connection outliving its cookies would be a connection that cannot work."""
    soonest = NOW_EPOCH + 60 * 60 * 6
    rows = [row(name=name) for name in sorted(REQUIRED_COOKIE_NAMES)]
    rows[0] = row(name=sorted(REQUIRED_COOKIE_NAMES)[0], expiry=soonest)

    validated = validate_cookie_jar(jar(*rows), now_epoch=NOW_EPOCH)

    assert validated.expires_at_epoch == soonest


@pytest.mark.unit
def test_a_session_cookie_with_no_expiry_does_not_shorten_the_connection() -> None:
    """A zero expiry means "this browser session", not "already expired"."""
    rows = [row(name=name) for name in sorted(REQUIRED_COOKIE_NAMES)]
    rows.append(row(name="YSC", expiry=0))

    validated = validate_cookie_jar(jar(*rows), now_epoch=NOW_EPOCH)

    assert validated.expires_at_epoch == LATER
    assert any(cookie.name == "YSC" for cookie in validated.cookies)


@pytest.mark.unit
def test_a_jar_whose_authentication_cookies_have_all_expired_is_refused() -> None:
    """Storing a dead credential would produce an import that can only fail."""
    document = authenticated(expiry=NOW_EPOCH - 60)

    assert refusal(document) == COOKIE_FILE_EXPIRED


@pytest.mark.unit
def test_a_jar_without_the_cookies_youtube_needs_is_refused() -> None:
    """A partial jar cannot authenticate, and keeping it would be keeping a secret for nothing."""
    document = jar(row(name="PREF"), row(name="VISITOR_INFO1_LIVE"))

    assert refusal(document) == COOKIE_FILE_INSUFFICIENT


@pytest.mark.unit
def test_a_jar_for_an_unrelated_site_is_refused_rather_than_stored_empty() -> None:
    """Somebody's bank cookies are not a YouTube connection, and are not kept either."""
    document = jar(
        row(domain=".example.com", name="SID"),
        row(domain=".bank.example", name="HSID"),
    )

    assert refusal(document) == COOKIE_FILE_INSUFFICIENT


@pytest.mark.unit
@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param(b"not a cookie file at all\n", id="no-rows"),
        pytest.param(jar("only\tfour\tfields\there"), id="too-few-fields"),
        pytest.param(jar(row(expiry=0).replace("\t0\t", "\tnot-a-number\t")), id="expiry-text"),
        pytest.param(jar(row(secure="MAYBE")), id="flag-not-a-boolean"),
        pytest.param(jar(row(value="value\x00with-a-null")), id="control-character"),
        pytest.param(jar(row(name="SID\x1b[31m")), id="escape-sequence-in-a-name"),
    ],
)
def test_a_file_this_parser_cannot_trust_is_refused_whole(malformed: bytes) -> None:
    """A jar half understood is a jar whose meaning nobody knows."""
    assert refusal(malformed) == COOKIE_FILE_MALFORMED


@pytest.mark.unit
def test_a_file_larger_than_the_accepted_size_is_refused_before_it_is_parsed() -> None:
    """A cookie jar is small; anything else is somebody sending something else."""
    document = b"# Netscape HTTP Cookie File\n" + b"a" * (MAX_COOKIE_FILE_BYTES + 1)

    assert refusal(document) == COOKIE_FILE_TOO_LARGE


@pytest.mark.unit
def test_the_last_row_wins_when_one_cookie_is_written_twice() -> None:
    """A browser export can repeat a name, and a jar with two answers has none."""
    rows = [row(name=name) for name in sorted(REQUIRED_COOKIE_NAMES)]
    rows.append(row(name="SID", value="the-newer-one", expiry=LATER + 60))

    validated = validate_cookie_jar(jar(*rows), now_epoch=NOW_EPOCH)

    names = [cookie.name for cookie in validated.cookies]
    assert names.count("SID") == 1
    assert next(cookie for cookie in validated.cookies if cookie.name == "SID").value == (
        "the-newer-one"
    )


@pytest.mark.unit
def test_an_expired_row_is_dropped_while_the_rest_of_the_jar_survives() -> None:
    """One stale extra cookie is not a reason to refuse a jar that still works."""
    rows = [row(name=name) for name in sorted(REQUIRED_COOKIE_NAMES)]
    rows.append(row(name="PREF", expiry=NOW_EPOCH - 1))

    validated = validate_cookie_jar(jar(*rows), now_epoch=NOW_EPOCH)

    assert all(cookie.name != "PREF" for cookie in validated.cookies)
    assert {cookie.name for cookie in validated.cookies} == REQUIRED_COOKIE_NAMES


@pytest.mark.unit
def test_the_serialized_jar_is_exactly_what_was_accepted_and_nothing_else() -> None:
    """What the provider reads has to be what this parser agreed to, row for row."""
    document = jar(
        *[row(name=name) for name in sorted(REQUIRED_COOKIE_NAMES)],
        row(name="TRACKING_ID", domain=".doubleclick.net"),
    )
    validated = validate_cookie_jar(document, now_epoch=NOW_EPOCH)

    serialized = serialize_cookie_jar(validated)

    lines = serialized.splitlines()
    assert lines[0].startswith("# Netscape HTTP Cookie File")
    assert len([line for line in lines if line and not line.startswith("#")]) == len(
        validated.cookies
    )
    assert "doubleclick" not in serialized
    assert serialized.endswith("\n")


@pytest.mark.unit
def test_a_serialized_jar_round_trips_through_the_parser_unchanged() -> None:
    """The provider's file is a file this parser would accept, which keeps one rule."""
    validated = validate_cookie_jar(authenticated(), now_epoch=NOW_EPOCH)

    again = validate_cookie_jar(
        serialize_cookie_jar(validated).encode("utf-8"), now_epoch=NOW_EPOCH
    )

    assert again.cookies == validated.cookies


@pytest.mark.unit
def test_a_refusal_never_carries_the_value_it_refused() -> None:
    """An error message is written to a log, and a cookie value may never reach one."""
    secret = "super-secret-session-value"
    document = jar(row(name="SID", value=secret, secure="MAYBE"))

    with pytest.raises(CookieValidationError) as error:
        validate_cookie_jar(document, now_epoch=NOW_EPOCH)

    assert secret not in str(error.value)
    assert secret not in repr(error.value)


@pytest.mark.unit
def test_the_accepted_names_are_the_documented_minimum_rather_than_a_whole_browser() -> None:
    """The allowlist is the contract this parser is judged against, so it is asserted."""
    assert (
        frozenset(
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
        == REQUIRED_COOKIE_NAMES
    )


@pytest.mark.unit
def test_a_file_that_is_not_text_at_all_is_refused() -> None:
    """An upload that is not UTF-8 is not a cookie file, whatever else it might be."""
    assert refusal(b"\xff\xfe\x00binary") == COOKIE_FILE_MALFORMED


@pytest.mark.unit
def test_a_deleted_character_hidden_in_a_value_is_refused() -> None:
    """A DEL byte inside a jar is somebody probing what this parser will swallow."""
    assert refusal(jar(row(value="value\x7f"))) == COOKIE_FILE_MALFORMED


@pytest.mark.unit
def test_a_negative_expiry_is_refused_rather_than_read_as_a_session_cookie() -> None:
    """Zero means a session cookie; a negative number means the file is wrong."""
    assert refusal(jar(row(expiry=-1))) == COOKIE_FILE_MALFORMED


@pytest.mark.unit
def test_blank_lines_between_rows_are_ignored_like_the_format_says() -> None:
    """A browser export can be padded, and padding is not a reason to refuse a jar."""
    rows: list[str] = []
    for name in sorted(REQUIRED_COOKIE_NAMES):
        rows.extend([row(name=name), "   "])

    validated = validate_cookie_jar(jar(*rows), now_epoch=NOW_EPOCH)

    assert {cookie.name for cookie in validated.cookies} == REQUIRED_COOKIE_NAMES
