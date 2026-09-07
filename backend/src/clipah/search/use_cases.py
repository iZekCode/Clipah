"""Answer one member's question about their own Workspace's library.

Two rules shape everything here. A result is evidence, not a record: it carries a
readable fragment, a deep link, and a timecode, and it never carries a storage key or a
provider payload. And a result is reachable only through a Workspace the caller has
already proved standing in — the tenant predicate is defence in depth, and this module
still scopes every read to the Workspace it was handed.

Ranking and reading are two statements on purpose. The first one ranks with nothing but
indexed columns; the second builds highlighted fragments for the handful of documents
that actually reached the page, because extracting a fragment from a document nobody will
see is the one thing that would make a large library slow.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Double,
    Select,
    and_,
    case,
    cast,
    func,
    literal,
    literal_column,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Session

from clipah.models import Project, SearchDocument
from clipah.search.indexer import normalize_text
from clipah.search.models import (
    HitBoundary,
    SearchEntityType,
    SearchHit,
    SearchLanguage,
    SearchPage,
    SearchQuery,
    TextFragment,
)
from clipah.workspaces.models import WorkspaceAccess

MAX_QUERY_CHARACTERS = 200
MAX_PAGE_SIZE = 50

# A highlighted run is delimited rather than wrapped in markup, so provider text stays
# text all the way to the browser. The indexer strips these characters from every
# document it writes, which is what makes them unambiguous here.
_HIGHLIGHT_START = "\x02"
_HIGHLIGHT_STOP = "\x03"
_HEADLINE_OPTIONS = (
    f'StartSel="{_HIGHLIGHT_START}", StopSel="{_HIGHLIGHT_STOP}", '
    'MaxFragments=2, MaxWords=22, MinWords=6, FragmentDelimiter=" … "'
)

# A name typed from memory is close, not exact. `word_similarity` asks whether the query
# resembles some run of words inside a title, which is the question a member is asking.
# Postgres reads its own threshold for the `<%` operator from `pg_trgm`, whose default of
# 0.6 is the value this search is tuned against.
TYPO_THRESHOLD = 0.6


class SearchQueryError(ValueError):
    """The question itself cannot be answered as asked."""


def search_content(session: Session, *, access: WorkspaceAccess, query: SearchQuery) -> SearchPage:
    """Rank one Workspace's derived documents against one member's question."""
    text = query.text.strip()
    if not text or len(text) > MAX_QUERY_CHARACTERS:
        raise SearchQueryError("a search needs between one and 200 characters")
    if not 1 <= query.limit <= MAX_PAGE_SIZE:
        raise SearchQueryError("a search page holds between one and fifty results")

    tsquery = _tsquery(text)
    normalized = normalize_text(text)
    # A quoted phrase is a promise about word order, so resemblance is set aside for it.
    resemblance = None if '"' in text else normalized
    ranked = session.execute(_ranking(access, query, tsquery, resemblance)).all()
    page = ranked[: query.limit]
    if not page:
        return SearchPage()

    scores = {document_id: float(score) for document_id, score in page}
    hits = tuple(
        _hit(row, scores) for row in session.execute(_reading(access, tsquery, list(scores))).all()
    )
    hits = tuple(sorted(hits, key=lambda hit: (-hit.score, str(hit.document_id))))
    boundary = (
        HitBoundary(score=hits[-1].score, document_id=hits[-1].document_id)
        if len(ranked) > query.limit and hits
        else None
    )
    return SearchPage(hits=hits, next_boundary=boundary)


def _ranking(
    access: WorkspaceAccess,
    query: SearchQuery,
    tsquery: ColumnElement[Any],
    resemblance: str | None,
) -> Select[Any]:
    """Choose which documents reach the page, in the order they reach it.

    The two ways a document can answer a question are asked as two branches rather than
    as one predicate with an `OR` in it. Each branch is answered by its own index — the
    text index for the words, the trigram index for the half-remembered name — so neither
    of them ever computes a similarity for a document the other one found.
    """
    branches = [_matched_words(access, query, tsquery)]
    if resemblance is not None:
        branches.append(_resembled_name(access, query, tsquery, resemblance))
    matches = union_all(*branches).subquery("matches")
    return (
        select(matches.c.id, matches.c.score)
        .order_by(matches.c.score.desc(), matches.c.id.asc())
        .limit(query.limit + 1)
    )


def _matched_words(
    access: WorkspaceAccess, query: SearchQuery, tsquery: ColumnElement[Any]
) -> Select[Any]:
    """Find the documents whose own words answer the question, ranked by how well.

    A word match always outranks a resembled name, so its score is lifted clear of the
    similarity scale rather than competing inside it.

    Relevance is `ts_rank`, not `ts_rank_cd`. Cover density measures how close a query's
    words sit to each other, which is worth paying for in long documents — and every
    document here is one short segment, one clip, or one name, where it costs an order of
    magnitude more and says almost nothing the weights have not already said.
    """
    relevance = cast(func.ts_rank(SearchDocument.search_vector, tsquery) + 1.0, Double)
    return _resumed(
        _visible(access, query, select(SearchDocument.id, relevance.label("score"))).where(
            SearchDocument.search_vector.op("@@")(tsquery)
        ),
        query,
        relevance,
    )


def _resembled_name(
    access: WorkspaceAccess, query: SearchQuery, tsquery: ColumnElement[Any], resemblance: str
) -> Select[Any]:
    """Find the documents whose name resembles what the member typed, and how closely.

    A document the words already found is excluded here rather than scored twice. That is
    what lets the two branches merge without an aggregate: each document reaches the page
    from exactly one of them, carrying exactly one score.
    """
    similarity = cast(func.word_similarity(resemblance, SearchDocument.title_normalized), Double)
    return _resumed(
        _visible(access, query, select(SearchDocument.id, similarity.label("score"))).where(
            _title_resembles(resemblance), ~SearchDocument.search_vector.op("@@")(tsquery)
        ),
        query,
        similarity,
    )


def _resumed(statement: Select[Any], query: SearchQuery, score: ColumnElement[Any]) -> Select[Any]:
    """Order one branch and resume it where the previous page stopped.

    Each branch carries the cursor and the page bound itself, so the union merges two
    short lists rather than every document that matched either way.
    """
    statement = statement.order_by(score.desc(), SearchDocument.id.asc())
    if query.after is None:
        return statement
    return statement.where(
        or_(
            score < query.after.score,
            and_(score == query.after.score, SearchDocument.id > query.after.document_id),
        )
    )


def _visible(access: WorkspaceAccess, query: SearchQuery, statement: Select[Any]) -> Select[Any]:
    """Bind one branch to this Workspace and to the narrowings the member asked for.

    A branch deliberately does not join `projects`. Archiving a Project deletes its
    documents in the same request, so the join would only ever re-prove something already
    true — and its presence is enough to make the planner walk the tenant index instead of
    the text index. The join still happens when the page is read, so a document that
    somehow outlived its Project is dropped there rather than shown.
    """
    return _narrow(
        statement.where(SearchDocument.workspace_id == access.workspace_id), query
    ).limit(query.limit + 1)


def _reading(
    access: WorkspaceAccess, tsquery: ColumnElement[Any], document_ids: list[UUID]
) -> Select[Any]:
    """Read the evidence of one page: the document, its Project, and its fragments."""
    return (
        select(SearchDocument, Project.name, _headline(tsquery).label("fragments"))
        .join(
            Project,
            and_(
                Project.workspace_id == SearchDocument.workspace_id,
                Project.id == SearchDocument.project_id,
            ),
        )
        .where(
            SearchDocument.workspace_id == access.workspace_id,
            SearchDocument.id.in_(document_ids),
        )
    )


def _narrow(statement: Select[Any], query: SearchQuery) -> Select[Any]:
    """Apply every narrowing the member asked for, and none they did not."""
    if query.entity_types:
        statement = statement.where(SearchDocument.entity_type.in_(query.entity_types))
    if query.project_id is not None:
        statement = statement.where(SearchDocument.project_id == query.project_id)
    if query.speaker is not None:
        statement = statement.where(SearchDocument.speaker == query.speaker)
    if query.topic is not None:
        statement = statement.where(SearchDocument.topics.any(literal(query.topic)))
    if query.language is not None:
        statement = statement.where(SearchDocument.language == query.language)
    if query.export_state is not None:
        statement = statement.where(SearchDocument.export_state == query.export_state)
    if query.created_after is not None:
        statement = statement.where(SearchDocument.source_created_at >= query.created_after)
    if query.created_before is not None:
        statement = statement.where(SearchDocument.source_created_at <= query.created_before)
    return statement


def _tsquery(text: str) -> ColumnElement[Any]:
    """Read one question in every language this library is written in.

    A member types one string; the documents beneath it are Indonesian, English, and
    whatever a provider could not stem. Asking all three interpretations and accepting any
    of them is what stops a language tag from deciding whether a search works at all.
    """
    parsed = [
        func.websearch_to_tsquery(_config(language), func.unaccent(text))
        for language in SearchLanguage
    ]
    combined: ColumnElement[Any] = parsed[0]
    for part in parsed[1:]:
        combined = combined.op("||")(part)
    return combined


def _config(language: SearchLanguage) -> ColumnElement[Any]:
    """Name one Postgres text-search configuration from the closed language set."""
    return literal_column(f"'{language.text_search_config}'::regconfig")


def _title_resembles(resemblance: str) -> ColumnElement[Any]:
    """Match a name the member half-remembered, one transposed letter and all.

    Trigram resemblance ignores word order, so it would answer a quoted phrase with the
    same words in the wrong order. A member who used quotes asked about order, which is
    why this branch is withdrawn for them rather than merely outranked.
    """
    # The `<%` operator, unlike the same comparison written as a function call, is what
    # the trigram index answers: a hundred thousand documents are not scanned to find a
    # name that resembles five characters.
    return literal(resemblance).op("<%", is_comparison=True)(SearchDocument.title_normalized)


def _headline(tsquery: ColumnElement[Any]) -> ColumnElement[Any]:
    """Ask Postgres for the part of the document that actually answered the question."""
    configuration = case(
        (
            SearchDocument.language == SearchLanguage.INDONESIAN,
            _config(SearchLanguage.INDONESIAN),
        ),
        (SearchDocument.language == SearchLanguage.ENGLISH, _config(SearchLanguage.ENGLISH)),
        else_=_config(SearchLanguage.OTHER),
    )
    return func.ts_headline(configuration, SearchDocument.body, tsquery, _HEADLINE_OPTIONS)


def _hit(row: Any, scores: dict[UUID, float]) -> SearchHit:
    """Render one stored document as the evidence a member is allowed to read."""
    document, project_name, fragments = row
    return SearchHit(
        document_id=document.id,
        entity_type=document.entity_type,
        entity_id=document.entity_id,
        project_id=document.project_id,
        project_name=project_name,
        title=document.title,
        fragments=_fragments(fragments or document.title),
        speaker=document.speaker,
        topics=tuple(document.topics),
        tags=tuple(document.tags),
        language=document.language,
        start_ms=document.start_ms,
        end_ms=document.end_ms,
        export_state=document.export_state,
        created_at=document.source_created_at,
        score=scores[document.id],
        deep_link=_deep_link(document),
    )


def _fragments(headline: str) -> tuple[TextFragment, ...]:
    """Split a delimited headline into runs, saying which of them matched."""
    fragments: list[TextFragment] = []
    for block in headline.split(_HIGHLIGHT_START):
        matched, delimiter, trailing = block.partition(_HIGHLIGHT_STOP)
        if delimiter:
            _append(fragments, matched, highlighted=True)
            _append(fragments, trailing, highlighted=False)
        else:
            _append(fragments, matched, highlighted=False)
    return tuple(fragments)


def _append(fragments: list[TextFragment], text: str, *, highlighted: bool) -> None:
    """Keep only the runs that carry text a person would actually read."""
    if text:
        fragments.append(TextFragment(text=text, highlighted=highlighted))


def _deep_link(document: SearchDocument) -> str:
    """Name the screen that lets a member finish the thought this result started."""
    if document.entity_type is SearchEntityType.PROJECT:
        return f"/dashboard/projects/{document.project_id}"
    if document.entity_type is SearchEntityType.TRANSCRIPT:
        return f"/dashboard/projects/{document.project_id}?t={document.start_ms}"
    if document.entity_type is SearchEntityType.CLIP:
        return f"/dashboard/clips/{document.anchor_id}"
    return f"/dashboard/clips/{document.anchor_id}?output={document.entity_id}"
