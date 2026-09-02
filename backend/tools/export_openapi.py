"""Write the backend's OpenAPI document to `contracts/openapi.json`.

The frontend never hand-writes request or response types. It generates them from this
document, so the document has to be produced from the same application factory the API
serves, and it has to be reproducible: no live infrastructure, no credentials, and no
clock or environment values leaking into the output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clipah.api.app import ReadinessProbes, create_app
from clipah.config import Environment, Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "openapi.json"


def build_document() -> dict[str, Any]:
    """Return the OpenAPI document of an application built without any adapters."""
    settings = Settings(environment=Environment.TEST)
    app = create_app(settings, readiness_probes=ReadinessProbes())
    return app.openapi()


def main() -> None:
    """Write the document to its contract path with stable key order and a final newline."""
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    document = json.dumps(build_document(), indent=2, sort_keys=True)
    CONTRACT_PATH.write_text(f"{document}\n", encoding="utf-8")
    print(f"wrote {CONTRACT_PATH.relative_to(REPOSITORY_ROOT)}")


if __name__ == "__main__":
    main()
