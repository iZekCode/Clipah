#!/bin/sh
# Regenerate the composition contract the editor's TypeScript types are generated from.
#
#   scripts/export-composition-schema.sh
#
# Run this after any change to `backend/src/clipah/editor/models.py`, then regenerate the
# TypeScript with `pnpm --filter clipah-frontend generate:composition`.
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root/backend"

exec uv run python tools/export_composition_schema.py "$@"
