"""Unit contracts for the sealed pending-authorization cookie."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from clipah.auth.models import AuthorizationError, PendingAuthorization
from clipah.auth.state_cookie import open_pending_authorization, seal_pending_authorization

SECRET = "a-test-session-secret-of-at-least-32-characters"
OTHER_SECRET = "a-different-secret-of-at-least-32-characters!!"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def pending() -> PendingAuthorization:
    """Build one ceremony to seal."""
    return PendingAuthorization(
        state="ceremony-state",
        nonce="ceremony-nonce",
        code_verifier="ceremony-code-verifier",
        redirect_uri="https://api.clipah.test/api/v1/auth/google/callback",
        created_at=NOW,
    )


@pytest.mark.unit
def test_a_sealed_ceremony_round_trips_without_revealing_its_bindings() -> None:
    """The browser holds the ceremony without being able to read or reuse its parts."""
    sealed = seal_pending_authorization(pending(), secret=SECRET)

    assert "ceremony-state" not in sealed
    assert "ceremony-code-verifier" not in sealed
    assert open_pending_authorization(sealed, secret=SECRET) == pending()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "secret"),
    [
        (lambda sealed: sealed, OTHER_SECRET),
        (lambda sealed: sealed[:-4] + "AAAA", SECRET),
        (lambda sealed: "not-a-sealed-value", SECRET),
        (lambda sealed: "", SECRET),
    ],
    ids=["foreign_secret", "tampered_payload", "malformed_value", "empty_value"],
)
def test_an_unforged_ceremony_is_the_only_usable_one(
    mutate: Callable[[str], str], secret: str
) -> None:
    """Only a ceremony this deployment sealed itself may be reopened."""
    sealed = seal_pending_authorization(pending(), secret=SECRET)

    with pytest.raises(AuthorizationError):
        open_pending_authorization(mutate(sealed), secret=secret)


@pytest.mark.unit
def test_a_recovered_ceremony_carries_the_instant_the_flow_ages_it_against() -> None:
    """Freshness stays the login flow's decision, so the sealed value must keep its age."""
    sealed = seal_pending_authorization(pending(), secret=SECRET)

    recovered = open_pending_authorization(sealed, secret=SECRET)

    assert recovered.created_at == NOW
