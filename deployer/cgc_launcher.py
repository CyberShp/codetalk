"""CGC (CodeGraphContext) launcher utilities for the native deployer.

CGC runs as a separate HTTP daemon: ``cgc api start --host 127.0.0.1 --port <PORT>``.
This module provides path resolution and working-directory helpers used by
deployers/native.py to launch and manage the CGC process.
"""
from __future__ import annotations

import os
import subprocess
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


class CGCInstallError(RuntimeError):
    """CGC or its dependencies could not be installed."""


def ensure_cgc_installed(venv_path: Path | None = None) -> None:
    """Ensure the CGC venv exists with codegraphcontext + mcp installed.

    Idempotent: returns immediately if cgc.exe already exists in the venv.
    Otherwise creates the venv (if absent) and runs pip install for both
    ``codegraphcontext`` and ``mcp`` (which cgc 0.x omits from its own deps).

    Args:
        venv_path: Target venv directory. Defaults to CGC_DEFAULT_VENV.

    Raises:
        CGCInstallError: on venv creation failure or pip install failure.
    """
    venv: Path = venv_path if isinstance(venv_path, Path) else (
        Path(venv_path) if venv_path else CGC_DEFAULT_VENV
    )
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    exe_name = "cgc.exe" if sys.platform == "win32" else "cgc"
    python_exe = venv / scripts / ("python.exe" if sys.platform == "win32" else "python")

    # Use `python -m pip` rather than `pip.exe` — the pip script wrapper can be
    # broken/missing (e.g. after a pip upgrade that replaces the launcher) while
    # the underlying module and Python executable remain fully functional.
    def _pip(*args: str) -> list:
        return [str(python_exe), "-m", "pip", *args]

    if (venv / scripts / exe_name).exists():
        # cgc.exe present — verify mcp is also installed (cgc 0.x omits it)
        chk = subprocess.run(_pip("show", "mcp"), capture_output=True)
        if chk.returncode == 0:
            return  # fully installed
        # mcp missing — install it without reinstalling codegraphcontext
        result = subprocess.run(_pip("install", "mcp"), capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            raise CGCInstallError(
                f"CGC 依赖安装失败 (mcp): {detail}\n"
                f"请手动运行: {python_exe} -m pip install mcp"
            )
        return

    if not python_exe.exists():
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            raise CGCInstallError(
                f"CGC 虚拟环境创建失败 ({venv}): {detail}\n"
                f"请手动运行: python -m venv {venv}"
            )

    for pkg in ("codegraphcontext", "mcp"):
        result = subprocess.run(
            _pip("install", pkg),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            raise CGCInstallError(
                f"CGC 依赖安装失败 ({pkg}): {detail}\n"
                f"请手动运行: {python_exe} -m pip install {pkg}"
            )


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
