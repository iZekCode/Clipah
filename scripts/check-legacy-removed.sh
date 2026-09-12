#!/bin/sh
# Prove the legacy Flask stack has not come back.
#
#   scripts/check-legacy-removed.sh
#
# Each pattern names a property of the rebuilt system rather than a style preference: no
# module-level progress state, no thread as a job runner, no single shared working file, no
# repository-root cookie jar, no disabled certificate check, no markup assigned as a string,
# and none of the legacy HTTP routes. Deleting `app.py` satisfies all of them today; this
# script is what stops them returning under a different filename tomorrow.
#
# Four patterns carry a narrow, named exemption, because the same characters appear in code
# the rebuild deliberately contains. Every exemption is one file, and the reason is written
# beside it. A new file matching the pattern still fails.
#
# Documentation is exempt throughout, because the cutover record has to be able to name what
# it removed. `scripts/legacy-smoke.sh` is exempt by name: comparing the two stacks during
# the rollback window requires calling the legacy routes, and it exists for nothing else.
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root"

# Exempt everywhere: documentation, this script, and the two files written to exercise it.
common_exempt=':!*.md
:!scripts/check-legacy-removed.sh
:!scripts/legacy-smoke.sh
:!backend/tests/contract/test_legacy_removed.py
:!backend/tests/contract/test_cutover_smoke_scripts.py'

failed=0

report() {
  echo "legacy pattern still present: $1" >&2
  printf '%s\n' "$2" >&2
  failed=1
}

# $1 pattern, $2 newline-separated extra pathspecs
sweep_fixed() {
  # shellcheck disable=SC2086
  if found=$(git grep -n --untracked --fixed-strings -e "$1" -- $common_exempt $2 2>/dev/null); then
    report "$1" "$found"
  fi
}

# Module-level progress state replaced by durable Jobs and Job events.
# `processingStatus` is also YouTube's own upload field, named once in its publisher test.
sweep_fixed 'processing_status' ':!backend/tests/contract/test_youtube_publisher.py'

# A thread as the job runner, replaced by Celery. Two modules use threads for the narrow job
# of draining a subprocess's pipes within a bound, which is not job execution.
sweep_fixed 'threading.Thread' ':!backend/src/clipah/assets/ffmpeg.py
:!backend/src/clipah/source_connectors/yt_dlp_adapter.py'

# One shared working file in the process's current directory, replaced by per-Job `0700`
# workspaces. The workspace test uses the old name on purpose, to prove two concurrent Jobs
# can hold the same relative filename without colliding.
sweep_fixed 'main_video.mp4' ':!backend/tests/unit/test_workspace.py'

# A cookie jar sitting in the repository root and handed to a downloader. The rebuilt
# authenticated connector, which Task 27 specified and a feature flag gates, writes a leased
# jar `0600` inside the Job's own workspace, redacts its path from diagnostics, and removes
# it on every exit. Those files are named here; nothing else may write one.
sweep_fixed 'cookies.txt' ':!.gitignore
:!backend/src/clipah/source_connectors/authenticated_youtube.py
:!backend/tests/integration/test_source_connections.py
:!frontend/tests/youtube-connection.test.tsx'

# No exemptions: nothing in the rebuilt stack disables certificate verification, and nothing
# assigns markup as a string.
sweep_fixed 'nocheckcertificate' ''
sweep_fixed 'innerHTML' ''

# The legacy application routes, matched as whole quoted route strings so an unrelated path
# segment elsewhere is not mistaken for one.
for route in process status download reset; do
  # shellcheck disable=SC2086
  if found=$(git grep -n --untracked --extended-regexp \
    -e "@app\.route\(['\"]/$route" \
    -e "['\"]/$route['\"]" \
    -- $common_exempt 2>/dev/null); then
    report "/$route" "$found"
  fi
done

if [ "$failed" -ne 0 ]; then
  echo 'the legacy stack is not fully removed' >&2
  exit 1
fi

echo 'no legacy behaviour found'
