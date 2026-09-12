#!/bin/sh
# Drive one fixture through the legacy Flask stack and record what it produced.
#
#   scripts/legacy-smoke.sh --fixture backend/tests/fixtures/media/landscape.mp4 \
#     --base-url https://clipah-legacy.example
#
# This exists for one purpose: the cutover comparison in `docs/operations/cutover.md`. It
# writes the same observation shape `scripts/new-stack-smoke.sh` writes, so the two can be
# compared field by field. It is the one file in the repository allowed to name the legacy
# `/process`, `/status`, and `/download` routes, and `scripts/check-legacy-removed.sh`
# exempts it by name for that reason.
#
# Without `--base-url` there is no legacy deployment to measure, which is the normal state
# once the rollback window has closed. The run then records `"status": "unmeasured"` and
# exits non-zero, because an absent comparison is a result to write down rather than a pass.
set -eu

base_url=''
fixture=''
output='reports/legacy-smoke.json'
curl_bin=${CLIPAH_CURL_BIN:-curl}

while [ $# -gt 0 ]; do
  case "$1" in
    --fixture) fixture=$2; shift 2 ;;
    --base-url) base_url=$2; shift 2 ;;
    --output) output=$2; shift 2 ;;
    --fixture=*) fixture=${1#*=}; shift ;;
    --base-url=*) base_url=${1#*=}; shift ;;
    --output=*) output=${1#*=}; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

work=$(mktemp -d)
observation_written=0
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

mkdir -p "$(dirname -- "$output")"

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


result = load("status.json", {})
clips = result.get("clips", [])
candidates = []
for index, clip in enumerate(clips, start=1):
    # The legacy stack reports seconds as floats. Milliseconds are the comparable unit.
    start = round(float(clip.get("start_time", 0)) * 1000)
    end = round(float(clip.get("end_time", 0)) * 1000)
    candidates.append(
        {
            "rank": index,
            "startMs": start,
            "endMs": end,
            "durationMs": end - start,
            "title": clip.get("title"),
            "excerpt": clip.get("transcript"),
        }
    )

archive = work / "download.bin"
final_media = "not produced"
if archive.exists() and archive.stat().st_size > 0:
    # The legacy stack delivers one ZIP. Its magic number is the only validity check
    # available without unpacking it, and it is the one the comparison needs.
    final_media = {
        "archiveBytes": archive.stat().st_size,
        "looksLikeZip": archive.read_bytes()[:2] == b"PK",
    }

pathlib.Path(output).write_text(
    json.dumps(
        {
            "stack": "legacy",
            "status": status,
            "fixture": (work / "fixture").read_text().strip() if (work / "fixture").exists() else None,
            "candidates": candidates,
            "subtitles": bool(result.get("subtitles", False)),
            "finalMedia": final_media,
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

if [ -z "$base_url" ]; then
  echo "no --base-url given, so there is no legacy deployment to measure; recording the run as unmeasured" >&2
  write_observation unmeasured
  exit 2
fi

# The legacy stack takes one multipart form and holds progress in process memory.
"$curl_bin" --silent --show-error --fail --request POST "$base_url/process" \
  --form "video_file=@$fixture" \
  --form 'language=en' \
  --form 'generate_subtitles=true' \
  --form 'add_watermark=false' > "$work/process.json"

attempt=0
while [ "$attempt" -lt 240 ]; do
  "$curl_bin" --silent --show-error --fail "$base_url/status" > "$work/status.json"
  case $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status",""))' "$work/status.json") in
    complete) break ;;
    error) echo 'the legacy run reported an error' >&2; exit 1 ;;
  esac
  attempt=$((attempt + 1))
  sleep 5
done

if [ "$attempt" -ge 240 ]; then
  echo 'the legacy run did not finish within the smoke budget' >&2
  exit 1
fi

"$curl_bin" --silent --show-error --fail "$base_url/download" --output "$work/download.bin"

write_observation measured
echo "legacy smoke recorded in $output"
