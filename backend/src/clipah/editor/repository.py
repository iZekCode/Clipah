"""Private persistence for Edits and their immutable Revision history.

Every read here is bounded by the Workspace the caller already proved standing in, and
every write goes through the one lock that decides which of two concurrent saves becomes
the next Revision. SQLAlchemy details stay inside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, select, text, update
from sqlalchemy.orm import Session

from clipah.models import (
    Asset,
    ClipCandidate,
    ClipEdit,
    ClipEditRevision,
    Project,
    ProjectStatus,
    Transcript,
)

_EDIT_CREATION_LOCK_NAMESPACE = 0x0C11_ED17


@dataclass(frozen=True, slots=True)
class SeedWord:
    """One transcript word, as the first composition will draw it."""

    word_id: str
    text: str
    start_ms: int
    end_ms: int
    speaker: str


@dataclass(frozen=True, slots=True)
class CandidateSeed:
    """Everything the first Revision of a reviewed candidate is derived from."""

    project_id: UUID
    candidate_id: UUID
    source_asset_id: UUID
    start_ms: int
    end_ms: int
    words: tuple[SeedWord, ...]


@dataclass(frozen=True, slots=True)
class EditLock:
    """The locked facts that decide whether one save may become the next Revision."""

    edit_id: UUID
    project_id: UUID
    current_revision: int
    composition_hash: bytes


@dataclass(frozen=True, slots=True)
class EditDetail:
    """One Edit and the composition its current Revision holds."""

    edit_id: UUID
    project_id: UUID
    candidate_id: UUID
    current_revision: int
    composition: dict[str, Any]
    composition_hash: bytes
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RevisionSummary:
    """One entry of an Edit's history, without repeating its whole composition."""

    revision: int
    composition_hash: bytes
    created_by_user_id: UUID
    created_at: datetime


class EditRepository:
    """Keep Edit and Revision ORM details behind one tenant-scoped boundary."""

    def __init__(self, session: Session) -> None:
        """Bind persistence to the request transaction holding verified RLS context."""
        self._session = session

    def lock_candidate(self, *, workspace_id: UUID, candidate_id: UUID) -> None:
        """Serialize Edit creation for one candidate so two clicks cannot fork its history."""
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:subject))"),
            {
                "namespace": _EDIT_CREATION_LOCK_NAMESPACE,
                "subject": f"{workspace_id}:{candidate_id}",
            },
        )

    def candidate_seed(
        self, *, workspace_id: UUID, project_id: UUID, candidate_id: UUID
    ) -> CandidateSeed | None:
        """Read one reviewable candidate, its source Asset, and the words it covers."""
        row = self._session.execute(
            select(ClipCandidate, Transcript)
            .join(
                Project,
                (Project.workspace_id == ClipCandidate.workspace_id)
                & (Project.id == ClipCandidate.project_id),
            )
            .join(
                Transcript,
                (Transcript.workspace_id == ClipCandidate.workspace_id)
                & (Transcript.id == ClipCandidate.transcript_id),
            )
            .where(
                ClipCandidate.workspace_id == workspace_id,
                ClipCandidate.project_id == project_id,
                ClipCandidate.id == candidate_id,
                ClipCandidate.model_metadata["exposed"].as_boolean().is_(True),
                Project.archived_at.is_(None),
                Project.status == ProjectStatus.READY,
            )
        ).first()
        if row is None:
            return None
        candidate, transcript = row
        source_asset_id = self._session.scalar(
            select(Asset.id).where(
                Asset.workspace_id == workspace_id,
                Asset.project_id == project_id,
                Asset.id == transcript.asset_id,
            )
        )
        if source_asset_id is None:
            return None
        return CandidateSeed(
            project_id=project_id,
            candidate_id=candidate_id,
            source_asset_id=source_asset_id,
            start_ms=candidate.start_ms,
            end_ms=candidate.end_ms,
            words=_seed_words(transcript.words, candidate.start_ms, candidate.end_ms),
        )

    def project_asset_ids(self, *, workspace_id: UUID, project_id: UUID) -> frozenset[UUID]:
        """Report every Asset this Project owns, which is what a composition may use."""
        return frozenset(
            self._session.scalars(
                select(Asset.id).where(
                    Asset.workspace_id == workspace_id, Asset.project_id == project_id
                )
            )
        )

    def edit_for_candidate(self, *, workspace_id: UUID, candidate_id: UUID) -> EditDetail | None:
        """Return the one Edit already opened for this candidate, if there is one."""
        edit_id = self._session.scalar(
            select(ClipEdit.id).where(
                ClipEdit.workspace_id == workspace_id, ClipEdit.candidate_id == candidate_id
            )
        )
        if edit_id is None:
            return None
        return self.detail(workspace_id=workspace_id, edit_id=edit_id)

    def create(
        self,
        *,
        workspace_id: UUID,
        candidate_id: UUID,
        created_by_user_id: UUID,
        composition: dict[str, Any],
        composition_hash: bytes,
        now: datetime,
    ) -> UUID:
        """Create one Edit together with the immutable Revision it starts from."""
        edit_id = uuid4()
        self._session.add(
            ClipEdit(
                id=edit_id,
                workspace_id=workspace_id,
                candidate_id=candidate_id,
                created_by_user_id=created_by_user_id,
                current_revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        self.append_revision(
            workspace_id=workspace_id,
            edit_id=edit_id,
            revision=1,
            composition=composition,
            composition_hash=composition_hash,
            created_by_user_id=created_by_user_id,
            now=now,
        )
        self._session.flush()
        return edit_id

    def lock(self, *, workspace_id: UUID, edit_id: UUID) -> EditLock | None:
        """Take the row lock that serializes every save of one Edit.

        The Edit row is locked on its own, and its Project and current Revision are read
        in a later statement. Reading them in the same statement would answer from the
        snapshot the lock was taken under, where a competing save's Revision does not yet
        exist, and a loser would look like a missing Edit rather than a conflict.
        """
        edit = self._session.scalar(
            select(ClipEdit)
            .where(ClipEdit.workspace_id == workspace_id, ClipEdit.id == edit_id)
            .with_for_update()
        )
        if edit is None:
            return None
        # A composite foreign key guarantees the candidate, and the current Revision was
        # written in the same transaction that pointed at it, so both rows must exist.
        project_id = self._session.scalars(
            select(ClipCandidate.project_id).where(
                ClipCandidate.workspace_id == workspace_id,
                ClipCandidate.id == edit.candidate_id,
            )
        ).one()
        digest = self._session.scalars(
            select(ClipEditRevision.composition_hash).where(
                ClipEditRevision.workspace_id == workspace_id,
                ClipEditRevision.clip_edit_id == edit_id,
                ClipEditRevision.revision == edit.current_revision,
            )
        ).one()
        return EditLock(
            edit_id=edit_id,
            project_id=project_id,
            current_revision=edit.current_revision,
            composition_hash=digest,
        )

    def append_revision(
        self,
        *,
        workspace_id: UUID,
        edit_id: UUID,
        revision: int,
        composition: dict[str, Any],
        composition_hash: bytes,
        created_by_user_id: UUID,
        now: datetime,
    ) -> None:
        """Append one immutable Revision; the API role holds no update or delete grant."""
        self._session.add(
            ClipEditRevision(
                id=uuid4(),
                workspace_id=workspace_id,
                clip_edit_id=edit_id,
                revision=revision,
                composition=composition,
                composition_hash=composition_hash,
                created_by_user_id=created_by_user_id,
                created_at=now,
            )
        )

    def advance_current_revision(
        self, *, workspace_id: UUID, edit_id: UUID, expected_revision: int, now: datetime
    ) -> bool:
        """Move the Edit's pointer forward only while it still names the expected Revision."""
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(ClipEdit)
                .where(
                    ClipEdit.workspace_id == workspace_id,
                    ClipEdit.id == edit_id,
                    ClipEdit.current_revision == expected_revision,
                )
                .values(current_revision=expected_revision + 1, updated_at=now)
            ),
        )
        return result.rowcount == 1

    def detail(self, *, workspace_id: UUID, edit_id: UUID) -> EditDetail | None:
        """Read one Edit together with the composition its current Revision holds."""
        row = self._session.execute(
            select(ClipEdit, ClipEditRevision, ClipCandidate.project_id)
            .join(
                ClipEditRevision,
                (ClipEditRevision.workspace_id == ClipEdit.workspace_id)
                & (ClipEditRevision.clip_edit_id == ClipEdit.id)
                & (ClipEditRevision.revision == ClipEdit.current_revision),
            )
            .join(
                ClipCandidate,
                (ClipCandidate.workspace_id == ClipEdit.workspace_id)
                & (ClipCandidate.id == ClipEdit.candidate_id),
            )
            .where(ClipEdit.workspace_id == workspace_id, ClipEdit.id == edit_id)
        ).first()
        if row is None:
            return None
        edit, revision, project_id = row
        return EditDetail(
            edit_id=edit.id,
            project_id=project_id,
            candidate_id=edit.candidate_id,
            current_revision=edit.current_revision,
            composition=dict(revision.composition),
            composition_hash=revision.composition_hash,
            created_at=edit.created_at,
            updated_at=edit.updated_at,
        )

    def revisions(
        self, *, workspace_id: UUID, edit_id: UUID, limit: int
    ) -> tuple[RevisionSummary, ...]:
        """Read the newest Revisions of one Edit, newest first."""
        rows = self._session.scalars(
            select(ClipEditRevision)
            .where(
                ClipEditRevision.workspace_id == workspace_id,
                ClipEditRevision.clip_edit_id == edit_id,
            )
            .order_by(ClipEditRevision.revision.desc())
            .limit(limit)
        )
        return tuple(
            RevisionSummary(
                revision=row.revision,
                composition_hash=row.composition_hash,
                created_by_user_id=row.created_by_user_id,
                created_at=row.created_at,
            )
            for row in rows
        )


def _seed_words(words: Any, start_ms: int, end_ms: int) -> tuple[SeedWord, ...]:
    """Read the persisted transcript words wholly inside the reviewed moment."""
    if not isinstance(words, list):
        return ()
    seeds: list[SeedWord] = []
    for word in words:
        try:
            word_start = int(word["start_ms"])
            word_end = int(word["end_ms"])
            if word_start < start_ms or word_end > end_ms:
                continue
            seeds.append(
                SeedWord(
                    word_id=str(word["word_id"]),
                    text=str(word["text"]),
                    start_ms=word_start,
                    end_ms=word_end,
                    speaker=str(word["speaker"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(seeds)
