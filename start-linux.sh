#!/bin/sh
# CAPCAST launcher (Linux). Runs the local server from this folder.
cd "$(dirname "$0")" || exit 1
PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3 is required (3.11 or newer recommended): https://www.python.org/downloads/"
    exit 127
fi
exec "$PY" -I server.py "$@"
