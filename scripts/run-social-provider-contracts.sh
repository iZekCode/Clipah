#!/bin/sh
# Run the provider contract suites that gate every social publishing rollout step.
#
#   scripts/run-social-provider-contracts.sh [--adapter=fake|sandbox] [--provider=NAME]
#
# The fake adapter drives each provider adapter against a recorded HTTP transport, so it
# runs anywhere and is the gate every change has to pass. The sandbox adapter talks to a
# provider's own test environment and needs real credentials in the environment; it fails
# closed rather than silently degrading to the fake one, because sandbox evidence is what
# an app review asks for and a fake run is not that evidence.
set -eu

adapter=fake
provider=all

for argument in "$@"; do
    case "$argument" in
        --adapter=*) adapter=${argument#--adapter=} ;;
        --provider=*) provider=${argument#--provider=} ;;
        -h | --help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *)
            echo "unknown argument: $argument" >&2
            exit 2
            ;;
    esac
done

case "$adapter" in
    fake | sandbox) ;;
    *)
        echo "adapter must be fake or sandbox" >&2
        exit 2
        ;;
esac

case "$provider" in
    all) suites="tests/contract/test_youtube_publisher.py tests/contract/test_instagram_publisher.py tests/contract/test_tiktok_publisher.py" ;;
    youtube) suites="tests/contract/test_youtube_publisher.py" ;;
    instagram) suites="tests/contract/test_instagram_publisher.py" ;;
    tiktok) suites="tests/contract/test_tiktok_publisher.py" ;;
    *)
        echo "provider must be all, youtube, instagram, or tiktok" >&2
        exit 2
        ;;
esac

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root/backend"

if [ "$adapter" = sandbox ]; then
    missing=
    for name in CLIPAH_YOUTUBE_SANDBOX_TOKEN CLIPAH_INSTAGRAM_SANDBOX_TOKEN CLIPAH_TIKTOK_SANDBOX_TOKEN; do
        eval "value=\${$name:-}"
        [ -n "$value" ] || missing="$missing $name"
    done
    if [ -n "$missing" ]; then
        echo "sandbox contracts need provider credentials:$missing" >&2
        exit 1
    fi
    # The sandbox suites are the same contracts pointed at each provider's test
    # environment, and they are marked slow because they leave the machine.
    exec uv run pytest -q -m slow $suites
fi

exec uv run pytest -q $suites
