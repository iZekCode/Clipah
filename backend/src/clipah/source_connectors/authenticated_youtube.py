"""Import one source with the member's own credential, for exactly as long as it takes.

The credential reaches yt-dlp the only way it can: as a file. Everything here is about
making that file's life as short and as narrow as possible.

* **It is written inside the Job's own workspace**, which Task 9 already proved is a
  private, `0700`, per-Job directory, and it is written `0600` before a byte goes into it.
* **It is passed as an argument-array value**, never through a shell and never through an
  environment variable, so it cannot be re-quoted or inherited.
* **Its path is redacted from diagnostics.** A provider failure carries a stable code and
  no path, because a path in a log is a hint about where a credential lived.
* **It is removed on every exit.** Success, failure, cancellation, and a worker shutdown
  all run the same cleanup, and the lease is discarded with it.

`--cookies-from-browser` is deliberately not used and not offered: on a hosted worker the
browser profile belongs to the machine rather than to the member, so the flag would read
somebody else's session. It is documented as a local, self-hosted command instead.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path

from clipah.assets.storage import StoredObject
from clipah.assets.youtube import NormalizedYouTubeUrl
from clipah.source_connectors.secrets import SecretLease
from clipah.source_connectors.yt_dlp_adapter import YtDlpSourceImporter

COOKIE_FILE_NAME = "cookies.txt"
COOKIE_FILE_MODE = 0o600


class AuthenticatedYtDlpSourceImporter:
    """Run one import with a leased cookie jar, and leave nothing of it behind."""

    def __init__(
        self,
        importer: YtDlpSourceImporter,
        *,
        lease: SecretLease,
        now: Callable[[], datetime],
    ) -> None:
        """Bind the public importer to the one credential this import may use."""
        self._importer = importer
        self._lease = lease
        self._now = now

    def import_source(
        self,
        source: NormalizedYouTubeUrl,
        *,
        workspace: Path,
        object_key: str,
        cancellation_check: Callable[[], None],
    ) -> StoredObject:
        """Materialize the jar, import through it, and remove it however this ends."""
        with materialized_cookie_jar(self._lease, workspace=workspace, now=self._now()) as jar:
            return self._importer.import_source(
                source,
                workspace=workspace,
                object_key=object_key,
                cancellation_check=cancellation_check,
                cookie_file=jar,
            )


@contextmanager
def materialized_cookie_jar(
    lease: SecretLease, *, workspace: Path, now: datetime
) -> Iterator[Path]:
    """Write one leased jar into the Job workspace and remove it on every exit.

    The file is created with its final permissions rather than adjusted afterwards, so
    there is no instant in which the credential is readable by anyone else on the host.
    """
    path = workspace / COOKIE_FILE_NAME
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, COOKIE_FILE_MODE)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(lease.plaintext(now=now))
        yield path
    finally:
        lease.discard()
        with suppress(FileNotFoundError):
            path.unlink()
