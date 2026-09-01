"""Unit contracts for provider-specific object-storage normalization."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

import pytest

from clipah.assets.storage import ObjectStoreUnavailableError, S3ObjectStore


class RecordingS3Client:
    """Return checksum-bearing S3 responses while retaining exact request payloads."""

    def __init__(self, body: bytes) -> None:
        """Bind the expected immutable object body."""
        self.body = body
        self.put_arguments: dict[str, Any] = {}

    def put_object(self, **arguments: Any) -> dict[str, str]:
        """Record one direct upload and return its provider-verified checksum."""
        self.put_arguments = arguments
        assert arguments["Body"].read() == self.body
        return {"ChecksumSHA256": base64.b64encode(hashlib.sha256(self.body).digest()).decode()}

    def head_object(self, **arguments: Any) -> dict[str, object]:
        """Return durable object metadata including the submitted immutable digest."""
        assert arguments == {"Bucket": "private", "Key": "derived/proxy"}
        return {
            "ContentType": "video/mp4",
            "ContentLength": len(self.body),
            "Metadata": {"sha256": hashlib.sha256(self.body).hexdigest()},
        }


@pytest.mark.unit
def test_s3_put_file_submits_and_observes_the_exact_sha256() -> None:
    """S3 uploads must bind bytes to a checksum and return verified stored metadata."""
    body = b"complete derivative"
    digest = hashlib.sha256(body).digest()
    client = RecordingS3Client(body)
    store = S3ObjectStore(bucket="private", client=client)

    stored = store.put_file(
        key="derived/proxy",
        content_type="video/mp4",
        file=BytesIO(body),
        sha256=digest,
    )

    assert client.put_arguments["ChecksumSHA256"] == base64.b64encode(digest).decode()
    assert client.put_arguments["Metadata"] == {"sha256": digest.hex()}
    assert stored.content_length == len(body)
    assert stored.sha256 == digest


class UnavailableS3Client:
    """Model a provider transport that fails before issuing a signed capability."""

    def generate_presigned_url(self, *_args: object, **_kwargs: object) -> str:
        """Raise a provider-specific diagnostic that must not escape the adapter."""
        raise RuntimeError("secret provider diagnostic")


@pytest.mark.unit
def test_s3_sign_download_maps_provider_failures_to_a_sanitized_storage_error() -> None:
    """Transient storage failures must be recognizable without leaking provider details."""
    store = S3ObjectStore(
        bucket="private",
        client=UnavailableS3Client(),
        now=lambda: datetime(2026, 9, 1, tzinfo=UTC),
    )

    with pytest.raises(ObjectStoreUnavailableError) as captured:
        store.sign_download(key="source/original", expires_in=timedelta(minutes=5))

    assert str(captured.value) == "object store signing unavailable"
    assert "secret" not in str(captured.value)
