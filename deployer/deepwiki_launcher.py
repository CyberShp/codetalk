"""
Codetalk native-deployer launcher for DeepWiki.

Patches tiktoken.load.read_file so BPE files are served from the local
vendor cache without requiring an internet connection.  This is needed
because the tiktoken version bundled with DeepWiki has no file-cache
mechanism and issues an HTTPS GET for every encoding it initialises.

Also applies a small FastAPI compatibility shim: newer FastAPI releases
removed ``FastAPI.add_websocket_route`` (which deepwiki-open's api.py
still calls).  We re-add it as a thin alias of ``add_api_websocket_route``
so the upstream code keeps working without patching the third-party repo.

Run by codetalk's NativeDeployer in place of `python -m uvicorn api.api:app`.
"""
import os
import sys

_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "tiktoken_cache")

_LOCAL_BPE: dict[str, str] = {
    "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken": os.path.join(
        _VENDOR_DIR, "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
    ),
}


def _patch_tiktoken() -> None:
    try:
        import tiktoken.load as _tl

        _orig = _tl.read_file

        def _read_file(blobpath: str) -> bytes:
            local = _LOCAL_BPE.get(blobpath)
            if local and os.path.exists(local):
                with open(local, "rb") as f:
                    return f.read()
            return _orig(blobpath)

        _tl.read_file = _read_file
    except Exception:
        pass  # best-effort; uvicorn will start even if patch fails


def _patch_fastapi_websocket() -> None:
    """Restore ``FastAPI.add_websocket_route`` for compatibility with deepwiki-open.

    deepwiki-open's ``api/api.py`` calls ``app.add_websocket_route("/ws/chat", ...)``
    but modern FastAPI only exposes ``add_api_websocket_route`` on the ``FastAPI``
    class (the lower-level ``add_websocket_route`` lives on Starlette's ``Router``
    and is not surfaced as an attribute of the ``FastAPI`` app).  Aliasing it
    here keeps the upstream code working without modifying the third-party repo.
    """
    try:
        from fastapi import FastAPI

        if not hasattr(FastAPI, "add_websocket_route"):
            def add_websocket_route(self, path, route, name=None):
                return self.add_api_websocket_route(path, route, name=name)

            FastAPI.add_websocket_route = add_websocket_route
    except Exception:
        pass  # best-effort; surface the original error if the import still fails


if __name__ == "__main__":
    # When run as a script, sys.path[0] is the script's directory (deployer/),
    # not cwd (deepwiki-open/). Insert cwd so `import api` resolves correctly.
    sys.path.insert(0, os.getcwd())
    _patch_tiktoken()
    # NOTE: must run before uvicorn imports api.api, which calls
    # ``app.add_websocket_route(...)`` at module top level.
    _patch_fastapi_websocket()

    port = int(os.environ.get("DEEPWIKI_API_PORT", "8001"))

    import uvicorn

    uvicorn.run("api.api:app", host="0.0.0.0", port=port)
