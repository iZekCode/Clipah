"""In-process HTTP harness: a stubbed provider, a hand-wound clock, and a cookie jar.

Every test that drives the API through a browser shares this harness so login,
Session, and Workspace behavior are exercised through the same public surface a
real client would use.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import anyio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Cookies, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from clipah.api.app import create_app
from clipah.api.dependencies import CSRF_HEADER, AuthComponents, session_policy
from clipah.assets.storage import ObjectStore
from clipah.auth.google_oidc import GOOGLE_ISSUER, GoogleOidcFlow, IdTokenClaims
from clipah.auth.limits import RateLimiter
from clipah.auth.models import AuthorizationRedirect
from clipah.config import Settings
from clipah.db import session_scope
from clipah.source_imports.dispatch import JobDispatcher
from support import runtime_settings

SESSION_SECRET = "a-test-session-secret-of-at-least-32-characters"
CLIENT_ID = "clipah-test-client-id.apps.googleusercontent.com"
REDIRECT_URI = "http://testserver/api/v1/auth/google/callback"
SITE_ORIGIN = "http://testserver"
FOREIGN_ORIGIN = "http://attacker.example"
# Postgres stamps `created_at` from the server clock, and several tables check that an
# expiry lies after it, so the hand-wound clock has to start from the same present day.
NOW = datetime.now(tz=UTC).replace(microsecond=0)
AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"


class Clock:
    """A hand-wound clock the whole application reads deadlines from."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def advance(self, amount: timedelta) -> None:
        """Move every deadline evaluation forward by one interval."""
        self.now += amount

    def __call__(self) -> datetime:
        """Return the instant the application should treat as current."""
        return self.now


class StubGoogleProvider:
    """Return the claims Google would return, without leaving the process."""

    def __init__(self, clock: Clock, **claim_overrides: Any) -> None:
        """Bind one provider stub to the clock its tokens are dated against."""
        self.clock = clock
        self.claim_overrides = dict(claim_overrides)
        self.nonce = ""
        self.exchanges: list[dict[str, str]] = []

    def identify(self, *, subject: str, email: str, name: str) -> None:
        """Speak for a different Google account from the next exchange onward."""
        self.claim_overrides.update({"subject": subject, "email": email, "name": name})

    def authorization_endpoint(self) -> str:
        """Return Google's advertised authorization endpoint."""
        return AUTHORIZATION_ENDPOINT

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> IdTokenClaims:
        """Record the redemption and mint claims bound to the live ceremony."""
        self.exchanges.append(
            {"code": code, "code_verifier": code_verifier, "redirect_uri": redirect_uri}
        )
        defaults: dict[str, Any] = {
            "issuer": GOOGLE_ISSUER,
            "subject": "108422224444555566667",
            "audience": CLIENT_ID,
            "nonce": self.nonce,
            "email": "creator@example.com",
            "email_verified": True,
            "name": "Creator Example",
            "picture": "https://lh3.googleusercontent.com/a/avatar",
            "issued_at": self.clock(),
            "expires_at": self.clock() + timedelta(hours=1),
        }
        return IdTokenClaims(**{**defaults, **self.claim_overrides})


class RecordingFlow(GoogleOidcFlow):
    """Expose the ceremony bindings a real browser would only ever hold sealed."""

    def __init__(self, *, provider: StubGoogleProvider) -> None:
        """Bind the ceremony to the stub provider and this test client registration."""
        super().__init__(provider=provider, client_id=CLIENT_ID, redirect_uri=REDIRECT_URI)
        self.stub = provider
        self.last_pending_state = ""

    def start(self, *, now: datetime) -> AuthorizationRedirect:
        """Start the ceremony and hand its bindings to both the test and the stub."""
        redirect = super().start(now=now)
        self.last_pending_state = redirect.pending.state
        self.stub.nonce = redirect.pending.nonce
        return redirect


@dataclass(frozen=True, slots=True)
class ServerSentEvent:
    """One frame a browser's ``EventSource`` would deliver to its listeners."""

    id: str | None = None
    event: str | None = None
    data: str = ""
    comment: str | None = None


def _parse_sse_block(block: list[str]) -> ServerSentEvent | None:
    """Turn one blank-line-delimited Server-Sent Events block into a frame."""
    if not block:
        return None
    fields: dict[str, str] = {}
    data: list[str] = []
    for line in block:
        if line.startswith(":"):
            return ServerSentEvent(comment=line[1:].strip())
        name, _, value = line.partition(":")
        if name == "data":
            data.append(value.lstrip())
        else:
            fields[name] = value.lstrip()
    return ServerSentEvent(id=fields.get("id"), event=fields.get("event"), data="\n".join(data))


class Browser:
    """One browser's cookie jar driven across a sequence of in-process requests."""

    def __init__(self, app: FastAPI, *, origin: str = SITE_ORIGIN) -> None:
        """Start an empty jar against one application and one site origin."""
        self.app = app
        self.origin = origin
        self.cookies = Cookies()

    def get(self, path: str, **kwargs: Any) -> Response:
        """Perform one safe request."""
        return self.request("GET", path, **kwargs)

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        origin: str | None = None,
        csrf_token: str | None = None,
    ) -> Response:
        """Send one request, echoing the double-submit token as a first-party client would."""
        request_headers = dict(headers or {})
        if method not in {"GET", "HEAD", "OPTIONS"}:
            request_headers.setdefault("Origin", origin or self.origin)
            token = csrf_token if csrf_token is not None else self.cookies.get("clipah_csrf")
            if token:
                request_headers.setdefault(CSRF_HEADER, token)
        return asyncio.run(self._request(method, path, request_headers, json))

    def stream(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        limit: int | None = None,
        comments: bool = False,
        while_open: Callable[[], None] | None = None,
    ) -> list[ServerSentEvent]:
        """Read one Server-Sent Events response until it closes or ``limit`` frames arrive.

        ``while_open`` is called once the first frame has arrived and before the client
        disconnects, so a test can observe what the server is holding open on its behalf.
        """
        return asyncio.run(self._stream(path, dict(headers or {}), limit, comments, while_open))

    async def _stream(
        self,
        path: str,
        headers: dict[str, str],
        limit: int | None,
        comments: bool,
        while_open: Callable[[], None] | None = None,
    ) -> list[ServerSentEvent]:
        """Drive the ASGI application directly, because a test client buffers whole bodies.

        Reading an endless stream needs frames as they are written and a disconnect the
        server can observe, which is exactly what an ASGI ``send``/``receive`` pair gives.
        """
        frames: list[ServerSentEvent] = []
        pending = b""
        disconnected = anyio.Event()

        async def receive() -> dict[str, Any]:
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            nonlocal pending
            if message["type"] != "http.response.body":
                return
            pending += message.get("body", b"")
            while b"\n\n" in pending:
                block, _, pending = pending.partition(b"\n\n")
                frame = _parse_sse_block(block.decode().splitlines())
                if frame is None or (frame.comment is not None and not comments):
                    continue
                frames.append(frame)
                if len(frames) == 1 and while_open is not None:
                    while_open()
                if limit is not None and len(frames) >= limit:
                    disconnected.set()

        await self.app(self._stream_scope(path, headers), receive, send)
        return frames

    def _stream_scope(self, path: str, headers: dict[str, str]) -> dict[str, Any]:
        """Describe one streaming GET the way an ASGI server would."""
        target, _, query = path.partition("?")
        host = urlsplit(self.origin).netloc
        sent = {**headers, "host": host}
        cookies = "; ".join(f"{name}={value}" for name, value in self.cookies.items())
        if cookies:
            sent["cookie"] = cookies
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.1"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": target,
            "raw_path": target.encode(),
            "root_path": "",
            "query_string": query.encode(),
            "headers": [(name.lower().encode(), value.encode()) for name, value in sent.items()],
            "client": ("testclient", 50000),
            "server": (host, 80),
        }

    async def _request(
        self, method: str, path: str, headers: dict[str, str], json: Any
    ) -> Response:
        transport = ASGITransport(app=self.app)
        async with AsyncClient(
            transport=transport, base_url=self.origin, cookies=self.cookies
        ) as client:
            response = await client.request(
                method, path, headers=headers, json=json, follow_redirects=False
            )
        self.cookies.extract_cookies(response)
        return response


@contextmanager
def _recording_scope(settings: Settings, recorded: list[dict[str, str]]) -> Iterator[Session]:
    """Run one request transaction and keep the tenant context it ends up holding."""
    with session_scope(settings=settings) as session:
        yield session
        recorded.append(
            {
                name: session.execute(
                    text("SELECT current_setting(:name, true)"), {"name": name}
                ).scalar_one()
                or ""
                for name in ("clipah.workspace_id", "clipah.user_id")
            }
        )


def build_app(
    clock: Clock,
    provider: StubGoogleProvider,
    *,
    recorded_contexts: list[dict[str, str]] | None = None,
    object_store: ObjectStore | None = None,
    rate_limiter: RateLimiter | None = None,
    job_dispatcher: JobDispatcher | None = None,
    source_url_validator: Any = None,
    generation_providers: Any = None,
    **setting_overrides: object,
) -> tuple[FastAPI, RecordingFlow, Settings]:
    """Compose the application against a stubbed provider and a hand-wound clock."""
    settings = runtime_settings(
        **{
            "session_secret": SESSION_SECRET,
            "frontend_origin": SITE_ORIGIN,
            **setting_overrides,
        }
    )
    flow = RecordingFlow(provider=provider)

    def open_session() -> AbstractContextManager[Session]:
        if recorded_contexts is None:
            return session_scope(settings=settings)
        return _recording_scope(settings, recorded_contexts)

    components = AuthComponents(
        oidc_flow=lambda: flow,
        open_session=open_session,
        policy=session_policy(settings),
        now=clock,
    )
    return (
        create_app(
            settings,
            auth_components=components,
            object_store=object_store,
            rate_limiter=rate_limiter,
            job_dispatcher=job_dispatcher,
            source_url_validator=source_url_validator,
            generation_providers=generation_providers,
        ),
        flow,
        settings,
    )


def sign_in(browser: Browser, flow: RecordingFlow, *, code: str = "authorization-code") -> Response:
    """Run one full login ceremony and return the callback response."""
    browser.get("/api/v1/auth/google/start")
    return browser.get(f"/api/v1/auth/google/callback?code={code}&state={flow.last_pending_state}")


def assert_error(response: Response, *, status_code: int, code: str) -> None:
    """Assert the sanitized public envelope for one refusal."""
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
