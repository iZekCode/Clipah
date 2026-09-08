"""Security contract for Social Account OAuth ceremonies and encrypted grants.

These tests keep the provider credential boundary intentionally small: ceremonies bind a browser
to one exact callback, ciphertext binds a grant to one Workspace and Social Account, and a lease
hands plaintext to one operation only without making it printable or long-lived.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from clipah.social_accounts.models import SocialProvider
from clipah.social_accounts.oauth import (
    AuthorizationExpiredError,
    AuthorizationStateError,
    provider_policy,
    start_authorization,
    validate_authorization,
)
from clipah.social_accounts.secrets import (
    EncryptedOAuthGrant,
    LocalSocialSecretStore,
    OAuthGrantLease,
    OAuthGrantLeaseExpiredError,
    OAuthSecretContext,
    SocialSecretDecryptionError,
)

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
ACCOUNT_ID = UUID("33333333-3333-4333-8333-333333333333")
GRANT_ID = UUID("44444444-4444-4444-8444-444444444444")
OPERATION_ID = UUID("55555555-5555-4555-8555-555555555555")
ACCESS_CANARY = b"access-canary-8dd2a2d5"
REFRESH_CANARY = b"refresh-canary-e4ff1452"
GRANT_DOCUMENT = (
    b'{"access_token":"access-canary-8dd2a2d5","refresh_token":"refresh-canary-e4ff1452"}'
)


def secret_context(
    *, workspace_id: UUID = WORKSPACE_ID, token_version: int = 1
) -> OAuthSecretContext:
    """Build the identity authenticated into one encrypted OAuth Grant."""
    return OAuthSecretContext(
        workspace_id=workspace_id,
        social_account_id=ACCOUNT_ID,
        grant_id=GRANT_ID,
        provider=SocialProvider.YOUTUBE,
        token_version=token_version,
    )


def secret_store(key: bytes = b"k" * 32) -> LocalSocialSecretStore:
    """Build one local wrapping-key implementation with an explicit version."""
    return LocalSocialSecretStore(key=key, key_reference="local/social-oauth", key_version=7)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("provider", "expected_scopes"),
    [
        (
            SocialProvider.YOUTUBE,
            frozenset(
                {
                    "https://www.googleapis.com/auth/youtube.readonly",
                    "https://www.googleapis.com/auth/youtube.upload",
                }
            ),
        ),
        (
            SocialProvider.INSTAGRAM,
            frozenset({"instagram_business_basic", "instagram_business_content_publish"}),
        ),
        (
            SocialProvider.TIKTOK,
            frozenset({"user.info.basic", "video.upload"}),
        ),
    ],
)
def test_each_provider_requests_only_its_initial_connection_scopes(
    provider: SocialProvider, expected_scopes: frozenset[str]
) -> None:
    """A broader or incomplete scope set either overreaches or creates an unusable connection."""
    policy = provider_policy(
        provider,
        client_id="provider-client",
        redirect_uri=f"https://clipah.example/api/v1/social-oauth/{provider.value}/callback",
        api_version="v3" if provider is SocialProvider.YOUTUBE else "v2",
    )

    assert policy.minimum_scopes == expected_scopes


@pytest.mark.unit
def test_a_connection_ceremony_uses_pkce_s256_and_the_exact_redirect_uri() -> None:
    """Changing either binding would allow an intercepted code to be redeemed elsewhere."""
    policy = provider_policy(
        SocialProvider.YOUTUBE,
        client_id="youtube-client",
        redirect_uri="https://clipah.example/api/v1/social-oauth/youtube/callback",
        api_version="v3",
    )

    started = start_authorization(
        policy=policy,
        now=NOW,
        random_bytes=lambda size: bytes(range(size)),
    )
    expected_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(started.pending.code_verifier.encode("ascii")).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )

    assert started.pending.redirect_uri == policy.redirect_uri
    assert started.pending.requested_scopes == policy.minimum_scopes
    assert started.pending.state != started.pending.code_verifier
    assert len(base64.urlsafe_b64decode(started.pending.state + "==")) == 32
    assert started.code_challenge == expected_challenge
    assert started.code_challenge_method == "S256"
    assert f"redirect_uri={policy.redirect_uri.replace(':', '%3A').replace('/', '%2F')}" in (
        started.authorization_url
    )


@pytest.mark.unit
def test_a_callback_refuses_a_different_state_without_echoing_it() -> None:
    """State is the callback CSRF proof and a refusal must not disclose either value."""
    policy = provider_policy(
        SocialProvider.TIKTOK,
        client_id="tiktok-client",
        redirect_uri="https://clipah.example/api/v1/social-oauth/tiktok/callback",
        api_version="v2",
    )
    pending = start_authorization(
        policy=policy, now=NOW, random_bytes=lambda size: b"a" * size
    ).pending

    with pytest.raises(AuthorizationStateError) as refusal:
        validate_authorization(
            pending=pending,
            provider=SocialProvider.TIKTOK,
            state="attacker-state-canary",
            redirect_uri=policy.redirect_uri,
            now=NOW,
        )

    assert "attacker-state-canary" not in str(refusal.value)
    assert pending.state not in str(refusal.value)


@pytest.mark.unit
def test_an_expired_connection_ceremony_cannot_be_redeemed() -> None:
    """An old browser cookie must not remain an authorization capability indefinitely."""
    policy = provider_policy(
        SocialProvider.INSTAGRAM,
        client_id="instagram-client",
        redirect_uri="https://clipah.example/api/v1/social-oauth/instagram/callback",
        api_version="v22.0",
    )
    pending = start_authorization(
        policy=policy, now=NOW, random_bytes=lambda size: b"b" * size
    ).pending

    with pytest.raises(AuthorizationExpiredError):
        validate_authorization(
            pending=pending,
            provider=SocialProvider.INSTAGRAM,
            state=pending.state,
            redirect_uri=policy.redirect_uri,
            now=NOW + timedelta(minutes=16),
        )


@pytest.mark.unit
def test_oauth_grant_ciphertext_and_metadata_reveal_no_token() -> None:
    """Database readers may see encrypted columns but must learn no reusable credential."""
    encrypted = secret_store().encrypt(GRANT_DOCUMENT, context=secret_context())

    assert isinstance(encrypted, EncryptedOAuthGrant)
    assert ACCESS_CANARY not in encrypted.ciphertext
    assert REFRESH_CANARY not in encrypted.ciphertext
    assert ACCESS_CANARY not in encrypted.wrapped_key
    assert encrypted.key_reference == "local/social-oauth"
    assert encrypted.key_version == 7
    assert len(encrypted.nonce) == 12


@pytest.mark.unit
def test_oauth_grant_round_trip_requires_the_exact_authenticated_context() -> None:
    """Copying ciphertext to another Workspace or token version must make it unusable."""
    keeper = secret_store()
    encrypted = keeper.encrypt(GRANT_DOCUMENT, context=secret_context())

    assert bytes(keeper.decrypt(encrypted, context=secret_context())) == GRANT_DOCUMENT
    with pytest.raises(SocialSecretDecryptionError):
        keeper.decrypt(encrypted, context=secret_context(workspace_id=OTHER_WORKSPACE_ID))
    with pytest.raises(SocialSecretDecryptionError):
        keeper.decrypt(encrypted, context=secret_context(token_version=2))


@pytest.mark.unit
def test_tampered_oauth_grant_material_is_refused_without_being_described() -> None:
    """Authenticated encryption must turn a changed byte into a sanitized refusal."""
    keeper = secret_store()
    encrypted = keeper.encrypt(GRANT_DOCUMENT, context=secret_context())
    tampered = EncryptedOAuthGrant(
        key_reference=encrypted.key_reference,
        key_version=encrypted.key_version,
        wrapped_key=encrypted.wrapped_key,
        nonce=encrypted.nonce,
        ciphertext=bytes([encrypted.ciphertext[0] ^ 1, *encrypted.ciphertext[1:]]),
    )

    with pytest.raises(SocialSecretDecryptionError) as refusal:
        keeper.decrypt(tampered, context=secret_context())

    assert ACCESS_CANARY.decode() not in str(refusal.value)
    assert str(tampered.ciphertext) not in str(refusal.value)


@pytest.mark.unit
def test_an_oauth_grant_lease_is_one_bounded_operation_and_clears_its_buffer() -> None:
    """Plaintext must disappear when the selected adapter operation ends."""
    material = bytearray(GRANT_DOCUMENT)
    lease = OAuthGrantLease(
        social_account_id=ACCOUNT_ID,
        operation_id=OPERATION_ID,
        expires_at=NOW + timedelta(minutes=5),
        secret=material,
    )

    with lease.open(now=NOW) as opened:
        assert bytes(opened) == GRANT_DOCUMENT
        assert ACCESS_CANARY.decode() not in repr(lease)
        assert REFRESH_CANARY.decode() not in str(lease)

    assert material == bytearray(len(GRANT_DOCUMENT))
    with pytest.raises(OAuthGrantLeaseExpiredError), lease.open(now=NOW):
        pass


@pytest.mark.unit
def test_an_expired_oauth_grant_lease_never_yields_plaintext() -> None:
    """A worker delayed beyond the lease window must obtain a fresh authorization decision."""
    lease = OAuthGrantLease(
        social_account_id=ACCOUNT_ID,
        operation_id=OPERATION_ID,
        expires_at=NOW,
        secret=bytearray(GRANT_DOCUMENT),
    )

    with (
        pytest.raises(OAuthGrantLeaseExpiredError),
        lease.open(now=NOW + timedelta(microseconds=1)),
    ):
        pass


@pytest.mark.unit
def test_a_rotated_wrapping_key_can_read_old_grants_and_writes_only_the_new_version() -> None:
    """Rotation preserves access long enough to rewrap without producing old-version ciphertext."""
    old_key = b"o" * 32
    old_store = LocalSocialSecretStore(key=old_key, key_reference="kms/social-oauth", key_version=7)
    old_envelope = old_store.encrypt(GRANT_DOCUMENT, context=secret_context())
    rotated = LocalSocialSecretStore(
        key=b"n" * 32,
        key_reference="kms/social-oauth",
        key_version=8,
        previous_keys={("kms/social-oauth", 7): old_key},
    )

    plaintext = rotated.decrypt(old_envelope, context=secret_context())
    new_envelope = rotated.encrypt(bytes(plaintext), context=secret_context())
    for index in range(len(plaintext)):
        plaintext[index] = 0

    assert new_envelope.key_version == 8
    assert bytes(rotated.decrypt(new_envelope, context=secret_context())) == GRANT_DOCUMENT


@pytest.mark.unit
def test_an_unavailable_wrapping_key_fails_closed_without_disclosing_material() -> None:
    """Missing historical KMS material denies decryption with one sanitized refusal."""
    old_envelope = secret_store().encrypt(GRANT_DOCUMENT, context=secret_context())
    current_only = LocalSocialSecretStore(
        key=b"n" * 32, key_reference="local/social-oauth", key_version=8
    )

    with pytest.raises(SocialSecretDecryptionError) as refusal:
        current_only.decrypt(old_envelope, context=secret_context())

    assert ACCESS_CANARY.decode() not in str(refusal.value)
    assert REFRESH_CANARY.decode() not in str(refusal.value)
