#!/usr/bin/env bash
#
# Fail the editor-engine bake-off on a license Clipah cannot ship.
#
# Three things end the run: a GPL-family license, which would reach the whole browser
# bundle; a license the registry does not state, which cannot be reviewed; and a
# commercial license with no recorded owner and renewal cost. Everything else is
# reported so the ADR can record the obligation rather than discover it later.
set -euo pipefail

cd "$(dirname "$0")/.."

# Commercial dependencies that have been reviewed, as `name=owner:renewal-cost`.
# A commercial license absent from this list fails the check.
REVIEWED_COMMERCIAL=()

readonly FORBIDDEN='^(GPL-|AGPL-|SSPL|CC-BY-NC)'
readonly COMMERCIAL='^(UNLICENSED|SEE LICENSE|Commercial|Proprietary)'

report="$(pnpm licenses list --json)"

failures=0
note() { printf '%s\n' "$*" >&2; }

while IFS=$'\t' read -r license package; do
  [ -z "$license" ] && continue
  if [[ "$license" =~ $FORBIDDEN ]]; then
    note "FAIL  $package is $license; a copyleft browser dependency cannot ship in Clipah."
    failures=$((failures + 1))
    continue
  fi
  if [ "$license" = "Unknown" ] || [ -z "$license" ]; then
    note "FAIL  $package states no license; it cannot be reviewed."
    failures=$((failures + 1))
    continue
  fi
  if [[ "$license" =~ $COMMERCIAL ]]; then
    reviewed=0
    for entry in ${REVIEWED_COMMERCIAL[@]+"${REVIEWED_COMMERCIAL[@]}"}; do
      [ "${entry%%=*}" = "$package" ] && reviewed=1
    done
    if [ "$reviewed" -eq 0 ]; then
      note "FAIL  $package is $license with no recorded owner and renewal cost."
      failures=$((failures + 1))
    fi
  fi
done < <(printf '%s' "$report" | python3 -c '
import json, sys

report = json.load(sys.stdin)
for license_name, packages in report.items():
    for package in packages:
        print(license_name + "\t" + package["name"])
')

# Weak copyleft is not a blocker, but it carries obligations that have to be recorded
# rather than discovered during a release.
note ""
note "Weak-copyleft obligations"
printf '%s' "$report" | python3 -c '
import json, sys

report = json.load(sys.stdin)
found = False
for license_name, packages in report.items():
    if not (license_name.startswith("LGPL") or license_name.startswith("MPL")):
        continue
    for entry in packages:
        found = True
        print("  " + entry["name"] + " " + ",".join(entry["versions"]) + " is " + license_name)
if not found:
    print("  none")
' >&2

# The engines the adoption gate names, reported whether or not they are installed, so the
# ADR never records an obligation that was simply not looked at.
note ""
note "Editor-engine license obligations"
for package in "@elah/core" "mediabunny"; do
  line="$(printf '%s' "$report" | python3 -c "
import json, sys
report = json.load(sys.stdin)
for license_name, packages in report.items():
    for entry in packages:
        if entry['name'] == '$package':
            print(f\"  {entry['name']} {','.join(entry['versions'])} is {license_name}\")
")"
  if [ -n "$line" ]; then
    note "$line"
  else
    note "  $package is not installed"
  fi
done
note "  OpenVideo Editor and Remotion are not installed; both require a separate license"
note "  review before any production use, and neither may be added without one."

if [ "$failures" -gt 0 ]; then
  note ""
  note "$failures dependency license(s) block adoption."
  exit 1
fi
note ""
note "No dependency license blocks adoption."
