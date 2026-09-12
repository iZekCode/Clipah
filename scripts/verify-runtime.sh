#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
compose_file="$repository_root/infra/compose.yaml"
docker_bin=${CLIPAH_DOCKER_BIN:-docker}

show_failure() {
  echo 'runtime verification failed; bounded service state follows' >&2
  "$docker_bin" compose -f "$compose_file" ps >&2 || true
  "$docker_bin" compose -f "$compose_file" logs --no-color --tail 80 >&2 || true
}

if ! "$docker_bin" compose -f "$compose_file" up --build --detach --wait --wait-timeout 300; then
  show_failure
  exit 1
fi

if ! "$docker_bin" compose -f "$compose_file" --profile smoke build runtime-smoke; then
  show_failure
  exit 1
fi

for pass in 1 2; do
  echo "runtime smoke pass $pass/2"
  if ! "$docker_bin" compose -f "$compose_file" --profile smoke run --rm --no-deps runtime-smoke; then
    show_failure
    exit 1
  fi
done

echo 'runtime verification ready'
