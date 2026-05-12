import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

_WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:[/\\]")


def running_in_container() -> bool:
    return Path("/.dockerenv").exists()


def default_repos_base_path(repo_root: Path, in_container: Optional[bool] = None) -> str:
    if in_container is None:
        in_container = running_in_container()
    if in_container:
        return "/data/repos"
    return str((repo_root / ".repos").resolve())


def ensure_repos_base_path(base_path: str) -> str:
    path = Path(base_path).resolve()
    path.mkdir(parents=True, exist_ok=True)

    if not os.access(path, os.W_OK):
        raise RuntimeError(
            "Repository storage path is not writable: "
            f"{path}. Set REPOS_BASE_PATH to a writable host path."
        )

    return str(path)


def _translate_path(repo_path: str, host_base: str, container_base: str) -> Optional[str]:
    """Try to translate repo_path from host_base to container_base.

    Returns translated container path if repo_path is under host_base, else None.
    """
    if not host_base:
        return None

    # Windows-style host path in DB, translating against a Windows-style host_base
    if _WINDOWS_ABS_RE.match(repo_path) or _WINDOWS_ABS_RE.match(host_base):
        win_repo = PureWindowsPath(repo_path)
        win_base = PureWindowsPath(host_base)
        try:
            relative = win_repo.relative_to(win_base)
            return str(PurePosixPath(container_base) / relative.as_posix())
        except ValueError:
            return None

    resolved_repo = Path(repo_path).expanduser()
    if not resolved_repo.is_absolute():
        resolved_repo = resolved_repo.resolve()

    resolved_base = Path(host_base).expanduser()
    if not resolved_base.is_absolute():
        resolved_base = resolved_base.resolve()

    try:
        relative = resolved_repo.relative_to(resolved_base)
        return str(PurePosixPath(container_base) / PurePosixPath(relative.as_posix()))
    except ValueError:
        return None


def to_tool_repo_path(
    repo_path: str,
    host_base_path: str,
    tool_base_path: str,
    local_host_path: str = "",
    local_container_path: str = "",
) -> str:
    """Translate a host repo path to the path visible inside tool containers.

    Checks two mappings in order:
    1. local_host_path → local_container_path  (user-specified local repos dir)
    2. host_base_path  → tool_base_path        (managed .repos clones dir)

    Returns the first successful translation, or the original path if neither matches.
    """
    # When a Windows-style path is stored in the DB but this code runs in a Linux container,
    # Path() on Linux won't treat "D:\\..." as absolute and resolve() prepends the CWD.
    # PureWindowsPath is filesystem-free and works cross-platform.

    # Try local repos mapping first (user-specified directories outside .repos)
    if local_host_path:
        result = _translate_path(repo_path, local_host_path, local_container_path)
        if result is not None:
            return result

    # Fall back to managed repos mapping (.repos / /data/repos)
    result = _translate_path(repo_path, host_base_path, tool_base_path)
    if result is not None:
        return result

    # No translation matched — return as-is (will produce a 404 in Docker unless mounted)
    return repo_path
