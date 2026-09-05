"""Importing with a leased credential: how the jar reaches the provider, and how it leaves.

The credential has to become a file, because that is the only way yt-dlp accepts one. The
questions worth asking are therefore about that file: who can read it while it exists, how
it is named to the provider, whether its path can leak, and whether it survives anything —
a success, a failure, a cancellation, or a worker that is killed mid-import.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from clipah.assets.youtube import NormalizedYouTubeUrl, SourceUnavailableError
from clipah.source_connectors.authenticated_youtube import (
    COOKIE_FILE_NAME,
    AuthenticatedYtDlpSourceImporter,
    materialized_cookie_jar,
)
from clipah.source_connectors.secrets import SecretLease, SecretLeaseExpiredError

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
JAR = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\tcanary\n"
SOURCE = NormalizedYouTubeUrl(
    canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    video_id="dQw4w9WgXcQ",
    host="www.youtube.com",
    addresses=frozenset({IPv4Address("142.250.72.14")}),
)


def lease(secret: bytes = JAR, *, expires_in: timedelta = timedelta(minutes=30)) -> SecretLease:
    """One credential borrowed for one Job."""
    return SecretLease(
        connection_id=uuid4(),
        job_id=uuid4(),
        secret=bytearray(secret),
        expires_at=NOW + expires_in,
    )


class RecordingImporter:
    """A public importer that records what it was asked to run, and answers as told."""

    def __init__(self, *, failure: Exception | None = None) -> None:
        """Bind the failure this importer raises, if it is meant to fail."""
        self.calls: list[dict[str, Any]] = []
        self.observed_jar: bytes | None = None
        self.observed_mode: int | None = None
        self._failure = failure

    def import_source(
        self,
        source: NormalizedYouTubeUrl,
        *,
        workspace: Path,
        object_key: str,
        cancellation_check: Any,
        cookie_file: Path | None = None,
    ) -> Any:
        """Read the jar the way the provider would, then succeed or fail as configured."""
        self.calls.append({"source": source, "cookie_file": cookie_file})
        if cookie_file is not None:
            self.observed_jar = cookie_file.read_bytes()
            self.observed_mode = cookie_file.stat().st_mode & 0o777
        if self._failure is not None:
            raise self._failure
        return object()


@pytest.mark.unit
def test_the_jar_is_written_where_only_this_job_can_read_it(tmp_path: Path) -> None:
    """A credential readable by another local user is a credential given away."""
    importer = RecordingImporter()
    borrowed = lease()

    AuthenticatedYtDlpSourceImporter(
        importer,  # type: ignore[arg-type]
        lease=borrowed,
        now=lambda: NOW,
    ).import_source(SOURCE, workspace=tmp_path, object_key="key", cancellation_check=lambda: None)

    assert importer.observed_jar == JAR
    assert importer.observed_mode == 0o600


@pytest.mark.unit
def test_the_jar_is_named_to_the_provider_as_one_argument_value(tmp_path: Path) -> None:
    """A path spliced into a command line is a path somebody else's quoting can change."""
    importer = RecordingImporter()

    AuthenticatedYtDlpSourceImporter(
        importer,  # type: ignore[arg-type]
        lease=lease(),
        now=lambda: NOW,
    ).import_source(SOURCE, workspace=tmp_path, object_key="key", cancellation_check=lambda: None)

    passed = importer.calls[0]["cookie_file"]
    assert passed == tmp_path / COOKIE_FILE_NAME
    assert passed.parent == tmp_path


@pytest.mark.unit
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(None, id="success"),
        pytest.param(SourceUnavailableError(), id="provider-failure"),
        pytest.param(KeyboardInterrupt(), id="worker-interrupted"),
    ],
)
def test_the_jar_is_removed_however_the_import_ends(
    tmp_path: Path, failure: BaseException | None
) -> None:
    """Success, failure, and a worker being killed all leave the same empty directory."""
    importer = RecordingImporter(failure=failure)  # type: ignore[arg-type]
    borrowed = lease()
    run = AuthenticatedYtDlpSourceImporter(
        importer,  # type: ignore[arg-type]
        lease=borrowed,
        now=lambda: NOW,
    ).import_source

    if failure is None:
        run(SOURCE, workspace=tmp_path, object_key="key", cancellation_check=lambda: None)
    else:
        with pytest.raises(type(failure)):
            run(SOURCE, workspace=tmp_path, object_key="key", cancellation_check=lambda: None)

    assert not (tmp_path / COOKIE_FILE_NAME).exists()
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(SecretLeaseExpiredError):
        borrowed.plaintext(now=NOW)


@pytest.mark.unit
def test_an_expired_lease_never_becomes_a_file_at_all(tmp_path: Path) -> None:
    """A loan that has run out may not be spent, and refusing early writes nothing."""
    borrowed = lease(expires_in=timedelta(seconds=-1))

    with (
        pytest.raises(SecretLeaseExpiredError),
        materialized_cookie_jar(borrowed, workspace=tmp_path, now=NOW),
    ):
        pass

    assert not (tmp_path / COOKIE_FILE_NAME).exists()


@pytest.mark.unit
def test_a_jar_left_behind_by_a_previous_attempt_is_never_reused(tmp_path: Path) -> None:
    """Writing over an existing file would trust a file this attempt did not create."""
    existing = tmp_path / COOKIE_FILE_NAME
    existing.write_bytes(b"somebody else's jar")

    with (
        pytest.raises(FileExistsError),
        materialized_cookie_jar(lease(), workspace=tmp_path, now=NOW),
    ):
        pass


@pytest.mark.unit
def test_the_jar_is_written_before_it_is_readable_rather_than_afterwards(
    tmp_path: Path,
) -> None:
    """Creating a file and then narrowing it leaves an instant when it is wide open."""
    with materialized_cookie_jar(lease(), workspace=tmp_path, now=NOW) as jar:
        mode = os.stat(jar).st_mode & 0o777
        assert mode == 0o600
        assert jar.read_bytes() == JAR
