#!/bin/sh
# Regenerate the built-in template and motion contract the editor reads.
#
#   scripts/export-templates.sh
#
# Run this after any change to `backend/src/clipah/renders/templates.py`. The document is
# written to `contracts/templates.json` and to the copy the frontend bundles.
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root/backend"

exec uv run python tools/export_templates.py "$@"
