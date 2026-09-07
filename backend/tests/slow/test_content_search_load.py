"""Opted-in load test: a hundred thousand documents must still answer in under half a second.

The fixture is generated rather than checked in as rows, the way this repository already
generates its media fixtures: the generator is deterministic, so the corpus one operator
measures is the corpus another operator measures. Set `CLIPAH_SEARCH_LOAD_TEST=1` to run
it — building the corpus takes far longer than the per-test timeout of the normal suite.
"""

from __future__ import annotations

import os
import time
from statistics import quantiles
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from clipah.db import RuntimeRole, session_scope
from clipah.models import Project, WorkspaceMembership
from clipah.search.models import SearchQuery
from clipah.search.use_cases import search_content
from clipah.workspaces.models import WorkspaceAccess
from support import provision_identity, runtime_settings

DOCUMENT_COUNT = 100_000
PROJECT_COUNT = 50
P95_BUDGET_SECONDS = 0.5
OBSERVATIONS = 40

# Ten Indonesian and English subjects, mixed into every document by its ordinal, so a
# query matches a predictable slice of the corpus rather than all of it or none of it.
SUBJECTS = (
    "formulir pendaftaran",
    "aktivasi pengguna",
    "retensi pelanggan",
    "harga berlangganan",
    "dukungan teknis",
    "onboarding funnel",
    "pricing experiment",
    "customer retention",
    "support backlog",
    "activation rate",
)
QUERIES = ("formulir", "aktivasi pengguna", "retention", '"pricing experiment"', "aktivsi")


@pytest.mark.slow
@pytest.mark.timeout(1_800)
@pytest.mark.skipif(
    os.environ.get("CLIPAH_SEARCH_LOAD_TEST") != "1",
    reason="the hundred-thousand-document search load test is explicitly opt-in",
)
def test_a_hundred_thousand_documents_answer_within_the_latency_budget(
    engine: Engine, clean_database: None
) -> None:
    """A library nobody can wait for is a library nobody searches twice."""
    del clean_database
    workspace_id, user_id = _workspace(engine)
    _generate_corpus(engine, workspace_id=workspace_id)

    latencies = _measure(workspace_id=workspace_id, user_id=user_id)

    p95 = quantiles(latencies, n=20)[18]
    assert p95 < P95_BUDGET_SECONDS, f"p95 was {p95:.3f}s over {len(latencies)} observations"


def _workspace(engine: Engine) -> tuple[UUID, UUID]:
    """Provision one Workspace and the member whose library is being measured."""
    user_id, workspace_id = provision_identity(engine, suffix="searchload")
    return workspace_id, user_id


def _generate_corpus(engine: Engine, *, workspace_id: UUID) -> None:
    """Write the deterministic hundred-thousand-document corpus in one server-side pass."""
    project_ids = [uuid4() for _ in range(PROJECT_COUNT)]
    with engine.begin() as connection:
        for ordinal, project_id in enumerate(project_ids):
            connection.execute(
                Project.__table__.insert().values(
                    id=project_id,
                    workspace_id=workspace_id,
                    created_by_user_id=connection.execute(
                        text("SELECT id FROM users LIMIT 1")
                    ).scalar_one(),
                    name=f"Sesi {ordinal:03d} {SUBJECTS[ordinal % len(SUBJECTS)]}",
                    source_kind="upload",
                )
            )
        connection.execute(
            text(
                """
                INSERT INTO search_documents (
                    id, workspace_id, project_id, entity_type, entity_id, anchor_id,
                    segment_ordinal, title, title_normalized, body, speaker, topics, tags,
                    language, start_ms, end_ms, export_state, source_created_at, indexed_at,
                    search_vector
                )
                SELECT
                    gen_random_uuid(),
                    project.workspace_id,
                    project.id,
                    'transcript',
                    project.id,
                    project.id,
                    ordinal.value,
                    heading.text,
                    lower(unaccent(heading.text)),
                    body.text,
                    'SPEAKER_0' || mod(ordinal.value, 3),
                    '{}',
                    '{}',
                    configuration.language::search_language,
                    ordinal.value * 1000,
                    (ordinal.value + 1) * 1000,
                    'not_exported',
                    now() - (ordinal.value || ' minutes')::interval,
                    now(),
                    setweight(
                        to_tsvector(configuration.text_search, unaccent(heading.text)), 'A'
                    ) || setweight(
                        to_tsvector(configuration.text_search, unaccent(body.text)), 'C'
                    ) || setweight(to_tsvector('simple', unaccent(body.text)), 'D')
                FROM generate_series(0, :per_project - 1) AS ordinal(value)
                CROSS JOIN LATERAL (
                    SELECT id, workspace_id, name FROM projects WHERE workspace_id = :workspace
                ) AS project
                CROSS JOIN LATERAL (
                    SELECT
                        CASE WHEN mod(ordinal.value, 2) = 0 THEN 'id' ELSE 'en' END AS language,
                        (
                            CASE
                                WHEN mod(ordinal.value, 2) = 0 THEN 'indonesian'
                                ELSE 'english'
                            END
                        )::regconfig AS text_search
                ) AS configuration
                CROSS JOIN LATERAL (
                    SELECT 'Bagian ' || ordinal.value || ' dari ' || project.name AS text
                ) AS heading
                CROSS JOIN LATERAL (
                    SELECT
                        'Bagian ' || ordinal.value || ' dari sesi ini'
                        || ' membahas ' || (cast(:subjects AS text[]))[
                            1 + mod(ordinal.value + length(project.name), :subject_count)
                        ]
                        || ' dan keputusan yang diambil setelahnya oleh tim produk.'
                        AS text
                ) AS body
                """
            ),
            {
                "workspace": workspace_id,
                "per_project": DOCUMENT_COUNT // PROJECT_COUNT,
                "subjects": list(SUBJECTS),
                "subject_count": len(SUBJECTS),
            },
        )
        connection.execute(text("ANALYZE search_documents"))
        total = connection.execute(text("SELECT count(*) FROM search_documents")).scalar_one()
    assert total == DOCUMENT_COUNT, total


def _measure(*, workspace_id: UUID, user_id: UUID) -> list[float]:
    """Time one search per observation against the same connection a request would use."""
    settings = runtime_settings()
    latencies: list[float] = []
    with session_scope(
        settings=settings,
        workspace_id=workspace_id,
        user_id=user_id,
        runtime_role=RuntimeRole.API,
    ) as session:
        access = WorkspaceAccess(
            workspace_id=workspace_id,
            user_id=user_id,
            role=session.query(WorkspaceMembership.role)
            .filter(WorkspaceMembership.workspace_id == workspace_id)
            .scalar(),
            publishing_role_policy=_policy(session, workspace_id),
        )
        for observation in range(OBSERVATIONS):
            query = SearchQuery(text=QUERIES[observation % len(QUERIES)], limit=20)
            started = time.perf_counter()
            search_content(session, access=access, query=query)
            latencies.append(time.perf_counter() - started)
    assert len(latencies) == OBSERVATIONS
    return latencies


def _policy(session: Session, workspace_id: UUID) -> object:
    """Read the Workspace's publishing policy, which the access value requires."""
    return session.execute(
        text("SELECT publishing_role_policy FROM workspaces WHERE id = :id"),
        {"id": workspace_id},
    ).scalar_one()
