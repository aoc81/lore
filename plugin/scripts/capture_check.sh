#!/bin/sh
# Resolve a Python 3 interpreter across OSes and run capture_check.py with stdin
# intact. Exits 0 silently if no Python is found, so the hook never blocks.
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY=$(command -v python3 || command -v python || command -v py)
[ -n "$PY" ] || exit 0
exec "$PY" "$DIR/capture_check.py" "$@"
