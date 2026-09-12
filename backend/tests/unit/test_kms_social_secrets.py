"""AWS KMS contracts for Social Account OAuth Grant envelope encryption."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import pytest
from botocore.exceptions import ClientError

from clipah.config import Environment, Settings, SocialSecretBackend
from clipah.social_accounts.models import SocialProvider
from clipah.social_accounts.secrets import (
    AwsKmsSocialSecretStore,
    LocalSocialSecretStore,
    OAuthSecretContext,
    SocialSecretDecryptionError,
    SocialSecretUnavailableError,
    social_secret_store_for,
)

KEY_ARN = "arn:aws:kms:ap-southeast-1:123456789012:key/11111111-2222-4333-8444-555555555555"
PLAINTEXT_KEY = b"k" * 32
WRAPPED_KEY = b"kms-ciphertext-blob"
GRANT = b'{"accessToken":"access-canary","refreshToken":"refresh-canary"}'


class FakeKmsClient:
    """Model the two AWS KMS operations the envelope store is allowed to use."""

    def __init__(self) -> None:
        """Start without an observed provider request."""
        self.generate_request: dict[str, object] | None = None
        self.decrypt_request: dict[str, object] | None = None

    def generate_data_key(self, **request: object) -> Mapping[str, Any]:
        """Return one complete AWS-shaped data-key response."""
        self.generate_request = dict(request)
        return {"Plaintext": PLAINTEXT_KEY, "CiphertextBlob": WRAPPED_KEY, "KeyId": KEY_ARN}

    def decrypt(self, **request: object) -> Mapping[str, Any]:
        """Return the key only when the exact stored blob is presented."""
        self.decrypt_request = dict(request)
        if request.get("CiphertextBlob") != WRAPPED_KEY:
            raise AssertionError("unexpected wrapped key")
        return {"Plaintext": PLAINTEXT_KEY, "KeyId": KEY_ARN}


class UnavailableKmsClient(FakeKmsClient):
    """Fail like AWS without making provider diagnostics part of the domain error."""

    def generate_data_key(self, **request: object) -> Mapping[str, Any]:
        """Raise the typed SDK error produced by a denied KMS request."""
        del request
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "private-role-and-key-arn"}},
            "GenerateDataKey",
        )


def secret_context(
    *, workspace: str = "11111111-1111-4111-8111-111111111111"
) -> OAuthSecretContext:
    """Build one literal durable identity for authenticated encryption."""
    return OAuthSecretContext(
        workspace_id=UUID(workspace),
        social_account_id=UUID("22222222-2222-4222-8222-222222222222"),
        grant_id=UUID("33333333-3333-4333-8333-333333333333"),
        provider=SocialProvider.YOUTUBE,
        token_version=4,
    )


@pytest.mark.unit
def test_kms_store_round_trips_without_persisting_plaintext_key_or_grant() -> None:
    """Database material must be useless without both KMS authority and the exact context."""
    client = FakeKmsClient()
    store = AwsKmsSocialSecretStore(client=client, key_arn=KEY_ARN)

    encrypted = store.encrypt(GRANT, context=secret_context())
    opened = store.decrypt(encrypted, context=secret_context())

    assert bytes(opened) == GRANT
    assert encrypted.key_reference == KEY_ARN
    assert encrypted.key_version == 1
    assert encrypted.wrapped_key == WRAPPED_KEY
    assert PLAINTEXT_KEY not in encrypted.ciphertext
    assert b"access-canary" not in encrypted.ciphertext
    expected_context = {
        "workspace_id": "11111111-1111-4111-8111-111111111111",
        "social_account_id": "22222222-2222-4222-8222-222222222222",
        "grant_id": "33333333-3333-4333-8333-333333333333",
        "provider": "youtube",
        "token_version": "4",
    }
    assert client.generate_request == {
        "KeyId": KEY_ARN,
        "KeySpec": "AES_256",
        "EncryptionContext": expected_context,
    }
    assert client.decrypt_request == {
        "CiphertextBlob": WRAPPED_KEY,
        "KeyId": KEY_ARN,
        "EncryptionAlgorithm": "SYMMETRIC_DEFAULT",
        "EncryptionContext": expected_context,
    }


@pytest.mark.unit
def test_kms_store_authenticates_the_workspace_context_again_at_decryption() -> None:
    """A copied OAuth Grant row cannot be opened under another Workspace identity."""
    store = AwsKmsSocialSecretStore(client=FakeKmsClient(), key_arn=KEY_ARN)
    encrypted = store.encrypt(GRANT, context=secret_context())

    with pytest.raises(SocialSecretDecryptionError, match=r"^OAuth Grant cannot be opened$"):
        store.decrypt(
            encrypted,
            context=secret_context(workspace="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        )


@pytest.mark.unit
def test_kms_provider_failure_is_sanitized() -> None:
    """AWS roles and key identifiers must not escape through a failed Grant operation."""
    store = AwsKmsSocialSecretStore(client=UnavailableKmsClient(), key_arn=KEY_ARN)

    with pytest.raises(SocialSecretUnavailableError) as refusal:
        store.encrypt(GRANT, context=secret_context())

    assert str(refusal.value) == "OAuth Grant key is unavailable"
    assert "private-role" not in str(refusal.value)


@pytest.mark.unit
def test_store_factory_selects_only_the_explicit_wrapping_backend() -> None:
    """Deployment selection must not silently fall back from KMS to local key material."""
    local = social_secret_store_for(
        Settings(environment=Environment.TEST, secret_encryption_key="l" * 32)
    )
    kms = social_secret_store_for(
        Settings(
            environment=Environment.TEST,
            social_secret_backend=SocialSecretBackend.AWS_KMS,
            aws_kms_key_arn=KEY_ARN,
            aws_region="ap-southeast-1",
        ),
        kms_client=FakeKmsClient(),
    )

    assert isinstance(local, LocalSocialSecretStore)
    assert isinstance(kms, AwsKmsSocialSecretStore)
