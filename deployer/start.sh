#!/usr/bin/env bash
# Note: on Unix/macOS, make this executable once after cloning:
#   chmod +x deployer/start.sh
cd "$(dirname "$0")"

for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' &>/dev/null; then
            exec "$candidate" start.py
        fi
    fi
done

echo "Error: Python 3.10+ is required but no Python executable was found on PATH."
exit 1
