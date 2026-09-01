#!/bin/sh
# Run the versioned highlight evaluation over the checked-in labeled cases.
#
#   scripts/run-highlight-eval.sh --adapter=fake
#   scripts/run-highlight-eval.sh --adapter=groq --output=reports/highlight-groq.json
#
# The fake adapter is offline and deterministic and must always pass. A live adapter needs its
# credential in the environment and produces a report meant to be kept as an artifact.
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root/backend"

exec uv run python -m evals.highlights.runner "$@"
