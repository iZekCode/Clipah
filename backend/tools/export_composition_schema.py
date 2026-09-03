"""Write the composition contract to `contracts/composition.schema.json`.

The backend validates compositions with `CompositionV1`, and the browser must apply the
same rules. Publishing the model's own JSON Schema keeps one definition: the schema is
generated here, and `pnpm generate:composition` turns it into TypeScript. Neither output
is edited by hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clipah.editor.models import CompositionV1

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "composition.schema.json"
SCHEMA_ID = "https://clipah.com/contracts/composition.schema.json"


def build_schema() -> dict[str, Any]:
    """Return the JSON Schema of the one composition document version 1 allows."""
    schema = CompositionV1.model_json_schema(by_alias=True, mode="validation")
    _drop_property_titles(schema)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = SCHEMA_ID
    schema["title"] = "CompositionV1"
    return schema


def _drop_property_titles(schema: Any) -> None:
    """Remove the per-field titles a generator would turn into one alias type each.

    A named object keeps its own title, because that is the TypeScript type name; a
    property called `gainDb` does not need a second type called `Gaindb`.
    """
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                for field_schema in value.values():
                    if isinstance(field_schema, dict):
                        field_schema.pop("title", None)
            _drop_property_titles(value)
    elif isinstance(schema, list):
        for item in schema:
            _drop_property_titles(item)


def main() -> None:
    """Write the schema with stable key order and a final newline."""
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    document = json.dumps(build_schema(), indent=2, sort_keys=True)
    CONTRACT_PATH.write_text(f"{document}\n", encoding="utf-8")
    print(f"wrote {CONTRACT_PATH.relative_to(REPOSITORY_ROOT)}")


if __name__ == "__main__":
    main()
