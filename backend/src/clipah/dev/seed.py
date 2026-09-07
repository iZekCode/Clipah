"""Mint one signed-in browser Session for local end-to-end tests.

Browser tests need a Session a real browser can carry, and the only other way to obtain
one is a live Google login. This helper skips authentication entirely, so it refuses to
run against a production deployment and is never imported by the application itself.
"""

from __future__ import annotations

import argparse
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from clipah.auth.models import SessionPolicy
from clipah.auth.sessions import issue_session
from clipah.config import Environment, ProcessRole, Settings
from clipah.db import (
    RuntimeRole,
    create_user_with_personal_workspace,
    session_scope,
    set_actor_context,
)
from clipah.models import (
    Asset,
    AssetKind,
    AssetSourceType,
    ClipCandidate,
    Project,
    ProjectStatus,
    SourceKind,
    Transcript,
    WorkspaceKind,
)
from clipah.workspaces.use_cases import create_workspace, workspace_slug

CSRF_TOKEN_BYTES = 32
SOURCE_DURATION_MS = 120_000
CLIP_START_MS = 5_000
CLIP_END_MS = 35_000
# One word every three seconds across the clip, so a trim, a split, and a caption edit all
# have something real to move. The timings are the source's own, as a transcript records.
SEEDED_WORDS: list[dict[str, object]] = [
    {
        "word_id": f"w{index:06d}",
        "text": text,
        "punctuation": "",
        "start_ms": CLIP_START_MS + index * 3_000,
        "end_ms": CLIP_START_MS + index * 3_000 + 900,
        "confidence": 0.99,
        "speaker": "SPEAKER_00",
    }
    for index, text in enumerate(
        ["This", "is", "the", "moment", "the", "analysis", "chose", "to", "keep", "today"]
    )
]


class SeedRefusedError(RuntimeError):
    """Raised when seeding is attempted somewhere real accounts could be affected."""


@dataclass(frozen=True, slots=True)
class SeededBrowserSession:
    """Everything a browser needs to arrive already signed in."""

    user_id: UUID
    workspace_id: UUID
    session_cookie_name: str
    session_token: str
    csrf_cookie_name: str
    csrf_token: str


def seed_browser_session(
    session: Session,
    *,
    settings: Settings,
    email: str,
    display_name: str,
    workspace_name: str,
    now: datetime,
    team_workspace: bool = False,
) -> SeededBrowserSession:
    """Create one User with their personal Workspace and a Session for that User."""
    if settings.environment is Environment.PRODUCTION:
        raise SeedRefusedError("browser-test seeding is refused outside development")

    provisioned = create_user_with_personal_workspace(
        session,
        primary_email=email,
        display_name=display_name,
        workspace_name=workspace_name,
        workspace_slug=f"{workspace_slug(workspace_name)}-{uuid4().hex[:8]}",
    )
    workspace_id = provisioned.workspace.id
    if team_workspace:
        workspace_id = create_workspace(
            session,
            owner_user_id=provisioned.user.id,
            name=workspace_name,
            kind=WorkspaceKind.TEAM,
            now=now,
        ).workspace_id
    set_actor_context(session, user_id=provisioned.user.id)
    issued = issue_session(
        session,
        user_id=provisioned.user.id,
        secret=_session_secret(settings),
        policy=_policy(settings),
        now=now,
    )
    return SeededBrowserSession(
        user_id=provisioned.user.id,
        workspace_id=workspace_id,
        session_cookie_name=settings.session_cookie_name,
        session_token=issued.token,
        csrf_cookie_name=settings.csrf_cookie_name,
        csrf_token=_csrf_token(),
    )


@dataclass(frozen=True, slots=True)
class SeededProject:
    """One analysed Project, and the clip a browser test opens the editor on."""

    project_id: UUID
    candidate_id: UUID
    source_asset_id: UUID
    name: str


def seed_analysed_project(
    *,
    settings: Settings,
    workspace_id: UUID,
    user_id: UUID,
    name: str,
    now: datetime,
) -> SeededProject:
    """Stage a Project as though ingest, transcription, and analysis had all finished.

    No API can create a Clip Candidate — it is the analysis worker's output — so the
    browser scenarios that need a real clip cannot reach one through the product's own
    surface. This writes the rows that pipeline would have written, and nothing else: the
    media it names is never fetched, because every scenario it unblocks is about the
    document rather than the picture.

    The rows are written as the two roles that would really have written them — the API
    owns Projects and Assets, the worker owns Transcripts and Clip Candidates — because a
    seed needing privileges no runtime role holds would be staging a state the pipeline
    itself could never reach.
    """
    if settings.environment is Environment.PRODUCTION:
        raise SeedRefusedError("browser-test seeding is refused outside development")

    project_id = uuid4()
    source_asset_id = uuid4()
    transcript_id = uuid4()
    candidate_id = uuid4()
    prefix = f"workspaces/{workspace_id}/projects/{project_id}"
    tenant = {"workspace_id": workspace_id, "user_id": user_id}

    with session_scope(settings=settings, runtime_role=RuntimeRole.API, **tenant) as session:
        session.add(
            Project(
                id=project_id,
                workspace_id=workspace_id,
                name=name,
                status=ProjectStatus.READY,
                source_kind=SourceKind.UPLOAD,
                created_by_user_id=user_id,
                created_at=now,
                updated_at=now,
            )
        )
        # The Assets point at the Project through a composite foreign key, so the insert
        # order is the schema's rather than the unit of work's.
        session.flush()
        session.add_all(
            [
                _asset(
                    asset_id=source_asset_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    kind=AssetKind.SOURCE,
                    source_type=AssetSourceType.USER_UPLOAD,
                    storage_key=f"{prefix}/source/original.mp4",
                ),
                _asset(
                    asset_id=uuid4(),
                    workspace_id=workspace_id,
                    project_id=project_id,
                    kind=AssetKind.PROXY,
                    source_type=AssetSourceType.DERIVED,
                    storage_key=f"{prefix}/derivatives/proxy.mp4",
                ),
            ]
        )

    # An API process may never hold worker privileges, so the half of the pipeline the
    # worker owns is written under a settings copy that says what it is being. Seeding
    # therefore needs both logins, which a deployment of either process alone would not
    # have — one more reason this only ever runs on a developer's machine.
    if not settings.worker_database_url:
        raise SeedRefusedError("CLIPAH_WORKER_DATABASE_URL must be configured to seed a clip")
    worker_settings = settings.model_copy(update={"process_role": ProcessRole.WORKER})
    with session_scope(
        settings=worker_settings, runtime_role=RuntimeRole.WORKER, **tenant
    ) as session:
        session.add(
            Transcript(
                id=transcript_id,
                workspace_id=workspace_id,
                project_id=project_id,
                asset_id=source_asset_id,
                provider="seed",
                provider_version="0",
                model="seed",
                language="en",
                full_text=" ".join(str(word["text"]) for word in SEEDED_WORDS),
                words=SEEDED_WORDS,
                speaker_segments=[],
                utterances=[],
                duration_ms=SOURCE_DURATION_MS,
                raw_result_storage_key=f"{prefix}/transcripts/raw.json",
            )
        )
        session.flush()
        session.add(
            ClipCandidate(
                id=candidate_id,
                workspace_id=workspace_id,
                project_id=project_id,
                transcript_id=transcript_id,
                rank=1,
                score=0.9,
                hook="The surprising opening",
                payoff="The useful resolution",
                reason="A complete and useful moment",
                category="insight",
                tags=["creator"],
                start_ms=CLIP_START_MS,
                end_ms=CLIP_END_MS,
                start_word_id=str(SEEDED_WORDS[0]["word_id"]),
                end_word_id=str(SEEDED_WORDS[-1]["word_id"]),
                transcript_excerpt=" ".join(str(word["text"]) for word in SEEDED_WORDS),
                context_dependencies=[],
                score_breakdown={
                    "hook": 0.9,
                    "payoff": 0.9,
                    "narrative_completeness": 0.9,
                    "context_safety": 0.9,
                    "platform_fit": 0.9,
                    "transcript_confidence": 0.9,
                    "visual_opportunity": 0.9,
                },
                context_warnings=[],
                visual_opportunities=[],
                model_metadata={"exposed": True},
                created_at=now,
            )
        )

    return SeededProject(
        project_id=project_id,
        candidate_id=candidate_id,
        source_asset_id=source_asset_id,
        name=name,
    )


def _asset(
    *,
    asset_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    kind: AssetKind,
    source_type: AssetSourceType,
    storage_key: str,
) -> Asset:
    """Describe one stored rendition of the seeded source."""
    return Asset(
        id=asset_id,
        workspace_id=workspace_id,
        project_id=project_id,
        kind=kind,
        source_type=source_type,
        storage_key=storage_key,
        content_type="video/mp4",
        size_bytes=1_024,
        duration_ms=SOURCE_DURATION_MS,
        width=1080,
        height=1920,
        sha256=bytes(32),
    )


def main(argv: list[str] | None = None, *, settings: Settings | None = None) -> int:
    """Seed one member from the command line and print their cookies as JSON."""
    parser = argparse.ArgumentParser(description="Seed one signed-in member for browser tests.")
    parser.add_argument("--email", default="e2e@example.com")
    parser.add_argument("--display-name", default="End To End")
    parser.add_argument("--workspace-name", default="End To End")
    parser.add_argument(
        "--with-clip",
        metavar="NAME",
        default=None,
        help="also stage an analysed Project under this name, with one reviewable clip",
    )
    parser.add_argument(
        "--team-workspace",
        action="store_true",
        help="return a team Workspace owned by the seeded User",
    )
    arguments = parser.parse_args(argv)

    settings = settings or Settings()
    now = datetime.now(tz=UTC)
    with session_scope(settings=settings) as session:
        seeded = seed_browser_session(
            session,
            settings=settings,
            email=arguments.email,
            display_name=arguments.display_name,
            workspace_name=arguments.workspace_name,
            now=now,
            team_workspace=arguments.team_workspace,
        )
    body = _as_json(seeded)
    if arguments.with_clip is not None:
        project = seed_analysed_project(
            settings=settings,
            workspace_id=seeded.workspace_id,
            user_id=seeded.user_id,
            name=arguments.with_clip,
            now=now,
        )
        body["project"] = _project_as_json(project)
    print(json.dumps(body))
    return 0


def _project_as_json(project: SeededProject) -> dict[str, str]:
    """Render one seeded clip for a test runner written in another language."""
    return {
        "projectId": str(project.project_id),
        "candidateId": str(project.candidate_id),
        "sourceAssetId": str(project.source_asset_id),
        "name": project.name,
    }


def _as_json(seeded: SeededBrowserSession) -> dict[str, object]:
    """Render one seeded Session for a test runner written in another language."""
    return {
        "userId": str(seeded.user_id),
        "workspaceId": str(seeded.workspace_id),
        "sessionCookieName": seeded.session_cookie_name,
        "sessionToken": seeded.session_token,
        "csrfCookieName": seeded.csrf_cookie_name,
        "csrfToken": seeded.csrf_token,
    }


def _policy(settings: Settings) -> SessionPolicy:
    """Give a seeded Session the same deadlines a real login would have given it."""
    return SessionPolicy(
        idle_ttl=timedelta(minutes=settings.session_idle_ttl_minutes),
        absolute_ttl=timedelta(minutes=settings.session_absolute_ttl_minutes),
        recent_auth_window=timedelta(minutes=settings.session_recent_auth_ttl_minutes),
    )


def _session_secret(settings: Settings) -> str:
    """Read the secret Sessions are keyed with, refusing to invent one."""
    secret = settings.session_secret
    if secret is None:
        raise SeedRefusedError("CLIPAH_SESSION_SECRET must be configured to seed a Session")
    return secret.get_secret_value()


def _csrf_token() -> str:
    """Mint the double-submit token the browser echoes back on unsafe requests."""
    return secrets.token_urlsafe(CSRF_TOKEN_BYTES)


if __name__ == "__main__":
    raise SystemExit(main())
