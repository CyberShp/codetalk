#!/usr/bin/env bash
# Note: on Unix/macOS, make this executable once after cloning:
#   chmod +x deployer/start.sh
cd "$(dirname "$0")"

if command -v python3 &>/dev/null; then
    python3 start.py
elif command -v python &>/dev/null; then
    python start.py
else
    echo "Error: Python 3.10+ is required but neither 'python3' nor 'python' was found on PATH."
    exit 1
fi
