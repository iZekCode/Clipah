"""Immutable provider-neutral values for durable social Publications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PublicationStatus(StrEnum):
    """One destination's durable lifecycle, independent from every sibling."""

    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    SCHEDULED = "scheduled"
    PREFLIGHTING = "preflighting"
    TRANSFERRING = "transferring"
    PROCESSING = "processing"
    PUBLISHED = "published"
    RETRYABLE_FAILED = "retryable_failed"
    RECONNECT_REQUIRED = "reconnect_required"
    PERMANENT_FAILED = "permanent_failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class PublicationTransition:
    """An accepted move whose previous state remains explicit for audit evidence."""

    previous: PublicationStatus
    current: PublicationStatus


class PublicationDestinationDraft(BaseModel):
    """Strict user choices for one explicitly selected Social Account."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    social_account_id: UUID
    metadata: dict[str, Any] = Field(default_factory=dict)
    provider_options: dict[str, Any] = Field(default_factory=dict)
    consent: dict[str, Any] = Field(default_factory=dict)
    scheduled_for: datetime | None = None
    display_timezone: str = Field(min_length=1, max_length=255)

    @field_validator("scheduled_for")
    @classmethod
    def require_aware_schedule(cls, value: datetime | None) -> datetime | None:
        """Reject a local time whose UTC instant cannot be reproduced."""
        if value is not None and value.utcoffset() is None:
            raise ValueError("scheduled_for must include an offset")
        return value


@dataclass(frozen=True, slots=True)
class PublicationSummary:
    """One destination state safe to return without provider secrets."""

    publication_id: UUID
    social_account_id: UUID
    status: PublicationStatus
    scheduled_for: datetime | None
    display_timezone: str


@dataclass(frozen=True, slots=True)
class PublicationBatchSummary:
    """One convenience grouping whose children retain independent state."""

    batch_id: UUID
    edit_revision_id: UUID
    render_artifact_id: UUID
    publications: tuple[PublicationSummary, ...]
