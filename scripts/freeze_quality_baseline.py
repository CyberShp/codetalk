#!/usr/bin/env python3
"""Freeze a reviewed F012 baseline from retained benchmark runs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.quality_baseline_freezer import main


if __name__ == "__main__":
    raise SystemExit(main())
