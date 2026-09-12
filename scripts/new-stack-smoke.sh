#!/bin/sh
# Drive one fixture through the rebuilt stack and record what it produced.
#
#   scripts/new-stack-smoke.sh --fixture backend/tests/fixtures/media/landscape.mp4
#   scripts/new-stack-smoke.sh --fixture in.mp4 --base-url https://clipah.example --output out.json
#
# The observation it writes is deliberately the same shape `scripts/legacy-smoke.sh` writes,
# so the cutover comparison is field by field rather than by eye. A run that could not reach
# the stack, or could not authenticate, records `"status": "unmeasured"` and exits non-zero:
# a smoke that did not run has not passed, and the cutover record has to say so.
#
# Authentication comes from the environment, because a session cookie on a command line is a
# session cookie in the shell history:
#
#   CLIPAH_SMOKE_SESSION_COOKIE   the whole Cookie header value for a signed-in Session
#   CLIPAH_SMOKE_CSRF_TOKEN       the double-submit token echoed on every unsafe request
#
# `clipah.dev.seed` mints both for a local run.
set -eu

base_url='http://127.0.0.1:8000'
fixture=''
output='reports/new-stack-smoke.json'
project_name='cutover smoke'
curl_bin=${CLIPAH_CURL_BIN:-curl}

while [ $# -gt 0 ]; do
  case "$1" in
    --fixture) fixture=$2; shift 2 ;;
    --base-url) base_url=$2; shift 2 ;;
    --output) output=$2; shift 2 ;;
    --project-name) project_name=$2; shift 2 ;;
    --fixture=*) fixture=${1#*=}; shift ;;
    --base-url=*) base_url=${1#*=}; shift ;;
    --output=*) output=${1#*=}; shift ;;
    --project-name=*) project_name=${1#*=}; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

work=$(mktemp -d)
observation_written=0
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

mkdir -p "$(dirname -- "$output")"

# Write the observation and remember that it exists, so the failure trap never overwrites a
# real result with an apology.
write_observation() {
  python3 - "$output" "$1" "$work" <<'PYTHON'
import json
import pathlib
import sys

output, status, work = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])


def load(name, default):
    path = work / name
    if not path.exists() or not path.read_text().strip():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


candidates = [
    {
        "rank": item.get("rank"),
        "startMs": item.get("startMs"),
        "endMs": item.get("endMs"),
        "durationMs": (item.get("endMs") or 0) - (item.get("startMs") or 0),
        "title": item.get("hook"),
        "excerpt": item.get("excerpt"),
    }
    for item in load("candidates.json", {}).get("items", [])
]
renders = load("renders.json", {}).get("items", [])

pathlib.Path(output).write_text(
    json.dumps(
        {
            "stack": "new",
            "status": status,
            "fixture": (work / "fixture").read_text().strip() if (work / "fixture").exists() else None,
            "candidates": candidates,
            # Captions come from the same transcript that produced the candidate, so an
            # excerpt is the evidence that subtitle text exists for it.
            "subtitles": bool(candidates) and all(candidate["excerpt"] for candidate in candidates),
            "finalMedia": [
                {"id": render.get("id"), "durationMs": render.get("durationMs")}
                for render in renders
            ]
            or "not rendered",
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PYTHON
  observation_written=1
}

on_failure() {
  status=$?
  if [ "$observation_written" -eq 0 ]; then
    write_observation unmeasured || true
  fi
  cleanup
  exit "$status"
}
trap on_failure EXIT

if [ -z "$fixture" ] || [ ! -r "$fixture" ]; then
  echo "a readable --fixture is required" >&2
  exit 2
fi
printf '%s\n' "$fixture" > "$work/fixture"

if [ -z "${CLIPAH_SMOKE_SESSION_COOKIE:-}" ] || [ -z "${CLIPAH_SMOKE_CSRF_TOKEN:-}" ]; then
  echo "CLIPAH_SMOKE_SESSION_COOKIE and CLIPAH_SMOKE_CSRF_TOKEN are required; recording the run as unmeasured" >&2
  write_observation unmeasured
  exit 2
fi

api_post() {
  "$curl_bin" --silent --show-error --fail --request POST "$base_url$1" \
    --header 'Content-Type: application/json' \
    --header "X-CSRF-Token: $CLIPAH_SMOKE_CSRF_TOKEN" \
    --header "Cookie: $CLIPAH_SMOKE_SESSION_COOKIE" \
    --header "Origin: $base_url" \
    --data "$2"
}

api_get() {
  "$curl_bin" --silent --show-error --fail "$base_url$1" \
    --header "Cookie: $CLIPAH_SMOKE_SESSION_COOKIE"
}

read_field() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], "") or "")' "$1" "$2"
}

# One Project, one upload, one analysis. Every write carries an idempotency key derived from
# the fixture path, so re-running the smoke against the same stack converges on one Project
# rather than filling a Workspace with near-identical ones.
api_post '/api/v1/projects' "{\"name\": \"$project_name\"}" > "$work/project.json"
project_id=$(read_field "$work/project.json" id)

api_post "/api/v1/projects/$project_id/uploads" \
  "{\"filename\": \"$(basename -- "$fixture")\", \"parts\": 1}" > "$work/upload.json"
upload_id=$(read_field "$work/upload.json" id)
part_url=$(read_field "$work/upload.json" url)

"$curl_bin" --silent --show-error --fail --request PUT "$part_url" \
  --upload-file "$fixture" > "$work/part.txt"

api_post "/api/v1/projects/$project_id/uploads/$upload_id/complete" '{}' > "$work/complete.json"
ingest_job=$(read_field "$work/complete.json" jobId)

await_job() {
  attempt=0
  while [ "$attempt" -lt 240 ]; do
    api_get "/api/v1/jobs/$1" > "$work/job.json"
    case $(read_field "$work/job.json" status) in
      succeeded) return 0 ;;
      failed|canceled) echo "job $1 ended as $(read_field "$work/job.json" status)" >&2; return 1 ;;
    esac
    attempt=$((attempt + 1))
    sleep 5
  done
  echo "job $1 did not finish within the smoke's budget" >&2
  return 1
}

await_job "$ingest_job"

api_post "/api/v1/projects/$project_id/analyses" '{}' > "$work/analysis.json"
await_job "$(read_field "$work/analysis.json" jobId)"

api_get "/api/v1/projects/$project_id/candidates" > "$work/candidates.json"
# A Project is reviewable before anything is rendered, so an empty render list is the normal
# result here rather than a failure. The comparison records it as an intentional difference.
api_get "/api/v1/projects/$project_id/renders" > "$work/renders.json"

write_observation measured
echo "new-stack smoke recorded in $output"
