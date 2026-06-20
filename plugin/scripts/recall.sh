#!/bin/sh
# Resolve a Python 3 interpreter across OSes (mac/Linux: python3; Windows: python/py)
# and run recall.py with stdin intact. Exits 0 silently if no Python is found, so
# the hook never blocks prompt submission.
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY=$(command -v python3 || command -v python || command -v py)
[ -n "$PY" ] || exit 0
exec "$PY" "$DIR/recall.py" "$@"
