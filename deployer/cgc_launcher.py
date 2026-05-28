"""CGC (CodeGraphContext) launcher utilities for the native deployer.

CGC runs as a separate HTTP daemon: ``cgc api start --host 127.0.0.1 --port <PORT>``.
This module provides path resolution and working-directory helpers used by
deployers/native.py to launch and manage the CGC process.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# D:\coworkers\ (parent of the codetalk repo)
# cgc_launcher.py lives at deployer/cgc_launcher.py → 3 levels up = D:\coworkers\
_COWORKERS: Path = Path(__file__).parent.parent.parent

CGC_DEFAULT_VENV: Path = _COWORKERS / "cgc-venv"
CGC_DEFAULT_PORT: int = 7072


def resolve_cgc_exe(config: dict | None = None) -> str | None:
    """Return the absolute path to the cgc executable, or None if not found.

    Priority:
      1. config['cgc_venv_path'] when set and non-empty
      2. CGC_DEFAULT_VENV  (D:\\coworkers\\cgc-venv after rename)
      3. D:\\coworkers\\cgc-venv-throwaway  (legacy pre-rename name)
    """
    candidates: list[Path] = []
    if config:
        venv_path = str(config.get("cgc_venv_path", "")).strip()
        if venv_path:
            candidates.append(Path(venv_path))
    candidates.append(CGC_DEFAULT_VENV)
    candidates.append(_COWORKERS / "cgc-venv-throwaway")

    scripts = "Scripts" if sys.platform == "win32" else "bin"
    exe_name = "cgc.exe" if sys.platform == "win32" else "cgc"

    for venv in candidates:
        exe = venv / scripts / exe_name
        if exe.exists():
            return str(exe)
    return None


def cgc_cwd() -> str:
    """Return the working directory for the cgc process.

    We use ~/.codegraphcontext/ rather than the codetalk project directory
    to avoid a GBK codec error: when cgc's cwd contains a .env file with
    non-ASCII characters (e.g. Chinese comments), cgc's own .env loading
    fails on Windows because the system locale defaults to GBK.
    """
    cwd = Path(os.path.expanduser("~")) / ".codegraphcontext"
    cwd.mkdir(parents=True, exist_ok=True)
    return str(cwd)
