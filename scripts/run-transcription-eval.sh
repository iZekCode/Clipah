#!/bin/sh
# Run the versioned transcription evaluation over the checked-in labeled cases.
#
#   scripts/run-transcription-eval.sh --adapter=fake
#   scripts/run-transcription-eval.sh --adapter=assemblyai --audio-dir=/path/to/audio \
#       --output=reports/transcription-assemblyai.json
#
# The fake adapter replays the labels offline and must always pass. Live AssemblyAI, Deepgram,
# and WhisperX runs each need an explicit credential or runtime and a local audio directory,
# because evaluation audio is deliberately not checked into the repository.
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root/backend"

exec uv run python -m evals.transcription.runner "$@"
