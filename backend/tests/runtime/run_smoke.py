"""Execute the complete deterministic runtime proof inside the smoke image."""

from __future__ import annotations

import argparse
import os
import subprocess
from uuid import UUID

from seed_smoke import SCENARIOS, SMOKE_IDENTITY, validate_fixture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", type=UUID, default=SMOKE_IDENTITY)
    args = parser.parse_args()
    if args.identity != SMOKE_IDENTITY:
        raise SystemExit("runtime smoke identity unavailable")
    validate_fixture()

    environment = dict(os.environ)
    environment["CLIPAH_SMOKE_IDENTITY"] = str(args.identity)
    command = [
        "python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--maxfail=1",
        *[scenario.node_id for scenario in SCENARIOS],
    ]
    completed = subprocess.run(command, env=environment, timeout=300, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
