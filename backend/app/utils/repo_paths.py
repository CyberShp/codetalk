import os
from pathlib import Path
from typing import Optional


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

    return str((Path(tool_base_path) / relative).resolve())
