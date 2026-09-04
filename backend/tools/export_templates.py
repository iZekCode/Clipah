"""Write the built-in template and motion contract both sides read.

The backend publishes the looks a member can apply and the window each movement is
legible within. The editor offers exactly those, and the renderer enforces exactly those,
so the definitions are exported once here rather than written down twice.
"""

from __future__ import annotations

import json
from pathlib import Path

from clipah.renders.templates import templates_document

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "templates.json"
EDITOR_PATH = REPOSITORY_ROOT / "frontend" / "features" / "editor" / "templates.generated.json"


def main() -> None:
    """Write the document with stable key order and a final newline, for both readers."""
    document = json.dumps(templates_document(), indent=2, sort_keys=True)
    for path in (CONTRACT_PATH, EDITOR_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{document}\n", encoding="utf-8")
        print(f"wrote {path.relative_to(REPOSITORY_ROOT)}")


if __name__ == "__main__":
    main()
