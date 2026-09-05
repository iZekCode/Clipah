"""Envelope encryption for source credentials, and the leases that hand them out.

A cookie jar is encrypted under a fresh data key, and that data key is wrapped by a key
this deployment holds — locally today, and by a key-management service wherever one is
configured. Postgres therefore stores a wrapped key and a ciphertext, and neither is
useful without the wrapping key.

Two properties are worth stating.

* **A secret is bound to who it belongs to.** The Workspace and the Connection are
  authenticated data, so a row copied into another Workspace's table cannot be opened
  there: the decryption fails rather than returning something plausible.
* **A lease is a loan, not a copy.** It names the Job that took it, stops answering when
  its window closes or when it is discarded, and never prints what it holds — a lease
  ends up in tracebacks and log lines, and a credential may not go with it.

Nothing here reads a database or a clock: the caller supplies the instant a lease is
judged against, and the store is injected, so the same inputs always answer the same way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

DATA_KEY_BYTES = 32
NONCE_BYTES = 12
# The label that separates this key's use from every other use of the same material.
KEY_DERIVATION_INFO = b"clipah.source-connection.secret-wrapping.v1"


class SecretDecryptionError(Exception):
    """A stored secret that cannot be opened with this key, in this context, unaltered."""


class SecretLeaseExpiredError(Exception):
    """A lease that has run out, or has already been given back."""


@dataclass(frozen=True, slots=True)
class SecretContext:
    """Who a secret belongs to, checked again every time it is opened."""

    workspace_id: UUID
    connection_id: UUID

    def as_associated_data(self) -> bytes:
        """Spell this identity as the authenticated data the cipher binds to."""
        return f"workspace={self.workspace_id};connection={self.connection_id}".encode()


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    """One stored secret: a wrapped data key, a nonce, and the ciphertext itself."""

    key_reference: str
    wrapped_key: bytes
    nonce: bytes
    ciphertext: bytes


class SecretStore(Protocol):
    """Encrypt and decrypt one secret, wrapping its data key with a managed key."""

    def encrypt(self, plaintext: bytes, *, context: SecretContext) -> EncryptedSecret:
        """Seal one secret for one Workspace and Connection."""

    def decrypt(self, secret: EncryptedSecret, *, context: SecretContext) -> bytes:
        """Open one secret, or refuse it."""


class LocalSecretStore:
    """Wrap data keys with a key this deployment holds in its own configuration.

    This is the store a local or single-tenant deployment uses. The shape is deliberately
    the same as a managed one: a data key per secret, wrapped separately, so moving to a
    key-management service changes which key does the wrapping and nothing else.
    """

    def __init__(self, *, key: bytes, key_reference: str) -> None:
        """Bind one wrapping key, refusing a key that is not the size the cipher needs."""
        if len(key) != DATA_KEY_BYTES:
            raise ValueError(f"a wrapping key must be {DATA_KEY_BYTES} bytes")
        self._cipher = AESGCM(key)
        self._key_reference = key_reference

    def encrypt(self, plaintext: bytes, *, context: SecretContext) -> EncryptedSecret:
        """Seal one secret under a fresh data key, and wrap that key in turn."""
        data_key = os.urandom(DATA_KEY_BYTES)
        nonce = os.urandom(NONCE_BYTES)
        wrap_nonce = os.urandom(NONCE_BYTES)
        associated = context.as_associated_data()
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, associated)
        wrapped = self._cipher.encrypt(wrap_nonce, data_key, associated)
        return EncryptedSecret(
            key_reference=self._key_reference,
            wrapped_key=wrap_nonce + wrapped,
            nonce=nonce,
            ciphertext=ciphertext,
        )

    def decrypt(self, secret: EncryptedSecret, *, context: SecretContext) -> bytes:
        """Unwrap the data key and open the secret, refusing anything that does not fit."""
        associated = context.as_associated_data()
        try:
            data_key = self._cipher.decrypt(
                secret.wrapped_key[:NONCE_BYTES], secret.wrapped_key[NONCE_BYTES:], associated
            )
            return AESGCM(data_key).decrypt(secret.nonce, secret.ciphertext, associated)
        except (InvalidTag, ValueError):
            raise SecretDecryptionError("this secret cannot be opened here") from None


@dataclass(slots=True)
class SecretLease:
    """One credential, borrowed for one Job, until one instant.

    The secret is held as bytes and given back through :meth:`plaintext`, which refuses
    once the window has closed or the lease has been discarded. Neither ``repr`` nor
    ``str`` includes it, because both reach places a credential must not.
    """

    connection_id: UUID
    job_id: UUID
    expires_at: datetime
    secret: bytes | bytearray = field(repr=False)
    _discarded: bool = field(default=False, repr=False)

    def plaintext(self, *, now: datetime) -> bytes:
        """Return the borrowed secret, or refuse because the loan is over."""
        if self._discarded or now > self.expires_at:
            raise SecretLeaseExpiredError(str(self.connection_id))
        return bytes(self.secret)

    def discard(self) -> None:
        """Give the loan back, overwriting the bytes where the buffer allows it."""
        if isinstance(self.secret, bytearray):
            for index in range(len(self.secret)):
                self.secret[index] = 0
        self.secret = b""
        self._discarded = True

    def __str__(self) -> str:
        """Describe the loan without describing what was lent."""
        return f"SecretLease(connection={self.connection_id}, job={self.job_id})"


def local_secret_store(
    material: str, *, key_reference: str = "local/source-connection-v1"
) -> LocalSecretStore:
    """Build a store from configured key material, deriving a key of the size it needs.

    The material is a deployment secret rather than a key, so it is stretched through
    HKDF with a label naming this one use: the same string configured for something else
    can never produce the same wrapping key.
    """
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=DATA_KEY_BYTES,
        salt=None,
        info=KEY_DERIVATION_INFO,
    ).derive(material.encode("utf-8"))
    return LocalSecretStore(key=derived, key_reference=key_reference)
