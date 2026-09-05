#!/bin/sh
# Run the B-roll retrieval evaluation over the checked-in labeled cases.
#
#   scripts/run-broll-eval.sh --adapter=fake
#   scripts/run-broll-eval.sh --adapter=fake-vision --output=reports/broll-vision.json
#
# Both adapters are offline and deterministic and must always pass. The gates are complete
# provenance on every selection, zero unsafe selections, and top-three relevance at or
# above 80%.
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root/backend"

exec uv run python -m evals.broll.runner "$@"
