"""Envelope encryption and bounded plaintext leases for Social Account OAuth Grants."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from clipah.config import Settings, SocialSecretBackend
from clipah.social_accounts.models import SocialProvider

DATA_KEY_BYTES = 32
NONCE_BYTES = 12
KEY_DERIVATION_INFO = b"clipah.social-oauth.secret-wrapping.v1"


class SocialSecretDecryptionError(Exception):
    """Encrypted OAuth Grant material cannot be trusted or opened in this context."""


class SocialSecretUnavailableError(SocialSecretDecryptionError):
    """The configured wrapping-key backend cannot serve the required key version."""


class OAuthGrantLeaseExpiredError(Exception):
    """A bounded OAuth Grant loan is expired, used, or discarded."""


@dataclass(frozen=True, slots=True)
class OAuthSecretContext:
    """Durable identity authenticated into one encrypted OAuth Grant."""

    workspace_id: UUID
    social_account_id: UUID
    grant_id: UUID
    provider: SocialProvider
    token_version: int

    def as_associated_data(self) -> bytes:
        """Encode the complete ownership and rotation identity for authenticated encryption."""
        return (
            f"workspace={self.workspace_id};account={self.social_account_id};"
            f"grant={self.grant_id};provider={self.provider.value};version={self.token_version}"
        ).encode("ascii")

    def as_kms_encryption_context(self) -> dict[str, str]:
        """Bind KMS operations to the same durable identity as the Grant ciphertext."""
        return {
            "workspace_id": str(self.workspace_id),
            "social_account_id": str(self.social_account_id),
            "grant_id": str(self.grant_id),
            "provider": self.provider.value,
            "token_version": str(self.token_version),
        }


@dataclass(frozen=True, slots=True)
class EncryptedOAuthGrant:
    """Ciphertext plus opaque wrapping-key metadata safe to persist."""

    key_reference: str
    key_version: int
    wrapped_key: bytes
    nonce: bytes
    ciphertext: bytes


class SocialSecretStore:
    """Envelope-encrypt OAuth Grant documents under a configured wrapping key."""

    def encrypt(self, plaintext: bytes, *, context: OAuthSecretContext) -> EncryptedOAuthGrant:
        """Seal one canonical grant document for its exact durable context."""
        raise NotImplementedError

    def decrypt(self, secret: EncryptedOAuthGrant, *, context: OAuthSecretContext) -> bytearray:
        """Open one grant into a mutable buffer or fail closed."""
        raise NotImplementedError


class AwsKmsClient(Protocol):
    """The two AWS KMS operations allowed by the Social Account secret boundary."""

    def generate_data_key(self, **request: object) -> Mapping[str, Any]:
        """Generate one plaintext data key and its KMS-wrapped representation."""
        ...

    def decrypt(self, **request: object) -> Mapping[str, Any]:
        """Open one KMS-wrapped data key under its authenticated context."""
        ...


class AwsKmsSocialSecretStore(SocialSecretStore):
    """Envelope-encrypt OAuth Grants with one AWS KMS data key per Grant version."""

    def __init__(self, *, client: AwsKmsClient, key_arn: str) -> None:
        """Bind one exact KMS key rather than accepting provider-selected aliases."""
        if not key_arn.startswith("arn:aws:kms:"):
            raise ValueError("an AWS KMS key ARN is required")
        self._client = client
        self._key_arn = key_arn

    def encrypt(self, plaintext: bytes, *, context: OAuthSecretContext) -> EncryptedOAuthGrant:
        """Encrypt a Grant locally and persist only the KMS-wrapped data key."""
        try:
            response = self._client.generate_data_key(
                KeyId=self._key_arn,
                KeySpec="AES_256",
                EncryptionContext=context.as_kms_encryption_context(),
            )
        except (BotoCoreError, ClientError) as error:
            raise SocialSecretUnavailableError("OAuth Grant key is unavailable") from error
        key = _kms_plaintext_key(response)
        wrapped_key = response.get("CiphertextBlob")
        if not isinstance(wrapped_key, bytes) or not wrapped_key:
            _zero(key)
            raise SocialSecretUnavailableError("OAuth Grant key is unavailable")
        nonce = os.urandom(NONCE_BYTES)
        try:
            ciphertext = AESGCM(bytes(key)).encrypt(nonce, plaintext, context.as_associated_data())
        finally:
            _zero(key)
        return EncryptedOAuthGrant(
            key_reference=self._key_arn,
            key_version=1,
            wrapped_key=wrapped_key,
            nonce=nonce,
            ciphertext=ciphertext,
        )

    def decrypt(self, secret: EncryptedOAuthGrant, *, context: OAuthSecretContext) -> bytearray:
        """Open a Grant only through the configured key and exact authenticated context."""
        if secret.key_reference != self._key_arn:
            raise SocialSecretUnavailableError("OAuth Grant key is unavailable")
        try:
            response = self._client.decrypt(
                CiphertextBlob=secret.wrapped_key,
                KeyId=self._key_arn,
                EncryptionAlgorithm="SYMMETRIC_DEFAULT",
                EncryptionContext=context.as_kms_encryption_context(),
            )
        except (BotoCoreError, ClientError) as error:
            raise SocialSecretUnavailableError("OAuth Grant key is unavailable") from error
        key = _kms_plaintext_key(response)
        try:
            plaintext = AESGCM(bytes(key)).decrypt(
                secret.nonce, secret.ciphertext, context.as_associated_data()
            )
        except (InvalidTag, ValueError):
            raise SocialSecretDecryptionError("OAuth Grant cannot be opened") from None
        finally:
            _zero(key)
        return bytearray(plaintext)


def _kms_plaintext_key(response: Mapping[str, Any]) -> bytearray:
    """Copy one complete AES-256 key into memory that this process can overwrite."""
    plaintext = response.get("Plaintext")
    if not isinstance(plaintext, (bytes, bytearray)) or len(plaintext) != DATA_KEY_BYTES:
        raise SocialSecretUnavailableError("OAuth Grant key is unavailable")
    return bytearray(plaintext)


def _zero(value: bytearray) -> None:
    """Overwrite one mutable secret working copy before releasing it."""
    for index in range(len(value)):
        value[index] = 0


class LocalSocialSecretStore(SocialSecretStore):
    """Envelope encryption using a deployment-held local wrapping key."""

    def __init__(
        self,
        *,
        key: bytes,
        key_reference: str,
        key_version: int,
        previous_keys: Mapping[tuple[str, int], bytes] | None = None,
    ) -> None:
        """Bind an exact wrapping-key identity and reject invalid key metadata."""
        if len(key) != DATA_KEY_BYTES:
            raise ValueError(f"a wrapping key must be {DATA_KEY_BYTES} bytes")
        if key_version <= 0:
            raise ValueError("a wrapping key version must be positive")
        self._key_reference = key_reference
        self._key_version = key_version
        keys = dict(previous_keys or {})
        keys[(key_reference, key_version)] = key
        if any(len(candidate) != DATA_KEY_BYTES for candidate in keys.values()):
            raise ValueError(f"a wrapping key must be {DATA_KEY_BYTES} bytes")
        if any(version <= 0 for _, version in keys):
            raise ValueError("a wrapping key version must be positive")
        self._ciphers = {identity: AESGCM(material) for identity, material in keys.items()}

    def encrypt(self, plaintext: bytes, *, context: OAuthSecretContext) -> EncryptedOAuthGrant:
        """Encrypt under a fresh data key and wrap that key separately."""
        data_key = os.urandom(DATA_KEY_BYTES)
        nonce = os.urandom(NONCE_BYTES)
        wrap_nonce = os.urandom(NONCE_BYTES)
        associated = context.as_associated_data()
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, associated)
        wrapped_key = self._ciphers[(self._key_reference, self._key_version)].encrypt(
            wrap_nonce, data_key, associated
        )
        return EncryptedOAuthGrant(
            key_reference=self._key_reference,
            key_version=self._key_version,
            wrapped_key=wrap_nonce + wrapped_key,
            nonce=nonce,
            ciphertext=ciphertext,
        )

    def decrypt(self, secret: EncryptedOAuthGrant, *, context: OAuthSecretContext) -> bytearray:
        """Open authenticated ciphertext without exposing failure detail."""
        cipher = self._ciphers.get((secret.key_reference, secret.key_version))
        if cipher is None:
            raise SocialSecretUnavailableError("OAuth Grant key is unavailable")
        associated = context.as_associated_data()
        try:
            data_key = cipher.decrypt(
                secret.wrapped_key[:NONCE_BYTES],
                secret.wrapped_key[NONCE_BYTES:],
                associated,
            )
            plaintext = AESGCM(data_key).decrypt(secret.nonce, secret.ciphertext, associated)
        except (InvalidTag, ValueError):
            raise SocialSecretDecryptionError("OAuth Grant cannot be opened") from None
        return bytearray(plaintext)


def local_social_secret_store(
    material: str,
    *,
    key_reference: str = "local/social-oauth",
    key_version: int = 1,
) -> LocalSocialSecretStore:
    """Derive a social-specific wrapping key from configured deployment material."""
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=DATA_KEY_BYTES,
        salt=None,
        info=KEY_DERIVATION_INFO,
    ).derive(material.encode("utf-8"))
    return LocalSocialSecretStore(key=key, key_reference=key_reference, key_version=key_version)


def social_secret_store_for(
    settings: Settings, *, kms_client: AwsKmsClient | None = None
) -> SocialSecretStore | None:
    """Build only the wrapping-key backend explicitly selected by this process."""
    if settings.social_secret_backend is SocialSecretBackend.LOCAL:
        if settings.secret_encryption_key is None:
            return None
        return local_social_secret_store(settings.secret_encryption_key.get_secret_value())
    if settings.aws_kms_key_arn is None or settings.aws_region is None:
        raise SocialSecretUnavailableError("OAuth Grant key is unavailable")
    client = kms_client or cast(AwsKmsClient, boto3.client("kms", region_name=settings.aws_region))
    return AwsKmsSocialSecretStore(client=client, key_arn=settings.aws_kms_key_arn)


@dataclass(slots=True)
class OAuthGrantLease:
    """One OAuth Grant loan that can be opened for exactly one bounded operation."""

    social_account_id: UUID
    operation_id: UUID
    expires_at: datetime
    secret: bytearray = field(repr=False)
    _used: bool = field(default=False, init=False, repr=False)

    @contextmanager
    def open(self, *, now: datetime) -> Iterator[bytearray]:
        """Yield plaintext once and overwrite it whenever the operation ends."""
        if self._used or now > self.expires_at:
            self.discard()
            raise OAuthGrantLeaseExpiredError("OAuth Grant lease is unavailable")
        self._used = True
        try:
            yield self.secret
        finally:
            self.discard()

    def discard(self) -> None:
        """Overwrite the mutable loan and make every later open fail."""
        for index in range(len(self.secret)):
            self.secret[index] = 0
        self._used = True

    def __str__(self) -> str:
        """Describe the loan without describing what it contains."""
        return f"OAuthGrantLease(account={self.social_account_id}, operation={self.operation_id})"
