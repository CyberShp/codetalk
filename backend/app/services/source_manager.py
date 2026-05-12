"""Resolve repository source to a local path accessible by tool containers."""

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.models.repository import Repository
from app.utils.repo_paths import _translate_path, ensure_repos_base_path, running_in_container

logger = logging.getLogger(__name__)

_running_pids: dict[UUID, int] = {}


async def cancel_sync(repo_id: UUID) -> bool:
    """Cancel a running sync operation. Returns True if process was found and terminated."""
    pid = _running_pids.pop(repo_id, None)
    if pid is None:
        return False
    try:
        os.kill(pid, 15)
        return True
    except OSError:
        return False


async def resolve_source(repo: Repository) -> str:
    if repo.source_type == "local_path":
        path = repo.source_uri

        if running_in_container():
            translated = _translate_path(
                path,
                settings.local_repos_host_path,
                settings.local_repos_container_path,
            )
            if translated and os.path.isdir(translated):
                return translated
            hint = ""
            if not settings.local_repos_host_path:
                hint = (
                    " Set LOCAL_REPOS_HOST_PATH to the parent directory "
                    "containing your local repos and restart the containers."
                )
            raise FileNotFoundError(
                f"Local path not reachable: {path}.{hint}"
            )

        if not os.path.isdir(path):
            raise FileNotFoundError(f"Local path does not exist: {path}")
        return str(Path(path).resolve())

    if repo.source_type == "git_url":
        return await _clone_or_pull(repo)

    if repo.source_type == "zip_upload":
        if not repo.local_path or not os.path.isdir(repo.local_path):
            raise FileNotFoundError(f"Uploaded repo path missing: {repo.local_path}")
        return repo.local_path

    raise ValueError(f"Unknown source type: {repo.source_type}")


def _run_git_sync(args: list[str], repo_name: str, timeout: int) -> None:
    """Run a git command synchronously (called via asyncio.to_thread)."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"git timed out after {timeout}s for {repo_name}: {' '.join(args[:4])}"
        )
    if result.returncode != 0:
        stderr_text = result.stderr.decode(errors="replace").strip()
        logger.error(
            "git failed for %s (exit %d): %s", repo_name, result.returncode, stderr_text
        )
        raise RuntimeError(
            f"git {args[1]} failed for {repo_name} (exit {result.returncode}): {stderr_text}"
        )


async def _run_git(args: list[str], repo_name: str, repo_id: UUID) -> None:
    timeout = settings.git_sync_timeout_seconds
    logger.debug("git %s for %s (timeout=%ds)", " ".join(args[1:4]), repo_name, timeout)
    await asyncio.to_thread(_run_git_sync, args, repo_name, timeout)


async def _clone_or_pull(repo: Repository) -> str:
    base_path = ensure_repos_base_path(settings.repos_base_path)
    dest = os.path.join(base_path, str(repo.id))

    if os.path.isdir(os.path.join(dest, ".git")):
        try:
            await _run_git(
                ["git", "-C", dest, "pull", "--ff-only"],
                repo.name,
                repo.id,
            )
        except RuntimeError as exc:
            if "timed out" in str(exc):
                raise
            logger.warning(
                "ff-only pull failed for %s; retrying with fetch --depth=1 + reset",
                repo.name,
            )
            await _run_git(
                ["git", "-C", dest, "fetch", "--depth=1", "origin", repo.branch],
                repo.name,
                repo.id,
            )
            await _run_git(
                ["git", "-C", dest, "reset", "--hard", f"origin/{repo.branch}"],
                repo.name,
                repo.id,
            )
        return dest

    # If dest exists but is not a valid git repo (e.g. partial clone from
    # a previous crash), remove it so git clone can succeed.
    if os.path.isdir(dest):
        logger.warning(
            "Removing stale non-git directory for %s at %s", repo.name, dest
        )
        shutil.rmtree(dest)

    await _run_git(
        ["git", "clone", "--depth=1", "-b", repo.branch, repo.source_uri, dest],
        repo.name,
        repo.id,
    )
    return dest
