"""A cookie value must not appear anywhere except the encrypted column that holds it.

This suite exists to be paranoid on purpose. It puts one distinctive value into a jar,
drives the whole feature over it, and then goes looking for that value in every place a
credential could plausibly leak: the API's own responses, every column of every table,
the events a Job publishes, the logs the process wrote while it worked, and the text of
the errors it raised.

Any hit is a defect regardless of which layer produced it, which is why the search is over
whole rows and whole log records rather than over the fields somebody remembered.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, inspect, text

from clipah.source_connectors.connections import (
    SourceConnectionService,
    SourceConnectionUnusableError,
)
from clipah.source_connectors.cookies import REQUIRED_COOKIE_NAMES, CookieValidationError
from clipah.source_connectors.secrets import (
    SecretContext,
    SecretDecryptionError,
    local_secret_store,
)
from harness import NOW, Browser, Clock, StubGoogleProvider, build_app, sign_in

ENCRYPTION_KEY = "a-thirty-two-character-secret-key-for-tests"
CANARY = "canary-cookie-value-8f2b1c9d"
LIST_PATH = "/api/v1/source-connections"


def jar(value: str = CANARY) -> bytes:
    """One complete jar whose every value is the canary this suite hunts for."""
    expiry = int((NOW + timedelta(days=2)).timestamp())
    rows = [
        "\t".join([".youtube.com", "TRUE", "/", "TRUE", str(expiry), name, value])
        for name in sorted(REQUIRED_COOKIE_NAMES)
    ]
    return "\n".join(["# Netscape HTTP Cookie File", *rows, ""]).encode("utf-8")


def payload() -> dict[str, Any]:
    """The body that creates one connection from the canary jar."""
    return {
        "cookiesBase64": base64.b64encode(jar()).decode(),
        "consentAcknowledged": True,
        "ownershipAttested": True,
    }


def connected(clock: Clock) -> tuple[Browser, UUID, Any]:
    """Sign one member in, enable the feature, and create one connection."""
    app, flow, settings = build_app(
        clock,
        StubGoogleProvider(clock),
        authenticated_source_import_enabled=True,
        secret_encryption_key=ENCRYPTION_KEY,
    )
    browser = Browser(app)
    sign_in(browser, flow)
    workspace_id = UUID(browser.get("/api/v1/workspaces").json()["workspaces"][0]["id"])
    created = browser.request("POST", f"{LIST_PATH}?workspace_id={workspace_id}", json=payload())
    assert created.status_code == 201, created.text
    return browser, workspace_id, settings


@pytest.mark.integration
def test_no_response_this_feature_produces_carries_the_credential(
    engine: Engine, clean_database: None
) -> None:
    """The API is the surface a member and an attacker both see, so it is searched whole."""
    del clean_database, engine
    browser, workspace_id, _ = connected(Clock(NOW))

    listed = browser.get(f"{LIST_PATH}?workspace_id={workspace_id}")
    connection_id = listed.json()["connections"][0]["id"]
    revoked = browser.request("DELETE", f"{LIST_PATH}/{connection_id}?workspace_id={workspace_id}")

    for response in (listed, revoked):
        assert CANARY not in response.text
        assert CANARY not in json.dumps(dict(response.headers))


@pytest.mark.integration
def test_no_column_of_any_table_holds_the_credential_in_the_clear(
    engine: Engine, clean_database: None
) -> None:
    """A credential in a searchable column is a credential in every backup and replica."""
    del clean_database
    connected(Clock(NOW))

    with engine.begin() as connection:
        tables = inspect(engine).get_table_names()
        for table in tables:
            rows = connection.execute(text(f'SELECT * FROM "{table}"')).mappings().all()
            for row in rows:
                rendered = " ".join(repr(value) for value in row.values())
                assert CANARY not in rendered, f"{table} holds the credential in the clear"


@pytest.mark.integration
def test_nothing_written_to_the_log_while_the_feature_runs_names_the_credential(
    engine: Engine, clean_database: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Logs are read by people who have no business seeing somebody's session cookie."""
    del clean_database, engine

    with caplog.at_level(logging.DEBUG):
        browser, workspace_id, _ = connected(Clock(NOW))
        browser.get(f"{LIST_PATH}?workspace_id={workspace_id}")

    written = "\n".join(record.getMessage() for record in caplog.records)
    assert CANARY not in written


@pytest.mark.integration
def test_every_refusal_this_feature_raises_is_free_of_the_credential(
    engine: Engine, clean_database: None
) -> None:
    """A traceback is written down, and a credential inside one outlives the request."""
    del clean_database, engine
    malformed = jar().replace(b"\tTRUE\t/", b"\tMAYBE\t/")

    with pytest.raises(CookieValidationError) as refusal:
        from clipah.source_connectors.cookies import validate_cookie_jar

        validate_cookie_jar(malformed, now_epoch=int(NOW.timestamp()))

    assert CANARY not in str(refusal.value)
    assert CANARY not in repr(refusal.value)


@pytest.mark.integration
def test_a_secret_opened_in_the_wrong_context_refuses_without_describing_itself(
    engine: Engine, clean_database: None
) -> None:
    """A refusal that quoted the ciphertext would leak exactly what it protected."""
    del clean_database, engine
    store = local_secret_store(ENCRYPTION_KEY)
    encrypted = store.encrypt(
        jar(), context=SecretContext(workspace_id=uuid4(), connection_id=uuid4())
    )

    with pytest.raises(SecretDecryptionError) as refusal:
        store.decrypt(encrypted, context=SecretContext(workspace_id=uuid4(), connection_id=uuid4()))

    assert CANARY not in str(refusal.value)
    assert str(encrypted.ciphertext) not in str(refusal.value)


@pytest.mark.integration
def test_a_lease_and_its_refusals_never_print_what_was_borrowed(
    engine: Engine, clean_database: None
) -> None:
    """A lease is passed around a worker, so its own printed form is part of the boundary."""
    del clean_database
    clock = Clock(NOW)
    browser, workspace_id, _ = connected(clock)
    connection_id = UUID(
        browser.get(f"{LIST_PATH}?workspace_id={workspace_id}").json()["connections"][0]["id"]
    )
    from clipah.db import RuntimeRole, session_scope
    from support import runtime_settings

    with session_scope(
        settings=runtime_settings(RuntimeRole.WORKER),
        runtime_role=RuntimeRole.WORKER,
        workspace_id=workspace_id,
        user_id=uuid4(),
    ) as session:
        service = SourceConnectionService(session, store=local_secret_store(ENCRYPTION_KEY))
        lease = service.lease(
            workspace_id=workspace_id, connection_id=connection_id, job_id=uuid4(), now=NOW
        )

        assert CANARY.encode() in lease.plaintext(now=NOW)
        assert CANARY not in repr(lease)
        assert CANARY not in str(lease)

        lease.discard()
        with pytest.raises(SourceConnectionUnusableError) as revoked_error:
            service.lease(
                workspace_id=workspace_id,
                connection_id=connection_id,
                job_id=uuid4(),
                now=NOW + timedelta(days=30),
            )
        assert CANARY not in str(revoked_error.value)
