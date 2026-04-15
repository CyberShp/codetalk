"""Resolve repository source to a local path accessible by tool containers."""

import asyncio
import os
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.models.repository import Repository

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
                f"(Docker volume boundary). Got: {path}"
            )
        return str(resolved)

    if repo.source_type == "git_url":
        return await _clone_or_pull(repo)

    if repo.source_type == "zip_upload":
        if not repo.local_path or not os.path.isdir(repo.local_path):
            raise FileNotFoundError(f"Uploaded repo path missing: {repo.local_path}")
        return repo.local_path

    raise ValueError(f"Unknown source type: {repo.source_type}")


async def _clone_or_pull(repo: Repository) -> str:
    # Use repo UUID to avoid name collisions across projects
    dest = os.path.join(settings.repos_base_path, str(repo.id))
    if os.path.isdir(os.path.join(dest, ".git")):
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", dest, "pull", "--ff-only",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _running_syncs[repo.id] = proc
        try:
            _, stderr = await proc.communicate()
        finally:
            _running_syncs.pop(repo.id, None)
        if proc.returncode != 0:
            raise RuntimeError(
                f"git pull failed for {repo.name}: {stderr.decode()}"
            )
        return dest

    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth=1", "-b", repo.branch,
        repo.source_uri, dest,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _running_syncs[repo.id] = proc
    try:
        _, stderr = await proc.communicate()
    finally:
        _running_syncs.pop(repo.id, None)
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed for {repo.name}: {stderr.decode()}")
    return dest
