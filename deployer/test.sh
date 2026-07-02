#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "${PYTHON:-}" ]; then
    echo "Error: Python 3.10+ is required but no compatible Python executable was found on PATH." >&2
    exit 1
fi

"$PYTHON" - <<'PY'
import start

start._ensure_venv_compatible()
start._install_dependencies()
PY

exec ".venv/bin/python" -m pytest "$@"
