"""Derive the searchable documents of one Project from its durable rows.

The index is a projection and nothing else. `index_project` reads the Project, its
Transcripts, its exposed Clip Candidates, and its Campaign Outputs, and makes the stored
documents equal to what those rows say — writing what changed, and deleting what no
longer has a source. Running it twice in a row therefore changes nothing the second time,
and it never writes to a table it read from.
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Select, delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from clipah.db import RuntimeRole, session_scope
from clipah.models import (
    CampaignOutput,
    ClipCandidate,
    ClipEdit,
    ClipEditRevision,
    Project,
    RenderArtifact,
    SearchDocument,
    Transcript,
    WorkspaceMembership,
)
from clipah.search.models import (
    MAX_BODY_CHARACTERS,
    ExportState,
    IndexedDocument,
    SearchEntityType,
    SearchLanguage,
)

# A transcript is indexed one speaker turn at a time, and a turn longer than this is cut
# into several documents: a result a member cannot read in a glance is not a result.
MAX_SEGMENT_WORDS = 120

# Sentinels mark a highlighted run in a search fragment, so the API can describe a match
# without ever sending markup. Stripping them here is what makes them unambiguous later.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


def index_project(session: Session, *, workspace_id: UUID, project_id: UUID) -> int:
    """Make the stored documents of one Project equal to what its durable rows say.

    A Project that has been soft-deleted, or that never existed in this Workspace, ends
    with no documents at all: work a member has thrown away must stop being findable.
    """
    project = session.scalar(
        select(Project).where(Project.workspace_id == workspace_id, Project.id == project_id)
    )
    documents: tuple[IndexedDocument, ...] = ()
    if project is not None and project.archived_at is None:
        documents = _build_documents(session, project=project)
    _replace_documents(
        session, workspace_id=workspace_id, project_id=project_id, documents=documents
    )
    return len(documents)


def rebuild_workspace(session: Session, *, workspace_id: UUID) -> int:
    """Rebuild every document of one Workspace from the rows they are derived from."""
    project_ids = session.scalars(
        select(Project.id).where(Project.workspace_id == workspace_id).order_by(Project.id)
    ).all()
    # A Project row that is gone entirely takes its documents with it: the derived table
    # references `projects` with ON DELETE CASCADE, so no orphan can survive this loop.
    return sum(
        index_project(session, workspace_id=workspace_id, project_id=project_id)
        for project_id in project_ids
    )


def _build_documents(session: Session, *, project: Project) -> tuple[IndexedDocument, ...]:
    """Derive every document one Project contributes to its Workspace's library."""
    exported_revisions = _exported_revision_ids(session, project=project)
    documents: list[IndexedDocument] = [_project_document(project)]
    documents.extend(_transcript_documents(session, project=project))
    documents.extend(
        _clip_documents(session, project=project, exported_revisions=exported_revisions)
    )
    documents.extend(
        _campaign_documents(session, project=project, exported_revisions=exported_revisions)
    )
    return tuple(documents)


def _project_document(project: Project) -> IndexedDocument:
    """Index the Project itself, so a member can find it by the name they gave it."""
    return IndexedDocument(
        workspace_id=project.workspace_id,
        project_id=project.id,
        entity_type=SearchEntityType.PROJECT,
        entity_id=project.id,
        anchor_id=project.id,
        segment_ordinal=0,
        title=_clean(project.name),
        body=_clean(project.name),
        speaker=None,
        topics=(),
        tags=(project.status.value, project.source_kind.value),
        language=SearchLanguage.OTHER,
        start_ms=None,
        end_ms=None,
        export_state=ExportState.NOT_EXPORTED,
        source_created_at=project.created_at,
    )


def _transcript_documents(session: Session, *, project: Project) -> Iterable[IndexedDocument]:
    """Index each speaker turn, so a spoken sentence is findable at its own timecode."""
    transcripts = session.scalars(
        _project_scoped(select(Transcript), Transcript, project).order_by(Transcript.id)
    ).all()
    for transcript in transcripts:
        language = SearchLanguage.parse(transcript.language)
        for ordinal, segment in enumerate(_segments(transcript)):
            yield IndexedDocument(
                workspace_id=project.workspace_id,
                project_id=project.id,
                entity_type=SearchEntityType.TRANSCRIPT,
                entity_id=transcript.id,
                anchor_id=transcript.id,
                segment_ordinal=ordinal,
                title=_headline(segment.text),
                body=_clean(segment.text),
                speaker=segment.speaker,
                topics=(),
                tags=(),
                language=language,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                export_state=ExportState.NOT_EXPORTED,
                source_created_at=transcript.created_at,
            )


def _clip_documents(
    session: Session, *, project: Project, exported_revisions: frozenset[UUID]
) -> Iterable[IndexedDocument]:
    """Index the ranked moments a member reviewed, hidden candidates excluded."""
    rows = session.execute(
        _project_scoped(
            select(ClipCandidate, Transcript.language, ClipEdit.id), ClipCandidate, project
        )
        .join(
            Transcript,
            (Transcript.workspace_id == ClipCandidate.workspace_id)
            & (Transcript.id == ClipCandidate.transcript_id),
        )
        .join(
            ClipEdit,
            (ClipEdit.workspace_id == ClipCandidate.workspace_id)
            & (ClipEdit.candidate_id == ClipCandidate.id),
            isouter=True,
        )
        .where(ClipCandidate.model_metadata["exposed"].as_boolean().is_(True))
        .order_by(ClipCandidate.rank)
    ).all()
    for candidate, language, edit_id in rows:
        yield IndexedDocument(
            workspace_id=project.workspace_id,
            project_id=project.id,
            entity_type=SearchEntityType.CLIP,
            entity_id=candidate.id,
            anchor_id=candidate.id,
            segment_ordinal=0,
            title=_clean(candidate.hook),
            body=_clean(
                " ".join(
                    (
                        candidate.hook,
                        candidate.payoff,
                        candidate.reason,
                        candidate.transcript_excerpt,
                    )
                )
            ),
            speaker=None,
            topics=tuple(candidate.tags),
            tags=(candidate.category,),
            language=SearchLanguage.parse(language),
            start_ms=candidate.start_ms,
            end_ms=candidate.end_ms,
            export_state=_edit_export_state(
                session,
                project=project,
                edit_id=edit_id,
                exported_revisions=exported_revisions,
            ),
            source_created_at=candidate.created_at,
        )


def _campaign_documents(
    session: Session, *, project: Project, exported_revisions: frozenset[UUID]
) -> Iterable[IndexedDocument]:
    """Index the copy derived from a cut, anchored to the clip it was written for."""
    rows = session.execute(
        _project_scoped(select(CampaignOutput, ClipEdit.candidate_id), CampaignOutput, project)
        .join(
            ClipEditRevision,
            (ClipEditRevision.workspace_id == CampaignOutput.workspace_id)
            & (ClipEditRevision.id == CampaignOutput.clip_edit_revision_id),
        )
        .join(
            ClipEdit,
            (ClipEdit.workspace_id == ClipEditRevision.workspace_id)
            & (ClipEdit.id == ClipEditRevision.clip_edit_id),
        )
        .order_by(CampaignOutput.id)
    ).all()
    for output, candidate_id in rows:
        hashtags = tuple(str(hashtag) for hashtag in output.hashtags)
        yield IndexedDocument(
            workspace_id=project.workspace_id,
            project_id=project.id,
            entity_type=SearchEntityType.CAMPAIGN_OUTPUT,
            entity_id=output.id,
            anchor_id=candidate_id,
            segment_ordinal=0,
            title=_clean(output.title),
            body=_clean(" ".join((output.title, output.post_copy, output.cta, *hashtags))),
            speaker=None,
            topics=hashtags,
            tags=(output.platform.value, output.language.value),
            language=SearchLanguage.parse(output.language.value),
            start_ms=None,
            end_ms=None,
            export_state=(
                ExportState.EXPORTED
                if output.clip_edit_revision_id in exported_revisions
                else ExportState.NOT_EXPORTED
            ),
            source_created_at=output.created_at,
        )


def _edit_export_state(
    session: Session,
    *,
    project: Project,
    edit_id: UUID | None,
    exported_revisions: frozenset[UUID],
) -> ExportState:
    """Report whether any Revision of one clip's Edit has ever been rendered."""
    if edit_id is None or not exported_revisions:
        return ExportState.NOT_EXPORTED
    revision_ids = session.scalars(
        select(ClipEditRevision.id).where(
            ClipEditRevision.workspace_id == project.workspace_id,
            ClipEditRevision.clip_edit_id == edit_id,
        )
    ).all()
    return (
        ExportState.EXPORTED
        if exported_revisions.intersection(revision_ids)
        else ExportState.NOT_EXPORTED
    )


def _exported_revision_ids(session: Session, *, project: Project) -> frozenset[UUID]:
    """Name every Edit Revision of this Project that has produced a rendered file."""
    revision_ids = session.scalars(
        select(RenderArtifact.clip_edit_revision_id)
        .join(
            ClipEditRevision,
            (ClipEditRevision.workspace_id == RenderArtifact.workspace_id)
            & (ClipEditRevision.id == RenderArtifact.clip_edit_revision_id),
        )
        .join(
            ClipEdit,
            (ClipEdit.workspace_id == ClipEditRevision.workspace_id)
            & (ClipEdit.id == ClipEditRevision.clip_edit_id),
        )
        .join(
            ClipCandidate,
            (ClipCandidate.workspace_id == ClipEdit.workspace_id)
            & (ClipCandidate.id == ClipEdit.candidate_id),
        )
        .where(
            RenderArtifact.workspace_id == project.workspace_id,
            ClipCandidate.project_id == project.id,
        )
    ).all()
    return frozenset(revision_ids)


def _project_scoped(statement: Select[Any], entity: Any, project: Project) -> Select[Any]:
    """Bind one read to the single Workspace and Project being indexed."""
    return statement.where(
        entity.workspace_id == project.workspace_id, entity.project_id == project.id
    )


class _Segment:
    """One contiguous run of transcript words attributed to a single speaker."""

    __slots__ = ("end_ms", "speaker", "start_ms", "text")

    def __init__(self, *, text: str, speaker: str | None, start_ms: int, end_ms: int) -> None:
        """Record the words of one turn together with the range they were spoken in."""
        self.text = text
        self.speaker = speaker
        self.start_ms = start_ms
        self.end_ms = end_ms


def _segments(transcript: Transcript) -> list[_Segment]:
    """Cut one Transcript into the readable turns the library indexes.

    Speaker segments are the authoritative division when the provider found them. When it
    found none, the words are cut into fixed runs instead, because a transcript nobody can
    open at a timecode is a transcript nobody can use.
    """
    words = [word for word in transcript.words if isinstance(word, dict)]
    if not words:
        return []
    by_id = {str(word.get("word_id")): index for index, word in enumerate(words)}
    runs: list[list[dict[str, Any]]] = []
    for segment in transcript.speaker_segments:
        if not isinstance(segment, dict):
            continue
        start = by_id.get(str(segment.get("start_word_id")))
        end = by_id.get(str(segment.get("end_word_id")))
        if start is None or end is None or end < start:
            continue
        runs.append(words[start : end + 1])
    if not runs:
        runs = [words]
    segments: list[_Segment] = []
    for run in runs:
        for offset in range(0, len(run), MAX_SEGMENT_WORDS):
            chunk = run[offset : offset + MAX_SEGMENT_WORDS]
            segments.append(
                _Segment(
                    text=" ".join(str(word.get("text", "")) for word in chunk),
                    speaker=_speaker_of(chunk),
                    start_ms=int(chunk[0].get("start_ms", 0)),
                    end_ms=int(chunk[-1].get("end_ms", 0)),
                )
            )
    return [segment for segment in segments if segment.end_ms > segment.start_ms]


def _speaker_of(words: Sequence[dict[str, Any]]) -> str | None:
    """Name the one speaker of a run, or nobody when the run is not one person's."""
    speakers = {str(word.get("speaker")) for word in words if word.get("speaker")}
    return speakers.pop() if len(speakers) == 1 else None


def _replace_documents(
    session: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    documents: Sequence[IndexedDocument],
) -> None:
    """Write what the rows now say and delete every document that lost its source."""
    keep = [document.document_id for document in documents]
    stale = delete(SearchDocument).where(
        SearchDocument.workspace_id == workspace_id, SearchDocument.project_id == project_id
    )
    if keep:
        stale = stale.where(SearchDocument.id.not_in(keep))
    session.execute(stale)
    for document in documents:
        session.execute(_upsert(document))


def _upsert(document: IndexedDocument) -> Any:
    """Write one document, replacing whatever the previous derivation of it said."""
    body = document.body[:MAX_BODY_CHARACTERS]
    keywords = " ".join((*document.topics, *document.tags, document.speaker or ""))
    values: dict[str, Any] = {
        "id": document.document_id,
        "workspace_id": document.workspace_id,
        "project_id": document.project_id,
        "entity_type": document.entity_type.value,
        "entity_id": document.entity_id,
        "anchor_id": document.anchor_id,
        "segment_ordinal": document.segment_ordinal,
        "title": document.title,
        "title_normalized": normalize_text(document.title),
        "body": body,
        "speaker": document.speaker,
        "topics": list(document.topics),
        "tags": list(document.tags),
        "language": document.language.value,
        "start_ms": document.start_ms,
        "end_ms": document.end_ms,
        "export_state": document.export_state.value,
        "source_created_at": document.source_created_at,
        "indexed_at": func.now(),
        "search_vector": _vector(
            config=document.language.text_search_config,
            title=document.title,
            keywords=keywords,
            body=body,
        ),
    }
    statement = insert(SearchDocument).values(**values)
    return statement.on_conflict_do_update(
        index_elements=[SearchDocument.id],
        set_={name: values[name] for name in values if name != "id"},
    )


def _vector(*, config: str, title: str, keywords: str, body: str) -> ColumnElement[Any]:
    """Weight a document's own words: its name first, its labels next, its text last.

    The stemmed half is what makes "daftar" find "pendaftaran". The unstemmed half beside
    it is what makes a word typed in the other language still find the document, and what
    gives a language this product does not stem an honest literal index.
    """
    stemmed: ColumnElement[Any] = literal_column(f"'{config}'::regconfig")
    simple: ColumnElement[Any] = literal_column("'simple'::regconfig")
    parts = (
        func.setweight(func.to_tsvector(stemmed, func.unaccent(title)), _weight("A")),
        func.setweight(func.to_tsvector(stemmed, func.unaccent(keywords)), _weight("B")),
        func.setweight(func.to_tsvector(stemmed, func.unaccent(body)), _weight("C")),
        func.setweight(
            func.to_tsvector(simple, func.unaccent(" ".join((title, keywords, body)))),
            _weight("D"),
        ),
    )
    combined: ColumnElement[Any] = parts[0]
    for part in parts[1:]:
        combined = combined.op("||")(part)
    return combined


def _weight(label: str) -> ColumnElement[Any]:
    """Name one of the four tsvector weights, which Postgres types as `"char"`."""
    return literal_column(f"'{label}'")


def _clean(value: str) -> str:
    """Strip the control characters a fragment sentinel would otherwise be confused with."""
    return _WHITESPACE.sub(" ", _CONTROL_CHARACTERS.sub(" ", value)).strip()


def _headline(text: str) -> str:
    """Name one transcript turn by its own opening words rather than by an identifier."""
    cleaned = _clean(text)
    if len(cleaned) <= 80:
        return cleaned
    return cleaned[:79].rsplit(" ", 1)[0] + "…"


def normalize_text(value: str) -> str:
    """Fold a name to the form a person searching for it months later will type."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    stripped = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return _WHITESPACE.sub(" ", re.sub(r"[^\w\s]", " ", stripped)).strip()


def main(argv: Sequence[str] | None = None) -> int:
    """Rebuild the library of one Workspace on behalf of one of its members.

    An operator names the member the rebuild runs as, and the command proves that member
    really does stand in that Workspace before it declares the tenant context. Row-level
    security requires an actor, and a maintenance task inventing one would be exactly the
    hole the tenancy rules exist to close.
    """
    parser = argparse.ArgumentParser(
        prog="python -m clipah.search.indexer",
        description="Rebuild derived content-library search documents for one Workspace.",
    )
    parser.add_argument("--workspace", type=UUID, required=True, help="The Workspace to rebuild.")
    parser.add_argument(
        "--actor", type=UUID, required=True, help="A member of that Workspace to run as."
    )
    arguments = parser.parse_args(argv)
    with session_scope(
        workspace_id=arguments.workspace,
        user_id=arguments.actor,
        runtime_role=RuntimeRole.API,
    ) as session:
        membership = session.scalar(
            select(WorkspaceMembership.role).where(
                WorkspaceMembership.workspace_id == arguments.workspace,
                WorkspaceMembership.user_id == arguments.actor,
            )
        )
        if membership is None:
            parser.error("the actor is not a member of that workspace")
        written = rebuild_workspace(session, workspace_id=arguments.workspace)
    print(f"indexed {written} documents in workspace {arguments.workspace}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a command, not as an import
    raise SystemExit(main())
