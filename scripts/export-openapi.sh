#!/bin/sh
# Regenerate the OpenAPI contract the frontend client is generated from.
#
#   scripts/export-openapi.sh
#
# Run this after any change to a route, request schema, or response schema, then
# regenerate the typed client with `pnpm --filter clipah-frontend generate:api`.
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root/backend"

exec uv run python tools/export_openapi.py "$@"
