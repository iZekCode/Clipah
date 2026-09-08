"""Provider-neutral values for Workspace Social Accounts and publishing capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class SocialProvider(StrEnum):
    """Official social destinations Clipah can connect for publishing."""

    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


class SocialConnectionStatus(StrEnum):
    """Whether a Social Account may currently authorize provider work."""

    ACTIVE = "active"
    RECONNECT_REQUIRED = "reconnect_required"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class SocialAccountIdentity:
    """Authoritative provider identity normalized before durable storage."""

    external_account_id: str
    display_name: str
    avatar_url: str | None = None
    account_type: str | None = None
    login_family: str = "social_oauth"


@dataclass(frozen=True, slots=True)
class PublishingCapabilities:
    """One versioned safe snapshot of choices a provider currently permits."""

    version: str
    values: MappingProxyType[str, Any] | dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return an isolated JSON-compatible copy for persistence or response shaping."""
        return dict(self.values)


@dataclass(frozen=True, slots=True)
class OAuthGrantMaterial:
    """Decrypted provider credentials whose printed form is always redacted."""

    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    token_type: str = "Bearer"
    access_token_expires_at: datetime | None = None
    refresh_token_expires_at: datetime | None = None

    def __str__(self) -> str:
        """Describe credential metadata without exposing reusable material."""
        return f"OAuthGrantMaterial(token_type={self.token_type!r})"


@dataclass(frozen=True, slots=True)
class OAuthTokenResult:
    """Normalized result of redeeming one provider authorization code."""

    material: OAuthGrantMaterial = field(repr=False)
    granted_scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class OAuthConnectionResult:
    """Validated provider evidence required to persist one Social Account."""

    identity: SocialAccountIdentity
    capabilities: PublishingCapabilities
    material: OAuthGrantMaterial = field(repr=False)
    granted_scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class RefreshedGrant:
    """A provider refresh result plus any newly observed capabilities."""

    material: OAuthGrantMaterial = field(repr=False)
    granted_scopes: frozenset[str]
    capabilities: PublishingCapabilities | None = None
