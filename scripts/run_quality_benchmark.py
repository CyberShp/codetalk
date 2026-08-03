#!/usr/bin/env python3
"""Run the bounded F012 benchmark-report persistence CLI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.quality_benchmark_runner import main


if __name__ == "__main__":
    raise SystemExit(main())
