"""
Codetalk native-deployer launcher for DeepWiki.

Why this exists:
  1. TIKTOKEN: deepwiki-open's bundled tiktoken version fetches BPE files over
     HTTPS at runtime. On intranet PCs that's a 100% reliable way to crash
     deepwiki on first request. We force TIKTOKEN_CACHE_DIR to a directory
     that already has the BPE files committed in this repo, and additionally
     monkey-patch tiktoken.load.read_file as a LOG-ONLY observer so that if
     anything still tries to download (= a missing encoding), we can see
     exactly which URL it wanted and add the corresponding file to the cache.

  2. FASTAPI COMPAT: deepwiki-open's api/api.py calls
     ``app.add_websocket_route(...)`` which modern FastAPI doesn't expose
     anymore (only ``add_api_websocket_route`` is on the FastAPI class).
     We alias them at class level before api.api is imported.

Run by codetalk's NativeDeployer in place of ``python -m uvicorn api.api:app``.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Logging — write to stderr so the deployer captures it in frontend logs.
# Prefix every line so it's greppable.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[deepwiki-launcher %(levelname)s] %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("deepwiki-launcher")

# ---------------------------------------------------------------------------
# Tiktoken cache resolution.
#
# Priority:
#   1. TIKTOKEN_CACHE_DIR env var (set by deployer/deployers/native.py with
#      the result of _best_tiktoken_cache()), if it exists AND has files.
#   2. {deepwiki_cwd}/tiktoken/ — populated by _stage_tiktoken_into_deepwiki().
#   3. Hard-coded candidate paths relative to this script, in case the
#      launcher is run standalone (e.g. for debugging) without env setup.
#
# KEEP IN SYNC with _TIKTOKEN_CACHE_CANDIDATES in deployer/deployers/native.py.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))               # .../deployer/
_REPO_ROOT = os.path.dirname(_HERE)                              # .../codetalk/
_TIKTOKEN_CACHE_CANDIDATES = [
    os.path.join(_REPO_ROOT, "data", "tiktoken_cache"),
    os.path.join(_REPO_ROOT, "docker", "deepwiki", "tiktoken"),
    os.path.join(_HERE, "vendor", "tiktoken_cache"),
]


def _list_cache_files(path: str) -> list[str]:
    """List regular files in path (no hidden, no dirs). Returns [] on error."""
    try:
        return sorted(
            f for f in os.listdir(path)
            if not f.startswith('.')
            and os.path.isfile(os.path.join(path, f))
        )
    except OSError:
        return []


def _resolve_cache_dir() -> Optional[str]:
    """Find the first cache dir that exists and has at least one BPE file.

    Logs every candidate considered so you can see why a particular one was
    picked (or why none was).
    """
    log.info("Resolving tiktoken cache dir...")

    # 1. Explicit env var
    env_dir = os.environ.get("TIKTOKEN_CACHE_DIR")
    if env_dir:
        files = _list_cache_files(env_dir)
        if files:
            sample = files[:4] + (["..."] if len(files) > 4 else [])
            log.info("  [HIT] env TIKTOKEN_CACHE_DIR=%s — %d file(s): %s",
                     env_dir, len(files), sample)
            return env_dir
        log.warning("  [MISS] env TIKTOKEN_CACHE_DIR=%s — dir empty or missing",
                    env_dir)

    # 2. {cwd}/tiktoken/ — populated by deployer's staging step
    cwd_dir = os.path.join(os.getcwd(), "tiktoken")
    files = _list_cache_files(cwd_dir)
    if files:
        log.info("  [HIT] {cwd}/tiktoken/=%s — %d file(s)", cwd_dir, len(files))
        return cwd_dir
    log.info("  [skip] {cwd}/tiktoken/=%s — empty/missing", cwd_dir)

    # 3. Hard-coded candidates
    for cand in _TIKTOKEN_CACHE_CANDIDATES:
        files = _list_cache_files(cand)
        if files:
            log.info("  [HIT] fallback %s — %d file(s)", cand, len(files))
            return cand
        log.info("  [skip] fallback %s — empty/missing", cand)

    log.error("No tiktoken cache found anywhere. deepwiki will attempt HTTPS "
              "on first encoding load and likely fail on intranet.")
    return None


def _force_set_cache_env(cache_dir: str) -> None:
    """Set TIKTOKEN_CACHE_DIR=cache_dir, OVERRIDING any prior value.

    Using assignment (not setdefault) intentionally: deepwiki may load its own
    .env via python-dotenv with override=True, or one of its modules may set
    a wrong value at import. We win by setting AFTER its loaders but BEFORE
    its first tiktoken call (which happens inside the api.api import chain).
    """
    prev = os.environ.get("TIKTOKEN_CACHE_DIR")
    os.environ["TIKTOKEN_CACHE_DIR"] = cache_dir
    if prev and prev != cache_dir:
        log.info("Overrode TIKTOKEN_CACHE_DIR: was %r, now %r", prev, cache_dir)
    else:
        log.info("Set TIKTOKEN_CACHE_DIR=%s", cache_dir)


def _install_read_file_observer(cache_dir: Optional[str]) -> None:
    """Monkey-patch tiktoken.load.read_file to LOG every download attempt.

    Purpose is purely observational: if tiktoken's cache lookup misses,
    read_file is the function that does the HTTPS GET. By the time we get
    here we already know the cache lookup missed (otherwise read_file
    wouldn't be called). We log:
      - the URL that's about to be fetched
      - the SHA1 of the URL (= cache filename tiktoken expects)
      - whether a same-named file exists in our cache_dir (= cache_dir
        misconfiguration vs. actually missing file)
    so debugging "why is it still downloading" takes 5 seconds not 5 days.
    """
    try:
        import tiktoken.load as _tl
    except Exception as e:
        log.warning("Could not import tiktoken.load to install observer: %s", e)
        return

    _orig_read_file = _tl.read_file

    def _observed_read_file(blobpath: str, *args, **kwargs):
        sha = hashlib.sha1(blobpath.encode()).hexdigest()
        expected_path = (
            os.path.join(cache_dir, sha) if cache_dir else "(no cache_dir set)"
        )
        log.warning(
            "tiktoken cache MISS — about to HTTPS download:\n"
            "  url      = %s\n"
            "  sha1     = %s\n"
            "  expected = %s",
            blobpath, sha, expected_path,
        )
        if cache_dir:
            actually_there = os.path.exists(os.path.join(cache_dir, sha))
            log.warning("  file at expected path exists? %s", actually_there)
            if not actually_there:
                files = _list_cache_files(cache_dir)
                log.warning("  files currently in cache_dir: %s",
                            files if files else "(empty)")
                log.warning(
                    "  >> Fix: download %s on a machine with internet, save it as "
                    "%s, then restart deepwiki.",
                    blobpath, os.path.join(cache_dir, sha),
                )
        return _orig_read_file(blobpath, *args, **kwargs)

    _tl.read_file = _observed_read_file
    log.info("Installed tiktoken.load.read_file observer (logs URL on cache MISS)")


def _patch_fastapi_websocket() -> None:
    """Restore FastAPI.add_websocket_route for deepwiki-open compatibility.

    deepwiki-open's api/api.py calls ``app.add_websocket_route("/ws/chat", ...)``
    but modern FastAPI only exposes ``add_api_websocket_route`` on the
    ``FastAPI`` class. Alias at class level before api.api imports so the
    upstream code keeps working without modifying the third-party repo.
    """
    try:
        from fastapi import FastAPI
    except Exception as e:
        log.warning("Could not import FastAPI for compat patch: %s", e)
        return

    if hasattr(FastAPI, "add_websocket_route"):
        log.info("FastAPI.add_websocket_route already present, no patch needed")
        return

    def add_websocket_route(self, path, route, name=None):
        return self.add_api_websocket_route(path, route, name=name)

    FastAPI.add_websocket_route = add_websocket_route
    log.info("Patched FastAPI.add_websocket_route -> add_api_websocket_route alias")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    log.info("=" * 60)
    log.info("deepwiki_launcher starting")
    log.info("  cwd:        %s", os.getcwd())
    log.info("  script:     %s", __file__)
    log.info("  python:     %s", sys.executable)
    log.info("  py version: %s", sys.version.split()[0])
    log.info("=" * 60)

    # Ensure `import api` resolves from cwd (= deepwiki-open dir), not from
    # this script's dir (= deployer/).
    sys.path.insert(0, os.getcwd())

    # Tiktoken cache wiring (must happen before any tiktoken import).
    cache_dir = _resolve_cache_dir()
    if cache_dir is not None:
        _force_set_cache_env(cache_dir)
    _install_read_file_observer(cache_dir)

    # FastAPI compat shim (must happen before api.api is imported).
    _patch_fastapi_websocket()

    port = int(os.environ.get("DEEPWIKI_API_PORT", "8001"))
    log.info("Starting uvicorn api.api:app on 0.0.0.0:%d", port)

    import uvicorn
    uvicorn.run("api.api:app", host="0.0.0.0", port=port)
