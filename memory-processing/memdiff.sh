#!/bin/sh
# Do not publish incomplete/failed plugin output as a reusable cache entry.
set -eu

if [ -z "${FROM_MEMORY_IMAGE_FILENAME:-}" ] || [ -z "${TO_MEMORY_IMAGE_FILENAME:-}" ]; then
    echo "Both memory image filenames are required" >&2
    exit 1
fi

RUN_NAME=${VMDIFF_MEMORY_RUN_ID:-}
if [ -z "$RUN_NAME" ]; then
    RUN_NAME=$(python3 - <<'PY'
import hashlib, json, os
source = os.environ.get('VMDIFF_SOURCE_ID') or os.path.abspath(os.environ.get('SNAPSHOT_DIR', '/snapshots'))
options = {'source': source, 'from': os.environ['FROM_MEMORY_IMAGE_FILENAME'], 'to': os.environ['TO_MEMORY_IMAGE_FILENAME']}
print('memory-v2-' + hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()[:24])
PY
)
fi
case "$RUN_NAME" in *[!a-zA-Z0-9_-]*|'') echo "Invalid memory cache ID" >&2; exit 1;; esac
RUN_DIR="${RESULTS_DIR:-/results}/memory/$RUN_NAME"
mkdir -p "$RUN_DIR"
TEMP_OUTPUT=''
trap 'if [ -n "$TEMP_OUTPUT" ]; then rm -f "$TEMP_OUTPUT"; fi' EXIT HUP INT TERM

valid_json() {
    python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); sys.exit(0 if isinstance(data,list) else 1)' "$1"
}

extract() {
    image=$1
    output=$2
    plugin=$3
    case "$image" in */*|*\\*) echo "Memory images must be directly inside SNAPSHOT_DIR" >&2; exit 1;; esac
    case "${USE_CACHE:-False}" in
        True|true) if [ -s "$output" ] && valid_json "$output"; then return; fi;;
    esac
    # Remove a prior success before reprocessing; failures must not look current.
    rm -f "$output"
    TEMP_OUTPUT=$(mktemp "$output.tmp.XXXXXX")
    volatility3 --cache-path ./volatilitycache -o . -f "${SNAPSHOT_DIR:-/snapshots}/$image" --renderer json "$plugin" > "$TEMP_OUTPUT"
    valid_json "$TEMP_OUTPUT"
    cat "$TEMP_OUTPUT"
    mv "$TEMP_OUTPUT" "$output"
    TEMP_OUTPUT=''
}

for plugin in ${MEMORY_PLUGINS:?MEMORY_PLUGINS is required}; do
    case "$plugin" in *[!a-zA-Z0-9_.]*|'') echo "Invalid plugin name" >&2; exit 1;; esac
    extract "$FROM_MEMORY_IMAGE_FILENAME" "$RUN_DIR/from-$plugin.json" "$plugin"
    extract "$TO_MEMORY_IMAGE_FILENAME" "$RUN_DIR/to-$plugin.json" "$plugin"
done
