"""Integration contracts for Workspace Social Accounts and encrypted OAuth Grants."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from clipah.models import OAuthGrant, WorkspaceRole
from clipah.social_accounts.models import (
    OAuthGrantMaterial,
    OAuthTokenResult,
    PublishingCapabilities,
    RefreshedGrant,
    SocialAccountIdentity,
    SocialProvider,
)
from clipah.social_accounts.oauth import SocialProviderGrantRejectedError
from clipah.social_accounts.secrets import (
    EncryptedOAuthGrant,
    OAuthSecretContext,
    SocialSecretUnavailableError,
)
from harness import NOW, Browser, Clock, StubGoogleProvider, assert_error, build_app, sign_in

SOCIAL_TABLES = {"social_oauth_ceremonies", "social_accounts", "oauth_grants"}
SOCIAL_SECRET = "social-secret-value-that-must-never-appear"
REFRESH_SECRET = "refresh-secret-value-that-must-never-appear"
ROTATED_SECRET = "rotated-secret-value-that-must-never-appear"
AUTHORIZATION_CODE = "authorization-code-that-must-never-appear"
SOCIAL_CALLBACK = "http://testserver/api/v1/social-oauth/youtube/callback"


class StubSocialProvider:
    """Deterministic external boundary for the complete Social Account lifecycle."""

    def __init__(
        self,
        *,
        granted_scopes: frozenset[str] | None = None,
        revoke_error: Exception | None = None,
        refresh_error: Exception | None = None,
        identity: SocialAccountIdentity | None = None,
    ) -> None:
        """Return an exact provider result while retaining only safe call evidence."""
        self.granted_scopes = granted_scopes or frozenset(
            {
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/youtube.upload",
            }
        )
        self.exchanges: list[dict[str, str]] = []
        self.revoked = False
        self.revoke_calls = 0
        self.revoke_error = revoke_error
        self.refresh_error = refresh_error
        self.identity = identity or SocialAccountIdentity(
            external_account_id="UC-social-account-1",
            display_name="Creator Channel",
            avatar_url="https://images.example/channel.png",
            account_type="channel",
            login_family="youtube_data_api",
        )

    def authorization_endpoint(self) -> str:
        """Return a fixed official-shaped endpoint without making a network call."""
        return "https://accounts.google.com/o/oauth2/v2/auth"

    def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthTokenResult:
        """Return normalized tokens while recording callback bindings for assertions."""
        self.exchanges.append(
            {"code": code, "code_verifier": code_verifier, "redirect_uri": redirect_uri}
        )
        return OAuthTokenResult(
            material=OAuthGrantMaterial(
                access_token=SOCIAL_SECRET,
                refresh_token=REFRESH_SECRET,
                access_token_expires_at=NOW + timedelta(hours=1),
                refresh_token_expires_at=NOW + timedelta(days=30),
            ),
            granted_scopes=self.granted_scopes,
        )

    def account_identity(self, *, grant: OAuthGrantMaterial) -> SocialAccountIdentity:
        """Return provider-authoritative channel metadata without returning login identity."""
        assert grant.access_token == SOCIAL_SECRET
        return self.identity

    def capabilities(self, *, grant: OAuthGrantMaterial) -> PublishingCapabilities:
        """Return a safe versioned capability snapshot."""
        assert grant.access_token in {SOCIAL_SECRET, ROTATED_SECRET}
        return PublishingCapabilities(
            version="youtube-v3-test-1",
            values={"visibility": ["private"], "uploads": True},
        )

    def refresh(self, *, grant: OAuthGrantMaterial) -> RefreshedGrant:
        """Rotate the access token while preserving the provider's refresh token."""
        assert grant.refresh_token == REFRESH_SECRET
        if self.refresh_error is not None:
            raise self.refresh_error
        return RefreshedGrant(
            material=OAuthGrantMaterial(
                access_token=ROTATED_SECRET,
                refresh_token=None,
                access_token_expires_at=NOW + timedelta(hours=2),
                refresh_token_expires_at=NOW + timedelta(days=30),
            ),
            granted_scopes=self.granted_scopes,
            capabilities=PublishingCapabilities(
                version="youtube-v3-test-2",
                values={"visibility": ["private"], "uploads": True},
            ),
        )

    def revoke(self, *, grant: OAuthGrantMaterial) -> None:
        """Record successful provider revocation without retaining the credential."""
        assert grant.access_token == ROTATED_SECRET
        self.revoke_calls += 1
        if self.revoke_error is not None:
            raise self.revoke_error
        self.revoked = True


class RecordingPublicationCoordinator:
    """Record the narrow pre-publication cancellation boundary."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID, Any]] = []

    def cancel_or_pause_unpublished(
        self, *, workspace_id: UUID, social_account_id: UUID, now: Any
    ) -> None:
        self.calls.append((workspace_id, social_account_id, now))


class UnavailableSocialSecretStore:
    """Fail every cryptographic operation like an unavailable KMS dependency."""

    def encrypt(self, plaintext: bytes, *, context: OAuthSecretContext) -> EncryptedOAuthGrant:
        del plaintext, context
        raise SocialSecretUnavailableError("kms-diagnostic-secret")

    def decrypt(self, secret: EncryptedOAuthGrant, *, context: OAuthSecretContext) -> bytearray:
        del secret, context
        raise SocialSecretUnavailableError("kms-diagnostic-secret")


def social_browser(
    clock: Clock,
    provider: StubSocialProvider,
    *,
    future_publications: Any = None,
    **setting_overrides: object,
) -> tuple[Browser, UUID, Any]:
    """Sign in an owner against one enabled deterministic social provider."""
    app, login_flow, settings = build_app(
        clock,
        StubGoogleProvider(clock),
        social_providers={SocialProvider.YOUTUBE: provider},
        future_publications=future_publications,
        social_publishing_enabled=True,
        youtube_publishing_enabled=True,
        youtube_oauth_client_id="youtube-social-client",
        youtube_oauth_client_secret="youtube-social-client-secret",
        youtube_oauth_redirect_uri=SOCIAL_CALLBACK,
        youtube_api_version="v3",
        secret_encryption_key="a-thirty-two-character-secret-key-for-tests",
        **setting_overrides,
    )
    browser = Browser(app)
    sign_in(browser, login_flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    return browser, workspace_id, settings


def begin_social_connection(browser: Browser, workspace_id: UUID) -> tuple[str, str]:
    """Start one OAuth ceremony and return its callback state plus sealed cookie."""
    response = browser.request(
        "POST", f"/api/v1/workspaces/{workspace_id}/social-accounts/youtube/connect"
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorizationUrl"]).query)
    return query["state"][0], browser.cookies.get("clipah_social_oauth")


def set_workspace_role(
    engine: Engine, browser: Browser, workspace_id: UUID, role: WorkspaceRole
) -> None:
    """Change the signed-in test member's live role."""
    user_id = browser.get("/api/v1/me").json()["id"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workspace_memberships SET role = :role "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id"
            ),
            {"role": role.value, "workspace_id": workspace_id, "user_id": user_id},
        )


@pytest.mark.integration
def test_social_tables_separate_ceremonies_accounts_and_encrypted_grants(engine: Engine) -> None:
    """Keep public identity, replay evidence, and credentials in distinct boundaries."""
    inspector = inspect(engine)

    assert set(inspector.get_table_names()) >= SOCIAL_TABLES
    ceremony_columns = {
        column["name"] for column in inspector.get_columns("social_oauth_ceremonies")
    }
    account_columns = {column["name"] for column in inspector.get_columns("social_accounts")}
    grant_columns = {column["name"] for column in inspector.get_columns("oauth_grants")}

    assert {
        "id",
        "workspace_id",
        "actor_user_id",
        "provider",
        "state_hash",
        "redirect_uri",
        "requested_scopes",
        "expires_at",
        "consumed_at",
    } <= ceremony_columns
    assert {
        "id",
        "workspace_id",
        "provider",
        "external_account_id",
        "display_name",
        "login_family",
        "api_version",
        "connection_status",
        "capability_snapshot",
        "authorized_by_user_id",
        "last_validated_at",
        "revoked_at",
    } <= account_columns
    assert {
        "id",
        "workspace_id",
        "social_account_id",
        "key_reference",
        "key_version",
        "wrapped_key",
        "nonce",
        "ciphertext",
        "granted_scopes",
        "access_token_expires_at",
        "refresh_token_expires_at",
        "token_version",
        "last_refreshed_at",
        "reconnect_reason",
        "revoked_at",
    } <= grant_columns
    assert "access_token" not in grant_columns
    assert "refresh_token" not in grant_columns
    assert "authorization_code" not in ceremony_columns


@pytest.mark.integration
@pytest.mark.parametrize("table_name", sorted(SOCIAL_TABLES))
def test_social_tables_force_workspace_row_level_security(engine: Engine, table_name: str) -> None:
    """A runtime principal cannot opt out of Workspace isolation for connection data."""
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE oid = CAST(:table_name AS regclass)
                """
            ),
            {"table_name": table_name},
        ).one()

    assert row == (True, True)


@pytest.mark.integration
def test_runtime_roles_receive_no_destructive_social_table_privileges(engine: Engine) -> None:
    """A compromised runtime may rotate or revoke a grant but cannot erase its audit tombstone."""
    with engine.connect() as connection:
        privileges = {
            (row.grantee, row.table_name, row.privilege_type)
            for row in connection.execute(
                text(
                    """
                    SELECT grantee, table_name, privilege_type
                    FROM information_schema.role_table_grants
                    WHERE grantee IN ('clipah_api', 'clipah_worker')
                      AND table_name = ANY(:table_names)
                    """
                ),
                {"table_names": sorted(SOCIAL_TABLES)},
            )
        }

    assert all(privilege != "DELETE" for _, _, privilege in privileges)
    assert ("clipah_api", "social_accounts", "SELECT") in privileges
    assert ("clipah_api", "social_oauth_ceremonies", "INSERT") in privileges
    assert ("clipah_worker", "social_oauth_ceremonies", "SELECT") not in privileges
    assert ("clipah_worker", "oauth_grants", "SELECT") in privileges


@pytest.mark.integration
def test_social_schema_has_workspace_composite_foreign_keys_and_uniqueness(engine: Engine) -> None:
    """A copied account or grant must be invalid before application authorization runs."""
    inspector = inspect(engine)
    account_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("social_accounts")
    }
    grant_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("oauth_grants")
    }
    grant_foreign_keys = {
        (tuple(key["constrained_columns"]), tuple(key["referred_columns"]))
        for key in inspector.get_foreign_keys("oauth_grants")
    }

    assert ("workspace_id", "id") in account_uniques
    assert ("workspace_id", "provider", "external_account_id") in account_uniques
    assert ("workspace_id", "social_account_id") in grant_uniques
    assert (
        ("workspace_id", "social_account_id"),
        ("workspace_id", "id"),
    ) in grant_foreign_keys


@pytest.mark.integration
def test_an_owner_can_connect_refresh_and_disconnect_one_redacted_social_account(
    engine: Engine, clean_database: None
) -> None:
    """The complete lifecycle exposes account status while credentials remain encrypted."""
    del clean_database
    provider = StubSocialProvider()
    clock = Clock(NOW)
    browser, workspace_id, _ = social_browser(clock, provider)
    state, _ = begin_social_connection(browser, workspace_id)

    callback = browser.get(
        f"/api/v1/social-oauth/youtube/callback?code={AUTHORIZATION_CODE}&state={state}"
    )

    assert callback.status_code == 303, callback.text
    assert provider.exchanges == [
        {
            "code": AUTHORIZATION_CODE,
            "code_verifier": provider.exchanges[0]["code_verifier"],
            "redirect_uri": SOCIAL_CALLBACK,
        }
    ]
    assert provider.exchanges[0]["code_verifier"]

    listed = browser.get(f"/api/v1/workspaces/{workspace_id}/social-accounts")
    assert listed.status_code == 200, listed.text
    account = listed.json()["socialAccounts"][0]
    account_id = account["id"]
    assert account == {
        "id": account_id,
        "provider": "youtube",
        "externalAccountId": "UC-social-account-1",
        "displayName": "Creator Channel",
        "avatarUrl": "https://images.example/channel.png",
        "accountType": "channel",
        "loginFamily": "youtube_data_api",
        "apiVersion": "v3",
        "connectionStatus": "active",
        "grantedScopes": [
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/youtube.upload",
        ],
        "capabilityVersion": "youtube-v3-test-1",
        "authorizedByUserId": account["authorizedByUserId"],
        "accessTokenExpiresAt": (NOW + timedelta(hours=1)).isoformat(),
        "refreshTokenExpiresAt": (NOW + timedelta(days=30)).isoformat(),
        "lastValidatedAt": NOW.isoformat(),
        "createdAt": NOW.isoformat(),
        "revokedAt": None,
    }

    capabilities = browser.get(
        f"/api/v1/social-accounts/{account_id}/capabilities?workspace_id={workspace_id}"
    )
    assert capabilities.json() == {
        "version": "youtube-v3-test-1",
        "capabilities": {"visibility": ["private"], "uploads": True},
    }

    clock.advance(timedelta(seconds=1))
    refreshed = browser.request(
        "POST", f"/api/v1/social-accounts/{account_id}/refresh?workspace_id={workspace_id}"
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["capabilityVersion"] == "youtube-v3-test-2"

    clock.advance(timedelta(seconds=1))
    disconnected = browser.request(
        "DELETE", f"/api/v1/social-accounts/{account_id}?workspace_id={workspace_id}"
    )
    assert disconnected.status_code == 204, disconnected.text
    assert provider.revoked is True

    tombstone = browser.get(f"/api/v1/workspaces/{workspace_id}/social-accounts").json()[
        "socialAccounts"
    ][0]
    assert tombstone["connectionStatus"] == "revoked"
    assert tombstone["revokedAt"] == (NOW + timedelta(seconds=2)).isoformat()

    responses = " ".join((callback.text, listed.text, capabilities.text, refreshed.text))
    assert SOCIAL_SECRET not in responses
    assert REFRESH_SECRET not in responses
    assert ROTATED_SECRET not in responses
    assert AUTHORIZATION_CODE not in responses

    with engine.connect() as connection:
        grant = connection.execute(
            text(
                "SELECT wrapped_key, nonce, ciphertext, revoked_at, token_version FROM oauth_grants"
            )
        ).one()
        audits = connection.execute(
            text(
                "SELECT action, before_metadata, after_metadata FROM audit_events "
                "WHERE target_kind = 'social_account' ORDER BY created_at, id"
            )
        ).all()
    assert grant.wrapped_key == b""
    assert grant.nonce == b""
    assert grant.ciphertext == b""
    assert grant.revoked_at == NOW + timedelta(seconds=2)
    assert grant.token_version == 2
    assert [row.action for row in audits] == [
        "social_account.connected",
        "social_account.refreshed",
        "social_account.disconnected",
    ]
    assert all(
        canary not in json.dumps([row.before_metadata, row.after_metadata])
        for row in audits
        for canary in (SOCIAL_SECRET, REFRESH_SECRET, ROTATED_SECRET, AUTHORIZATION_CODE)
    )


@pytest.mark.integration
def test_a_consumed_social_oauth_callback_cannot_be_replayed(
    engine: Engine, clean_database: None
) -> None:
    """Restoring an old sealed browser cookie must not redeem a second grant."""
    del engine, clean_database
    provider = StubSocialProvider()
    browser, workspace_id, _ = social_browser(Clock(NOW), provider)
    state, sealed_cookie = begin_social_connection(browser, workspace_id)
    callback_path = f"/api/v1/social-oauth/youtube/callback?code={AUTHORIZATION_CODE}&state={state}"

    assert browser.get(callback_path).status_code == 303
    browser.cookies.set("clipah_social_oauth", sealed_cookie)
    replay = browser.get(callback_path)

    assert_error(replay, status_code=400, code="SOCIAL_OAUTH_INVALID")
    assert len(provider.exchanges) == 1


@pytest.mark.integration
def test_a_callback_with_declined_minimum_scopes_persists_no_account_or_grant(
    engine: Engine, clean_database: None
) -> None:
    """Partial consent cannot create a destination that will fail only during publishing."""
    del clean_database
    provider = StubSocialProvider(
        granted_scopes=frozenset({"https://www.googleapis.com/auth/youtube.upload"})
    )
    browser, workspace_id, _ = social_browser(Clock(NOW), provider)
    state, _ = begin_social_connection(browser, workspace_id)

    refused = browser.get(
        f"/api/v1/social-oauth/youtube/callback?code={AUTHORIZATION_CODE}&state={state}"
    )

    assert_error(refused, status_code=422, code="SOCIAL_SCOPE_MISSING")
    assert browser.cookies.get("clipah_social_oauth") is None
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM social_accounts")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM oauth_grants")).scalar_one() == 0
        assert (
            connection.execute(
                text("SELECT consumed_at IS NOT NULL FROM social_oauth_ceremonies")
            ).scalar_one()
            is True
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "role", [WorkspaceRole.VIEWER, WorkspaceRole.REVIEWER, WorkspaceRole.EDITOR]
)
def test_only_workspace_admins_and_owners_can_start_social_connections(
    engine: Engine, clean_database: None, role: WorkspaceRole
) -> None:
    """A role without connection-management authority cannot begin OAuth."""
    del clean_database
    browser, workspace_id, _ = social_browser(Clock(NOW), StubSocialProvider())
    set_workspace_role(engine, browser, workspace_id, role)

    refused = browser.request(
        "POST", f"/api/v1/workspaces/{workspace_id}/social-accounts/youtube/connect"
    )

    assert_error(refused, status_code=403, code="FORBIDDEN")


@pytest.mark.integration
def test_social_connection_mutations_require_csrf_and_recent_authentication(
    engine: Engine, clean_database: None
) -> None:
    """Starting an external authorization requires both browser and fresh-user proof."""
    del engine, clean_database
    clock = Clock(NOW)
    browser, workspace_id, settings = social_browser(clock, StubSocialProvider())
    path = f"/api/v1/workspaces/{workspace_id}/social-accounts/youtube/connect"

    without_csrf = browser.request("POST", path, csrf_token="")
    clock.advance(timedelta(minutes=settings.session_recent_auth_ttl_minutes))
    stale = browser.request("POST", path)

    assert_error(without_csrf, status_code=403, code="CSRF_FAILED")
    assert_error(stale, status_code=403, code="RECENT_AUTHENTICATION_REQUIRED")


@pytest.mark.integration
def test_disabled_social_provider_routes_are_indistinguishable_from_missing(
    engine: Engine, clean_database: None
) -> None:
    """A configured adapter is not reachable unless both feature flags enable it."""
    del engine, clean_database
    clock = Clock(NOW)
    provider = StubSocialProvider()
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        social_providers={SocialProvider.YOUTUBE: provider},
        social_publishing_enabled=False,
        youtube_publishing_enabled=True,
        youtube_oauth_client_id="youtube-social-client",
        youtube_oauth_client_secret="youtube-social-client-secret",
        youtube_oauth_redirect_uri=SOCIAL_CALLBACK,
        secret_encryption_key="a-thirty-two-character-secret-key-for-tests",
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])

    refused = browser.request(
        "POST", f"/api/v1/workspaces/{workspace_id}/social-accounts/youtube/connect"
    )

    assert_error(refused, status_code=404, code="NOT_FOUND")


@pytest.mark.integration
def test_reconnecting_the_same_provider_identity_converges_on_one_account_and_grant(
    engine: Engine, clean_database: None
) -> None:
    """Repeated explicit consent rotates one grant instead of duplicating a destination."""
    del clean_database
    provider = StubSocialProvider()
    browser, workspace_id, _ = social_browser(Clock(NOW), provider)

    for _ in range(2):
        state, _ = begin_social_connection(browser, workspace_id)
        assert (
            browser.get(
                f"/api/v1/social-oauth/youtube/callback?code={AUTHORIZATION_CODE}&state={state}"
            ).status_code
            == 303
        )

    listed = browser.get(f"/api/v1/workspaces/{workspace_id}/social-accounts").json()
    with engine.connect() as connection:
        account_count = connection.execute(
            text("SELECT count(*) FROM social_accounts")
        ).scalar_one()
        grant = connection.execute(
            text("SELECT count(*) AS count, max(token_version) AS version FROM oauth_grants")
        ).one()

    assert len(listed["socialAccounts"]) == 1
    assert account_count == 1
    assert grant == (1, 2)


@pytest.mark.integration
def test_login_source_and_each_social_provider_remain_separate_credential_families(
    engine: Engine, clean_database: None
) -> None:
    """Matching provider identifiers never merge login, import, or publishing credentials."""
    del clean_database
    clock = Clock(NOW)
    shared_external_id = "108422224444555566667"
    providers = {
        SocialProvider.YOUTUBE: StubSocialProvider(
            identity=SocialAccountIdentity(
                external_account_id=shared_external_id,
                display_name="creator@example.com",
                login_family="youtube_data_api",
            )
        ),
        SocialProvider.INSTAGRAM: StubSocialProvider(
            granted_scopes=frozenset(
                {"instagram_business_basic", "instagram_business_content_publish"}
            ),
            identity=SocialAccountIdentity(
                external_account_id=shared_external_id,
                display_name="creator@example.com",
                login_family="instagram_business_oauth",
            ),
        ),
        SocialProvider.TIKTOK: StubSocialProvider(
            granted_scopes=frozenset({"user.info.basic", "video.upload"}),
            identity=SocialAccountIdentity(
                external_account_id=shared_external_id,
                display_name="creator@example.com",
                login_family="tiktok_content_posting_oauth",
            ),
        ),
    }
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        social_providers=providers,
        social_publishing_enabled=True,
        youtube_publishing_enabled=True,
        youtube_oauth_client_id="youtube-social-client",
        youtube_oauth_client_secret="youtube-social-secret",
        youtube_oauth_redirect_uri=SOCIAL_CALLBACK,
        instagram_publishing_enabled=True,
        instagram_oauth_client_id="instagram-client",
        instagram_oauth_client_secret="instagram-secret",
        instagram_oauth_redirect_uri=("http://testserver/api/v1/social-oauth/instagram/callback"),
        tiktok_publishing_enabled=True,
        tiktok_client_key="tiktok-client",
        tiktok_client_secret="tiktok-secret",
        tiktok_oauth_redirect_uri="http://testserver/api/v1/social-oauth/tiktok/callback",
        secret_encryption_key="a-thirty-two-character-secret-key-for-tests",
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    with engine.connect() as connection:
        login_before = connection.execute(
            text("SELECT id, user_id, provider, subject FROM auth_identities")
        ).all()

    for social_provider in SocialProvider:
        started = browser.request(
            "POST",
            f"/api/v1/workspaces/{workspace_id}/social-accounts/{social_provider.value}/connect",
        )
        state = parse_qs(urlsplit(started.json()["authorizationUrl"]).query)["state"][0]
        connected = browser.get(
            f"/api/v1/social-oauth/{social_provider.value}/callback?code=x&state={state}"
        )
        assert connected.status_code == 303

    with engine.connect() as connection:
        login_after = connection.execute(
            text("SELECT id, user_id, provider, subject FROM auth_identities")
        ).all()
        social_rows = connection.execute(
            text(
                "SELECT provider, external_account_id, login_family "
                "FROM social_accounts ORDER BY provider"
            )
        ).all()
        source_count = connection.execute(text("SELECT count(*) FROM source_connections"))
        grant_count = connection.execute(text("SELECT count(*) FROM oauth_grants"))

    assert login_after == login_before
    assert {row.provider for row in social_rows} == {provider.value for provider in SocialProvider}
    assert {row.external_account_id for row in social_rows} == {shared_external_id}
    assert len({row.login_family for row in social_rows}) == 3
    assert source_count.scalar_one() == 0
    assert grant_count.scalar_one() == 3


@pytest.mark.integration
def test_guessed_social_account_ids_do_not_cross_workspace_boundaries(
    engine: Engine, clean_database: None
) -> None:
    """A valid foreign ID and a missing ID return the same public refusal."""
    del clean_database
    clock = Clock(NOW)
    provider = StubSocialProvider()
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        social_providers={SocialProvider.YOUTUBE: provider},
        social_publishing_enabled=True,
        youtube_publishing_enabled=True,
        youtube_oauth_client_id="youtube-social-client",
        youtube_oauth_client_secret="youtube-social-client-secret",
        youtube_oauth_redirect_uri=SOCIAL_CALLBACK,
        secret_encryption_key="a-thirty-two-character-secret-key-for-tests",
    )
    owner = Browser(app)
    sign_in(owner, flow)
    owner_workspace = UUID(owner.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    state, _ = begin_social_connection(owner, owner_workspace)
    owner.get(f"/api/v1/social-oauth/youtube/callback?code=x&state={state}")
    account_id = owner.get(f"/api/v1/workspaces/{owner_workspace}/social-accounts").json()[
        "socialAccounts"
    ][0]["id"]

    flow.stub.identify(subject="social-stranger", email="stranger@example.test", name="S")
    stranger = Browser(app)
    sign_in(stranger, flow)
    stranger_workspace = UUID(stranger.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    guessed = stranger.get(
        f"/api/v1/social-accounts/{account_id}/capabilities?workspace_id={stranger_workspace}"
    )
    missing = stranger.get(
        f"/api/v1/social-accounts/{uuid4()}/capabilities?workspace_id={stranger_workspace}"
    )

    assert_error(guessed, status_code=404, code="NOT_FOUND")
    assert_error(missing, status_code=404, code="NOT_FOUND")
    assert guessed.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.integration
def test_disconnect_erases_locally_after_provider_failure_and_is_idempotent(
    engine: Engine, clean_database: None
) -> None:
    """Provider failure cannot restore availability or repeat cancellation side effects."""
    del clean_database
    coordinator = RecordingPublicationCoordinator()
    provider = StubSocialProvider(revoke_error=RuntimeError("provider-secret-diagnostic"))
    browser, workspace_id, _ = social_browser(Clock(NOW), provider, future_publications=coordinator)
    state, _ = begin_social_connection(browser, workspace_id)
    browser.get(f"/api/v1/social-oauth/youtube/callback?code=x&state={state}")
    account_id = browser.get(f"/api/v1/workspaces/{workspace_id}/social-accounts").json()[
        "socialAccounts"
    ][0]["id"]
    browser.request(
        "POST", f"/api/v1/social-accounts/{account_id}/refresh?workspace_id={workspace_id}"
    )

    first = browser.request(
        "DELETE", f"/api/v1/social-accounts/{account_id}?workspace_id={workspace_id}"
    )
    second = browser.request(
        "DELETE", f"/api/v1/social-accounts/{account_id}?workspace_id={workspace_id}"
    )

    assert first.status_code == second.status_code == 204
    assert provider.revoke_calls == 1
    assert coordinator.calls == [(workspace_id, UUID(account_id), NOW)]
    with engine.connect() as connection:
        grant = connection.execute(
            text("SELECT wrapped_key, nonce, ciphertext, revoked_at FROM oauth_grants")
        ).one()
    assert grant.wrapped_key == grant.nonce == grant.ciphertext == b""
    assert grant.revoked_at == NOW


@pytest.mark.integration
def test_an_unavailable_social_secret_backend_fails_closed_with_a_sanitized_error(
    engine: Engine, clean_database: None
) -> None:
    """A redeemed provider code cannot create plaintext fallback storage when KMS is down."""
    del clean_database
    clock = Clock(NOW)
    provider = StubSocialProvider()
    app, flow, _ = build_app(
        clock,
        StubGoogleProvider(clock),
        social_providers={SocialProvider.YOUTUBE: provider},
        social_secret_store=UnavailableSocialSecretStore(),
        social_publishing_enabled=True,
        youtube_publishing_enabled=True,
        youtube_oauth_client_id="youtube-social-client",
        youtube_oauth_client_secret="youtube-client-secret-canary",
        youtube_oauth_redirect_uri=SOCIAL_CALLBACK,
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    state, _ = begin_social_connection(browser, workspace_id)

    refused = browser.get(
        f"/api/v1/social-oauth/youtube/callback?code={AUTHORIZATION_CODE}&state={state}"
    )

    assert_error(refused, status_code=503, code="SERVICE_UNAVAILABLE")
    assert browser.cookies.get("clipah_social_oauth") is None
    assert "kms-diagnostic-secret" not in refused.text
    assert "youtube-client-secret-canary" not in refused.text
    assert AUTHORIZATION_CODE not in refused.text
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM social_accounts")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM oauth_grants")).scalar_one() == 0
        assert (
            connection.execute(
                text("SELECT consumed_at IS NOT NULL FROM social_oauth_ceremonies")
            ).scalar_one()
            is True
        )


@pytest.mark.integration
def test_oauth_grant_token_version_rejects_a_stale_optimistic_replacement(
    engine: Engine, clean_database: None
) -> None:
    """A stale writer cannot overwrite a newer token rotation even outside the row-lock path."""
    del clean_database
    browser, workspace_id, _ = social_browser(Clock(NOW), StubSocialProvider())
    state, _ = begin_social_connection(browser, workspace_id)
    callback = browser.get(f"/api/v1/social-oauth/youtube/callback?code=x&state={state}")
    assert callback.status_code == 303

    first = Session(engine)
    stale = Session(engine)
    try:
        first_grant = first.scalar(select(OAuthGrant))
        stale_grant = stale.scalar(select(OAuthGrant))
        assert first_grant is not None and stale_grant is not None
        first_grant.token_version = 2
        first.commit()

        stale_grant.token_version = 2
        with pytest.raises(StaleDataError):
            stale.commit()
    finally:
        first.close()
        stale.close()


@pytest.mark.integration
def test_provider_rejection_erases_the_grant_and_requires_an_explicit_reconnect(
    engine: Engine, clean_database: None
) -> None:
    """A revoked refresh token becomes a durable safe status, never a provider diagnostic."""
    del clean_database
    provider = StubSocialProvider(
        refresh_error=SocialProviderGrantRejectedError("provider-token-canary")
    )
    browser, workspace_id, _ = social_browser(Clock(NOW), provider)
    state, _ = begin_social_connection(browser, workspace_id)
    browser.get(f"/api/v1/social-oauth/youtube/callback?code=x&state={state}")
    account_id = browser.get(f"/api/v1/workspaces/{workspace_id}/social-accounts").json()[
        "socialAccounts"
    ][0]["id"]

    refused = browser.request(
        "POST", f"/api/v1/social-accounts/{account_id}/refresh?workspace_id={workspace_id}"
    )

    assert_error(refused, status_code=409, code="SOCIAL_ACCOUNT_RECONNECT_REQUIRED")
    assert "provider-token-canary" not in refused.text
    account = browser.get(f"/api/v1/workspaces/{workspace_id}/social-accounts").json()[
        "socialAccounts"
    ][0]
    assert account["connectionStatus"] == "reconnect_required"
    with engine.connect() as connection:
        grant = connection.execute(
            text("SELECT wrapped_key, nonce, ciphertext, reconnect_reason FROM oauth_grants")
        ).one()
        audit = connection.execute(
            text(
                "SELECT action, after_metadata FROM audit_events "
                "WHERE action = 'social_account.reconnect_required'"
            )
        ).one()
    assert grant.wrapped_key == grant.nonce == grant.ciphertext == b""
    assert grant.reconnect_reason == "provider_grant_rejected"
    assert audit.after_metadata == {
        "reason": "provider_grant_rejected",
        "status": "reconnect_required",
    }


@pytest.mark.integration
def test_capabilities_report_each_social_publishing_rollout_gate(
    engine: Engine, clean_database: None
) -> None:
    """The browser learns exactly which staged gate this deployment has opened."""
    del engine, clean_database
    browser, _, _ = social_browser(
        Clock(NOW),
        StubSocialProvider(),
        instagram_publishing_enabled=True,
        instagram_oauth_client_id="instagram-client",
        instagram_oauth_client_secret="instagram-client-secret",
        instagram_oauth_redirect_uri="http://testserver/api/v1/social-oauth/instagram/callback",
        tiktok_publishing_enabled=True,
        tiktok_client_key="tiktok-client",
        tiktok_client_secret="tiktok-client-secret",
        tiktok_oauth_redirect_uri="http://testserver/api/v1/social-oauth/tiktok/callback",
        tiktok_audit_approved=False,
        multi_destination_scheduling_enabled=False,
    )

    capabilities = browser.get("/api/v1/me").json()["capabilities"]

    assert capabilities["socialPublishing"] is True
    assert capabilities["youtubePublishing"] is True
    assert capabilities["youtubePublicPrivacy"] is False
    assert capabilities["instagramPublishing"] is True
    assert capabilities["tiktokPublishing"] is True
    assert capabilities["tiktokDirectPost"] is False
    assert capabilities["multiDestinationScheduling"] is False


@pytest.mark.integration
def test_capabilities_close_every_gate_a_deployment_has_not_switched_on(
    engine: Engine, clean_database: None
) -> None:
    """A disabled deployment must never let the browser offer publishing at all."""
    del engine, clean_database
    app, login_flow, _ = build_app(Clock(NOW), StubGoogleProvider(Clock(NOW)))
    browser = Browser(app)
    sign_in(browser, login_flow)

    capabilities = browser.get("/api/v1/me").json()["capabilities"]

    assert capabilities["socialPublishing"] is False
    assert capabilities["youtubePublishing"] is False
    assert capabilities["youtubePublicPrivacy"] is False
    assert capabilities["instagramPublishing"] is False
    assert capabilities["tiktokPublishing"] is False
    assert capabilities["tiktokDirectPost"] is False
    assert capabilities["multiDestinationScheduling"] is False
