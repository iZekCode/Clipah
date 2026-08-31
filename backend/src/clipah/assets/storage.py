"""Provider-neutral object storage contract plus S3 and deterministic fake adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


@dataclass(frozen=True)
class MultipartUpload:
    """Opaque provider multipart-upload handle kept out of ordinary API metadata."""

    upload_id: str


@dataclass(frozen=True)
class SignedUrl:
    """Short-lived URL issued for a narrowly scoped storage operation."""

    url: str
    expires_at: datetime


@dataclass(frozen=True)
class CompletedPart:
    """One client-observed part identity accepted by a multipart completion request."""

    part_number: int
    etag: str


@dataclass(frozen=True)
class StoredObject:
    """Provider-neutral metadata used to validate a completed private object."""

    key: str
    content_type: str
    content_length: int


class MultipartCompletionError(Exception):
    """Raised when a provider rejects submitted multipart part identifiers or ordering."""


class ObjectStore(Protocol):
    """Minimal object-store capability required by direct multipart media upload."""

    def create_multipart_upload(self, *, key: str, content_type: str) -> MultipartUpload:
        """Create one private multipart upload for a server-generated key."""

    def sign_upload_part(self, *, upload_id: str, key: str, part_number: int) -> SignedUrl:
        """Sign exactly one numbered part belonging to the recorded upload and key."""

    def complete_multipart_upload(
        self, *, upload_id: str, key: str, parts: list[CompletedPart]
    ) -> StoredObject:
        """Complete recorded parts and return provider-neutral final object metadata."""

    def abort_multipart_upload(self, *, upload_id: str, key: str) -> None:
        """Abort only the exact provider upload and object key recorded by Clipah."""

    def head_object(self, *, key: str) -> StoredObject:
        """Read final object metadata without exposing provider payload types."""

    def delete_object(self, *, key: str) -> None:
        """Delete one exact object after a failed final metadata validation."""

    def sign_download(self, *, key: str, expires_in: timedelta) -> SignedUrl:
        """Sign a time-bounded private object download."""


class S3ObjectStore:
    """S3-compatible adapter that contains all boto3 provider payload handling."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        client: Any | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """Bind one bucket and optional S3-compatible endpoint to this adapter."""
        if client is None:
            import boto3  # type: ignore[import-untyped]
            from botocore.config import Config  # type: ignore[import-untyped]

            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                config=Config(s3={"addressing_style": "path"}),
            )
        self._bucket = bucket
        self._client = client
        self._now = now or _utc_now

    def create_multipart_upload(self, *, key: str, content_type: str) -> MultipartUpload:
        """Create one S3 multipart upload while retaining only its opaque identifier."""
        response = self._client.create_multipart_upload(
            Bucket=self._bucket, Key=key, ContentType=content_type
        )
        return MultipartUpload(upload_id=str(response["UploadId"]))

    def sign_upload_part(self, *, upload_id: str, key: str, part_number: int) -> SignedUrl:
        """Generate a five-minute presigned URL for one S3 upload part."""
        expires_in = timedelta(minutes=5)
        url = self._client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=int(expires_in.total_seconds()),
            HttpMethod="PUT",
        )
        return SignedUrl(url=str(url), expires_at=self._now() + expires_in)

    def complete_multipart_upload(
        self, *, upload_id: str, key: str, parts: list[CompletedPart]
    ) -> StoredObject:
        """Complete S3 parts then normalize its metadata through one head request."""
        try:
            self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={
                    "Parts": [{"PartNumber": part.part_number, "ETag": part.etag} for part in parts]
                },
            )
        except Exception as error:
            if _is_invalid_multipart_completion(error):
                raise MultipartCompletionError("provider rejected multipart completion") from error
            raise
        return self.head_object(key=key)

    def abort_multipart_upload(self, *, upload_id: str, key: str) -> None:
        """Abort only the S3 upload ID and key supplied by the durable record."""
        self._client.abort_multipart_upload(Bucket=self._bucket, Key=key, UploadId=upload_id)

    def head_object(self, *, key: str) -> StoredObject:
        """Translate S3 head metadata into the provider-neutral object value."""
        response = self._client.head_object(Bucket=self._bucket, Key=key)
        return StoredObject(
            key=key,
            content_type=str(response.get("ContentType") or "application/octet-stream"),
            content_length=int(response["ContentLength"]),
        )

    def delete_object(self, *, key: str) -> None:
        """Delete one known object key without prefix or listing operations."""
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def sign_download(self, *, key: str, expires_in: timedelta) -> SignedUrl:
        """Generate a presigned GET URL for the caller-selected bounded lifetime."""
        url = self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=int(expires_in.total_seconds()),
            HttpMethod="GET",
        )
        return SignedUrl(url=str(url), expires_at=self._now() + expires_in)


class FakeObjectStore:
    """Deterministic in-memory object store for isolated domain and HTTP tests."""

    def __init__(self, *, now: Callable[[], datetime]) -> None:
        """Use an injected clock so signed URL expiry assertions never read wall time."""
        self._now = now
        self._next_upload = 0
        self.upload_keys: dict[str, str] = {}
        self.upload_content_types: dict[str, str] = {}
        self.upload_parts: dict[str, dict[int, int]] = {}
        self.completed_parts: list[tuple[int, ...]] = []
        self.objects: dict[str, StoredObject] = {}
        self.aborted: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def create_multipart_upload(self, *, key: str, content_type: str) -> MultipartUpload:
        """Allocate a reproducible fake upload identifier for one exact key."""
        self._next_upload += 1
        upload_id = f"fake-upload-{self._next_upload}"
        self.upload_keys[upload_id] = key
        self.upload_content_types[upload_id] = content_type
        self.upload_parts[upload_id] = {}
        return MultipartUpload(upload_id=upload_id)

    def sign_upload_part(self, *, upload_id: str, key: str, part_number: int) -> SignedUrl:
        """Return a fake URL only when the caller retains the exact stored binding."""
        self._assert_upload_binding(upload_id=upload_id, key=key)
        return SignedUrl(
            url=f"fake://multipart/{upload_id}/{part_number}",
            expires_at=self._now() + timedelta(minutes=5),
        )

    def put_multipart_part(self, *, upload_id: str, part_number: int, size_bytes: int) -> None:
        """Make a fake uploaded part available to the ensuing completion operation."""
        self.upload_parts[upload_id][part_number] = size_bytes

    def complete_multipart_upload(
        self, *, upload_id: str, key: str, parts: list[CompletedPart]
    ) -> StoredObject:
        """Join supplied fake parts into one object and expose normalized metadata."""
        self._assert_upload_binding(upload_id=upload_id, key=key)
        try:
            content_length = sum(self.upload_parts[upload_id][part.part_number] for part in parts)
        except KeyError as error:
            raise MultipartCompletionError("fake part is unavailable") from error
        self.completed_parts.append(tuple(part.part_number for part in parts))
        stored = StoredObject(
            key=key,
            content_type=self.upload_content_types[upload_id],
            content_length=content_length,
        )
        self.objects[key] = stored
        return stored

    def abort_multipart_upload(self, *, upload_id: str, key: str) -> None:
        """Record an exact fake abort target so cleanup scope remains observable."""
        self._assert_upload_binding(upload_id=upload_id, key=key)
        self.aborted.append((upload_id, key))

    def head_object(self, *, key: str) -> StoredObject:
        """Return fake final metadata or raise when completion did not create the object."""
        return self.objects[key]

    def delete_object(self, *, key: str) -> None:
        """Remove exactly one fake object after failed final validation."""
        self.deleted.append(key)
        self.objects.pop(key, None)

    def sign_download(self, *, key: str, expires_in: timedelta) -> SignedUrl:
        """Return a fake download URL that preserves requested expiration semantics."""
        self.head_object(key=key)
        return SignedUrl(url=f"fake://download/{key}", expires_at=self._now() + expires_in)

    def _assert_upload_binding(self, *, upload_id: str, key: str) -> None:
        """Reject accidental cleanup/signing that widens beyond the recorded fake target."""
        if self.upload_keys.get(upload_id) != key:
            raise ValueError("unknown multipart upload binding")


def _utc_now() -> datetime:
    """Supply timezone-aware UTC timestamps for production-only presigned URL calculations."""
    return datetime.now(tz=UTC)


def _is_invalid_multipart_completion(error: Exception) -> bool:
    """Recognize only provider errors safe to expose as fixed completion validation failures."""
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return False
    details = response.get("Error")
    return isinstance(details, dict) and details.get("Code") in {"InvalidPart", "InvalidPartOrder"}
