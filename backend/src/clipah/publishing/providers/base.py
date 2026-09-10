"""Provider-neutral contracts for publishing frozen media."""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import SecretStr

from clipah.social_accounts.models import SocialProvider

RequestT = TypeVar("RequestT", contravariant=True)
CheckpointT = TypeVar("CheckpointT", covariant=True)
StatusT = TypeVar("StatusT", covariant=True)


class PublicationMedia(Protocol):
    """Immutable provider-ready bytes exposed without a private storage key."""

    @property
    def content_type(self) -> str:
        """Return the validated media content type."""

    @property
    def size_bytes(self) -> int:
        """Return the exact immutable byte length."""

    @property
    def sha256(self) -> bytes:
        """Return the verified immutable content checksum."""

    def read_range(self, start: int, end: int) -> bytes:
        """Read bytes in the half-open range ``start:end``."""


@runtime_checkable
class SocialPublisher(Protocol[RequestT, CheckpointT, StatusT]):
    """Minimum operations shared by official social publishing adapters."""

    provider: SocialProvider

    def confirm_destination(self, *, access_token: SecretStr, expected_account_id: str) -> None:
        """Prove the live credential still names the frozen destination."""

    def begin(
        self, *, request: RequestT, media: PublicationMedia, access_token: SecretStr
    ) -> CheckpointT:
        """Begin one provider transfer from immutable request and media values."""

    def poll(self, *, provider_id: str, access_token: SecretStr) -> StatusT:
        """Read the authoritative provider state for one created resource."""
