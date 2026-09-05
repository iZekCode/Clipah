"""Envelope encryption for a source credential, and the lease that hands it out.

A cookie jar is stored encrypted under a data key that is itself wrapped by a key this
process never holds in plaintext for longer than one call. These tests hold that shape to
its promises: the ciphertext reveals nothing, the wrapping key is named rather than
embedded, a secret encrypted for one Workspace cannot be opened as another's, and a lease
is a bounded loan rather than a copy that outlives the work it was taken for.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from clipah.source_connectors.secrets import (
    EncryptedSecret,
    LocalSecretStore,
    SecretContext,
    SecretDecryptionError,
    SecretLease,
    SecretLeaseExpiredError,
)

WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_WORKSPACE_ID = UUID("44444444-4444-4444-8444-444444444444")
CONNECTION_ID = UUID("55555555-5555-4555-8555-555555555555")
JOB_ID = UUID("66666666-6666-4666-8666-666666666666")
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
JAR = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1802592000\tSID\tsecret\n"


def store(key: bytes = b"k" * 32) -> LocalSecretStore:
    """One store wrapping data keys under a key held only by this deployment."""
    return LocalSecretStore(key=key, key_reference="local/test-key")


def context(workspace_id: UUID = WORKSPACE_ID) -> SecretContext:
    """The identity a secret is bound to, which is checked again on every open."""
    return SecretContext(workspace_id=workspace_id, connection_id=CONNECTION_ID)


@pytest.mark.unit
def test_a_stored_secret_reveals_nothing_about_what_it_holds() -> None:
    """The ciphertext is what reaches Postgres, so it is what is judged here."""
    encrypted = store().encrypt(JAR, context=context())

    assert isinstance(encrypted, EncryptedSecret)
    assert b"SID" not in encrypted.ciphertext
    assert b"youtube" not in encrypted.ciphertext
    assert b"secret" not in encrypted.ciphertext
    assert encrypted.key_reference == "local/test-key"
    assert len(encrypted.wrapped_key) > 0
    assert len(encrypted.nonce) == 12


@pytest.mark.unit
def test_a_secret_round_trips_through_the_store_that_wrote_it() -> None:
    """A credential nobody can read back is a credential nobody can use."""
    keeper = store()

    encrypted = keeper.encrypt(JAR, context=context())

    assert keeper.decrypt(encrypted, context=context()) == JAR


@pytest.mark.unit
def test_two_encryptions_of_one_secret_never_produce_the_same_bytes() -> None:
    """A repeated ciphertext would tell a reader of the database that nothing changed."""
    keeper = store()

    first = keeper.encrypt(JAR, context=context())
    second = keeper.encrypt(JAR, context=context())

    assert first.ciphertext != second.ciphertext
    assert first.wrapped_key != second.wrapped_key


@pytest.mark.unit
def test_a_secret_belonging_to_one_workspace_cannot_be_opened_as_another() -> None:
    """The Workspace is authenticated data, so a stolen row is useless somewhere else."""
    keeper = store()
    encrypted = keeper.encrypt(JAR, context=context())

    with pytest.raises(SecretDecryptionError):
        keeper.decrypt(encrypted, context=context(OTHER_WORKSPACE_ID))


@pytest.mark.unit
def test_a_tampered_ciphertext_is_refused_rather_than_partially_read() -> None:
    """Authenticated encryption is the point: a changed byte is a refusal, not a guess."""
    keeper = store()
    encrypted = keeper.encrypt(JAR, context=context())
    tampered = EncryptedSecret(
        key_reference=encrypted.key_reference,
        wrapped_key=encrypted.wrapped_key,
        nonce=encrypted.nonce,
        ciphertext=bytes([encrypted.ciphertext[0] ^ 0x01, *encrypted.ciphertext[1:]]),
    )

    with pytest.raises(SecretDecryptionError):
        keeper.decrypt(tampered, context=context())


@pytest.mark.unit
def test_another_deployments_key_cannot_open_this_deployments_secret() -> None:
    """Key material is the boundary, and a wrong key is a refusal rather than noise."""
    encrypted = store().encrypt(JAR, context=context())

    with pytest.raises(SecretDecryptionError):
        store(key=b"j" * 32).decrypt(encrypted, context=context())


@pytest.mark.unit
def test_a_store_refuses_a_key_that_is_not_the_size_the_cipher_needs() -> None:
    """A short key is a misconfiguration, and starting anyway would hide it."""
    with pytest.raises(ValueError):
        LocalSecretStore(key=b"too-short", key_reference="local/test-key")


@pytest.mark.unit
def test_a_lease_is_a_bounded_loan_of_one_secret_for_one_job() -> None:
    """A lease that did not say who took it could not be audited afterwards."""
    lease = SecretLease(
        connection_id=CONNECTION_ID,
        job_id=JOB_ID,
        secret=JAR,
        expires_at=NOW + timedelta(minutes=30),
    )

    assert lease.plaintext(now=NOW) == JAR
    assert lease.connection_id == CONNECTION_ID
    assert lease.job_id == JOB_ID


@pytest.mark.unit
def test_a_lease_stops_answering_once_it_has_expired() -> None:
    """A credential borrowed for one job may not be used after that job's window."""
    lease = SecretLease(
        connection_id=CONNECTION_ID,
        job_id=JOB_ID,
        secret=JAR,
        expires_at=NOW,
    )

    with pytest.raises(SecretLeaseExpiredError):
        lease.plaintext(now=NOW + timedelta(seconds=1))


@pytest.mark.unit
def test_a_lease_never_prints_the_secret_it_holds() -> None:
    """A lease reaches a log line or a traceback, and a credential may not go with it."""
    lease = SecretLease(
        connection_id=CONNECTION_ID,
        job_id=JOB_ID,
        secret=JAR,
        expires_at=NOW + timedelta(minutes=30),
    )

    assert b"SID" not in repr(lease).encode()
    assert "secret" not in str(lease)
    assert str(CONNECTION_ID) in repr(lease)


@pytest.mark.unit
def test_a_discarded_lease_cannot_be_read_again() -> None:
    """Cleanup is a promise: once the work is done the loan is over."""
    lease = SecretLease(
        connection_id=CONNECTION_ID,
        job_id=JOB_ID,
        secret=bytearray(JAR),
        expires_at=NOW + timedelta(minutes=30),
    )

    lease.discard()

    with pytest.raises(SecretLeaseExpiredError):
        lease.plaintext(now=NOW)
