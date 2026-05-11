"""Resolve repository source to a local path accessible by tool containers."""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.models.repository import Repository
from app.utils.repo_paths import ensure_repos_base_path

logger = logging.getLogger(__name__)

_running_syncs: dict[UUID, asyncio.subprocess.Process] = {}


async def cancel_sync(repo_id: UUID) -> bool:
    """Cancel a running sync operation. Returns True if process was found and terminated."""
    proc = _running_syncs.pop(repo_id, None)
    if proc and proc.returncode is None:
        proc.terminate()
        return True
    return False


async def resolve_source(repo: Repository) -> str:
    if repo.source_type == "local_path":
        path = repo.source_uri
        if not os.path.isdir(path):
            raise FileNotFoundError(f"Local path does not exist: {path}")
        resolved = Path(path).resolve()
        boundary = Path(settings.repos_base_path).resolve()
        if not resolved.is_relative_to(boundary):
            raise ValueError(
                f"Local path must be under {settings.repos_base_path} "
                f"(runtime shared repo boundary). Got: {path}"
            )
        return str(resolved)

    if repo.source_type == "git_url":
        return await _clone_or_pull(repo)

    if repo.source_type == "zip_upload":
        if not repo.local_path or not os.path.isdir(repo.local_path):
            raise FileNotFoundError(f"Uploaded repo path missing: {repo.local_path}")
        return repo.local_path

    raise ValueError(f"Unknown source type: {repo.source_type}")


async def _run_git(args: list[str], repo_name: str, repo_id: UUID) -> None:
    timeout = settings.git_sync_timeout_seconds
    logger.debug("git %s for %s (timeout=%ds)", " ".join(args[1:4]), repo_name, timeout)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _running_syncs[repo_id] = proc
    try:
        try:
            _, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout)
            )
        except asyncio.TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                proc.kill()
            raise RuntimeError(
                f"git timed out after {timeout}s for {repo_name}: {' '.join(args[:4])}"
            )
    finally:
        _running_syncs.pop(repo_id, None)

    if proc.returncode != 0:
        stderr_text = stderr_bytes.decode(errors="replace").strip()
        logger.error(
            "git failed for %s (exit %d): %s", repo_name, proc.returncode, stderr_text
        )
        raise RuntimeError(
            f"git {args[1]} failed for {repo_name} (exit {proc.returncode}): {stderr_text}"
        )


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
