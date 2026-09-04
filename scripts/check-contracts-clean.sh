#!/bin/sh
# Fail when a generated contract or client is out of date with the code that produces it.
#
#   scripts/check-contracts-clean.sh
#
# Snapshots the generated files, regenerates the OpenAPI document, the composition JSON
# Schema, the typed API client, and the composition TypeScript types, then fails if any of
# them changed. The comparison is against the files on disk rather than against git, so the
# check answers the same way before a commit and inside CI. A generated file that is edited
# by hand, or left stale, is a contract nobody agreed to.
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root"

targets="contracts/openapi.json contracts/composition.schema.json contracts/templates.json frontend/lib/api/generated frontend/features/editor/composition.generated.ts frontend/features/editor/templates.generated.json"

snapshot=$(mktemp -d)
trap 'rm -rf "$snapshot"' EXIT
for target in $targets; do
    mkdir -p "$snapshot/$(dirname "$target")"
    cp -R "$target" "$snapshot/$target"
done

"$repository_root/scripts/export-openapi.sh"
"$repository_root/scripts/export-composition-schema.sh"
"$repository_root/scripts/export-templates.sh"
pnpm --filter clipah-frontend generate:api
pnpm --filter clipah-frontend generate:composition

status=0
for target in $targets; do
    if ! diff -r "$snapshot/$target" "$target" >/dev/null 2>&1; then
        echo "stale generated contract: $target" >&2
        status=1
    fi
done

if [ "$status" -ne 0 ]; then
    echo "Generated contracts were out of date and have been regenerated. Commit the result." >&2
    exit 1
fi

echo "generated contracts are up to date"
