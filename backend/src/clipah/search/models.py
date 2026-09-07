"""The vocabulary of the content library: what is indexed, and what a search answers.

Nothing here touches SQLAlchemy or FastAPI. A search document is a *derived* value —
every field in it can be recomputed from the durable domain rows — so the shapes in this
module describe meaning, never storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid5

# One fixed namespace, so the identifier of a derived document is a function of what it
# describes rather than of when it happened to be written. Two rebuilds therefore produce
# the same rows, and a rebuild is an idempotent operation instead of a churn of new UUIDs.
DOCUMENT_NAMESPACE = UUID("6f2a1c94-1d5e-5a0b-9a2f-6a9f2c0d4b71")

# The searchable text of one document is capped, because a two-hour transcript is not one
# result a person can read — it is a thousand of them, and it is indexed as segments.
MAX_BODY_CHARACTERS = 8_000


class SearchEntityType(StrEnum):
    """What kind of work one search result points at."""

    PROJECT = "project"
    TRANSCRIPT = "transcript"
    CLIP = "clip"
    CAMPAIGN_OUTPUT = "campaign_output"


class SearchLanguage(StrEnum):
    """The language a document's text is treated as when it is stemmed.

    Only the two languages this product transcribes and writes copy in get their own
    stemmer. Everything else is indexed by its literal tokens rather than guessed at.
    """

    INDONESIAN = "id"
    ENGLISH = "en"
    OTHER = "other"

    @property
    def text_search_config(self) -> str:
        """Name the Postgres text-search configuration this language is stemmed with."""
        return {
            SearchLanguage.INDONESIAN: "indonesian",
            SearchLanguage.ENGLISH: "english",
            SearchLanguage.OTHER: "simple",
        }[self]

    @classmethod
    def parse(cls, value: str | None) -> SearchLanguage:
        """Resolve a stored provider language tag onto a stemmer this product supports."""
        if value is None:
            return cls.OTHER
        primary = value.strip().lower().replace("_", "-").split("-")[0]
        try:
            return cls(primary)
        except ValueError:
            return cls.OTHER


class ExportState(StrEnum):
    """Whether the work a document describes has left Clipah as a rendered file."""

    NOT_EXPORTED = "not_exported"
    EXPORTED = "exported"


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    """One derived, searchable record of a single piece of Workspace work."""

    workspace_id: UUID
    project_id: UUID
    entity_type: SearchEntityType
    entity_id: UUID
    # What a member opens when they click the result: the indexed row itself, except for
    # campaign copy, which is read on the Clip Candidate it was written for.
    anchor_id: UUID
    segment_ordinal: int
    title: str
    body: str
    speaker: str | None
    topics: tuple[str, ...]
    tags: tuple[str, ...]
    language: SearchLanguage
    start_ms: int | None
    end_ms: int | None
    export_state: ExportState
    source_created_at: datetime

    @property
    def document_id(self) -> UUID:
        """Derive this document's stable identifier from the row it describes."""
        return uuid5(
            DOCUMENT_NAMESPACE,
            f"{self.workspace_id}:{self.entity_type.value}:{self.entity_id}:{self.segment_ordinal}",
        )


@dataclass(frozen=True, slots=True)
class TextFragment:
    """One run of result text, marked for whether it matched what was searched for.

    Fragments exist so a highlight can be shown without a server ever sending markup: the
    client decides how a matched run looks, and provider text stays text.
    """

    text: str
    highlighted: bool


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One result: what it is, where it lives, and enough evidence to act on it."""

    document_id: UUID
    entity_type: SearchEntityType
    entity_id: UUID
    project_id: UUID
    project_name: str
    title: str
    fragments: tuple[TextFragment, ...]
    speaker: str | None
    topics: tuple[str, ...]
    tags: tuple[str, ...]
    language: SearchLanguage
    start_ms: int | None
    end_ms: int | None
    export_state: ExportState
    created_at: datetime
    score: float
    deep_link: str


@dataclass(frozen=True, slots=True)
class HitBoundary:
    """The last hit of a page, so the next page resumes exactly where this one stopped."""

    score: float
    document_id: UUID


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """One member's question, and every narrowing they asked for alongside it."""

    text: str
    entity_types: tuple[SearchEntityType, ...] = ()
    project_id: UUID | None = None
    speaker: str | None = None
    topic: str | None = None
    language: SearchLanguage | None = None
    export_state: ExportState | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    limit: int = 20
    after: HitBoundary | None = None


@dataclass(frozen=True, slots=True)
class SearchPage:
    """One ranked page of results and the boundary the next page resumes from."""

    hits: tuple[SearchHit, ...] = field(default_factory=tuple)
    next_boundary: HitBoundary | None = None
