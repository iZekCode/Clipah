"""Provider-neutral object storage contract plus S3 and deterministic fake adapters."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, BinaryIO, Protocol

from clipah.observability.metrics import count, observe
from clipah.observability.tracing import span


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
    sha256: bytes | None = None
    duration_ms: int | None = None


class MultipartCompletionError(Exception):
    """Raised when a provider rejects submitted multipart part identifiers or ordering."""


class ObjectStoreUnavailableError(Exception):
    """Raised when an object-store provider operation cannot currently complete."""


class ObjectStore(Protocol):
    """Minimal object-store capability required by direct multipart media upload."""

    def create_multipart_upload(self, *, key: str, content_type: str) -> MultipartUpload:
        """Create one private multipart upload for a server-generated key."""

    def put_file(
        self,
        *,
        key: str,
        content_type: str,
        file: BinaryIO,
        sha256: bytes | None = None,
    ) -> StoredObject:
        """Upload one exact server-selected stream without any prefix or listing operation."""

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


class ObservedObjectStore:
    """Time and count every object-store operation without naming a single object.

    The wrapper exists because storage failures are slow rather than loud: an operator
    needs the latency and the failure rate before anybody reports a stalled upload. The
    key is deliberately never recorded, since it is the one part of the operation that
    identifies a Workspace's media.
    """

    def __init__(self, inner: ObjectStore) -> None:
        """Wrap one adapter, keeping its contract exactly."""
        self._inner = inner

    def create_multipart_upload(self, *, key: str, content_type: str) -> MultipartUpload:
        """Create one private multipart upload for a server-generated key."""
        with _observed("create_multipart_upload"):
            return self._inner.create_multipart_upload(key=key, content_type=content_type)

    def put_file(
        self,
        *,
        key: str,
        content_type: str,
        file: BinaryIO,
        sha256: bytes | None = None,
    ) -> StoredObject:
        """Upload one exact server-selected stream without any prefix or listing operation."""
        with _observed("put_file"):
            stored = self._inner.put_file(
                key=key, content_type=content_type, file=file, sha256=sha256
            )
        count("clipah.bytes.uploaded", stored.content_length, operation="put_file")
        return stored

    def sign_upload_part(self, *, upload_id: str, key: str, part_number: int) -> SignedUrl:
        """Sign exactly one numbered part belonging to the recorded upload and key."""
        with _observed("sign_upload_part"):
            return self._inner.sign_upload_part(
                upload_id=upload_id, key=key, part_number=part_number
            )

    def complete_multipart_upload(
        self, *, upload_id: str, key: str, parts: list[CompletedPart]
    ) -> StoredObject:
        """Complete recorded parts and return provider-neutral final object metadata."""
        with _observed("complete_multipart_upload"):
            stored = self._inner.complete_multipart_upload(
                upload_id=upload_id, key=key, parts=parts
            )
        count("clipah.bytes.uploaded", stored.content_length, operation="multipart")
        return stored

    def abort_multipart_upload(self, *, upload_id: str, key: str) -> None:
        """Abort only the exact provider upload and object key recorded by Clipah."""
        with _observed("abort_multipart_upload"):
            self._inner.abort_multipart_upload(upload_id=upload_id, key=key)

    def head_object(self, *, key: str) -> StoredObject:
        """Read final object metadata without exposing provider payload types."""
        with _observed("head_object"):
            return self._inner.head_object(key=key)

    def delete_object(self, *, key: str) -> None:
        """Delete one exact object after a failed final metadata validation."""
        with _observed("delete_object"):
            self._inner.delete_object(key=key)

    def sign_download(self, *, key: str, expires_in: timedelta) -> SignedUrl:
        """Sign a time-bounded private object download."""
        with _observed("sign_download"):
            return self._inner.sign_download(key=key, expires_in=expires_in)


def observed_s3_store(
    *,
    bucket: str,
    endpoint_url: str | None,
    access_key_id: str,
    secret_access_key: str,
) -> ObjectStore:
    """Build the production adapter every process uses, already instrumented."""
    return ObservedObjectStore(
        S3ObjectStore(
            bucket=bucket,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
    )


@contextmanager
def _observed(operation: str) -> Iterator[None]:
    """Record one storage operation's duration and how it ended."""
    started = perf_counter()
    outcome = "failed"
    try:
        with span("storage.operation", operation=operation):
            yield
        outcome = "succeeded"
    finally:
        observe(
            "clipah.storage.duration",
            (perf_counter() - started) * 1000,
            operation=operation,
            outcome=outcome,
        )


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

    def put_file(
        self,
        *,
        key: str,
        content_type: str,
        file: BinaryIO,
        sha256: bytes | None = None,
    ) -> StoredObject:
        """Stream one exact private object to S3 and retain its verified SHA-256."""
        try:
            if sha256 is None:
                self._client.upload_fileobj(
                    file,
                    self._bucket,
                    key,
                    ExtraArgs={"ContentType": content_type},
                )
                return self.head_object(key=key)
            encoded_digest = base64.b64encode(sha256).decode("ascii")
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=file,
                ContentType=content_type,
                ChecksumSHA256=encoded_digest,
                Metadata={"sha256": sha256.hex()},
            )
            provider_digest = _decode_sha256(response.get("ChecksumSHA256"))
            observed = self.head_object(key=key)
            return StoredObject(
                key=observed.key,
                content_type=observed.content_type,
                content_length=observed.content_length,
                sha256=provider_digest,
            )
        except ObjectStoreUnavailableError:
            raise
        except Exception as error:
            raise ObjectStoreUnavailableError("object store upload unavailable") from error

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
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as error:
            raise ObjectStoreUnavailableError("object store metadata unavailable") from error
        metadata = response.get("Metadata")
        digest = _metadata_sha256(metadata)
        return StoredObject(
            key=key,
            content_type=str(response.get("ContentType") or "application/octet-stream"),
            content_length=int(response["ContentLength"]),
            sha256=digest,
        )

    def delete_object(self, *, key: str) -> None:
        """Delete one known object key without prefix or listing operations."""
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def sign_download(self, *, key: str, expires_in: timedelta) -> SignedUrl:
        """Generate a presigned GET URL for the caller-selected bounded lifetime."""
        try:
            url = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=int(expires_in.total_seconds()),
                HttpMethod="GET",
            )
        except Exception as error:
            raise ObjectStoreUnavailableError("object store signing unavailable") from error
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
        self.object_bodies: dict[str, bytes] = {}
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

    def put_file(
        self,
        *,
        key: str,
        content_type: str,
        file: BinaryIO,
        sha256: bytes | None = None,
    ) -> StoredObject:
        """Read one exact fake stream so hashing and retry behavior remain observable."""
        body = file.read()
        observed_digest = hashlib.sha256(body).digest()
        if sha256 is not None and sha256 != observed_digest:
            raise ValueError("fake upload checksum mismatch")
        stored = StoredObject(
            key=key,
            content_type=content_type,
            content_length=len(body),
            sha256=observed_digest if sha256 is not None else None,
        )
        self.object_bodies[key] = body
        self.objects[key] = stored
        return stored

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
        self.object_bodies.pop(key, None)

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


def _decode_sha256(value: object) -> bytes | None:
    """Decode one provider checksum without accepting malformed metadata."""
    if not isinstance(value, str):
        return None
    try:
        digest = base64.b64decode(value, validate=True)
    except ValueError:
        return None
    return digest if len(digest) == hashlib.sha256().digest_size else None


def _metadata_sha256(value: object) -> bytes | None:
    """Read the immutable digest copied into S3 object metadata at upload time."""
    if not isinstance(value, dict):
        return None
    encoded = value.get("sha256")
    if not isinstance(encoded, str) or len(encoded) != hashlib.sha256().digest_size * 2:
        return None
    try:
        return bytes.fromhex(encoded)
    except ValueError:
        return None
