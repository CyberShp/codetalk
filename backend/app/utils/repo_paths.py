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


def to_tool_repo_path(
    repo_path: str,
    host_base_path: str,
    tool_base_path: str,
) -> str:
    # When a Windows-style path is stored in the DB but this code runs in a Linux container,
    # Path() on Linux won't treat "D:\..." as absolute and resolve() prepends the CWD.
    # PureWindowsPath is filesystem-free and works cross-platform.
    if _WINDOWS_ABS_RE.match(repo_path):
        win_repo = PureWindowsPath(repo_path)
        win_base = PureWindowsPath(host_base_path)
        try:
            relative = win_repo.relative_to(win_base)
            return str(PurePosixPath(tool_base_path) / relative.as_posix())
        except ValueError:
            return repo_path

    resolved_repo = Path(repo_path).expanduser()
    if not resolved_repo.is_absolute():
        resolved_repo = resolved_repo.resolve()

    resolved_host_base = Path(host_base_path).expanduser()
    if not resolved_host_base.is_absolute():
        resolved_host_base = resolved_host_base.resolve()

    try:
        relative = resolved_repo.relative_to(resolved_host_base)
    except ValueError:
        return str(resolved_repo)

    return str(PurePosixPath(tool_base_path) / PurePosixPath(relative.as_posix()))
