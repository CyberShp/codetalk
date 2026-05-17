"""FastAPI deployment wizard server -- serves UI and SSE deployment events."""

import asyncio
import json
import threading
import uuid
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

import checks as checks_module
import config_store
from deployers.compose import ComposeDeployer
from deployers.k8s import K8sDeployer
from deployers.native import NativeDeployer

STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# Module-level deployment state
# ---------------------------------------------------------------------------
_deploy_state: dict = {
    "job_id": None,
    "running": False,
    "deployer": None,
    "event_queue": None,
}

app = FastAPI(title="CodeTalk Deployer", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:9000",
        "http://127.0.0.1:9000",
        "http://localhost:3000",
        "http://localhost:3005",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/checks")
async def api_checks(mode: str = "compose"):
    """Run prerequisite checks for the given deployment mode."""
    results = await checks_module.run_checks(mode)
    return {"checks": results}


@app.get("/api/config")
async def api_get_config():
    """Return the currently saved deployment config (camelCase for frontend)."""
    return config_store.load_config_for_frontend()


@app.post("/api/config")
async def api_save_config(config: dict):
    """Persist deployment config to disk."""
    config_store.save_config(config)
    return {"ok": True}


@app.post("/api/deploy")
async def api_deploy(body: dict):
    """Start a deployment in a background thread and return a job_id."""
    if _deploy_state["running"]:
        raise HTTPException(status_code=409, detail="A deployment is already running")

    cfg = config_store.load_config()
    cfg.update(body)

    job_id = str(uuid.uuid4())
    event_queue: asyncio.Queue = asyncio.Queue()
    mode = cfg.get("mode", "compose")

    if mode == "native":
        deployer = NativeDeployer(cfg, event_queue)
    elif mode == "k8s":
        deployer = K8sDeployer(cfg, event_queue)
    else:
        deployer = ComposeDeployer(cfg, event_queue)

    _deploy_state.update({
        "job_id": job_id,
        "running": True,
        "deployer": deployer,
        "event_queue": event_queue,
    })

    loop = asyncio.get_event_loop()

    def _run_in_thread():
        future = asyncio.run_coroutine_threadsafe(_run_deployment(deployer), loop)
        future.result()

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()

    return {"job_id": job_id}


async def _run_deployment(deployer) -> None:
    error_occurred = False
    try:
        await deployer.deploy()
    except Exception:
        error_occurred = True
    finally:
        _deploy_state["running"] = False
        q = _deploy_state.get("event_queue")
        if q is not None:
            if error_occurred:
                await q.put({"step": "done", "status": "error", "message": "Deployment failed"})
            else:
                await q.put({"step": "done", "status": "done", "message": "Deployment complete"})
            await q.put(None)  # sentinel -- signals SSE stream end (no event injected)


@app.get("/api/deploy/stream")
async def api_deploy_stream():
    """SSE endpoint that streams deployment progress events."""

    async def event_generator() -> AsyncGenerator[str, None]:
        queue = _deploy_state.get("event_queue")
        if queue is None:
            yield "data: {}\n\n"
            return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                break  # stream closed; done/error already sent by _run_deployment
            yield "data: " + json.dumps(event) + "\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/deploy/stop")
async def api_deploy_stop():
    """Cancel the currently running deployment."""
    deployer = _deploy_state.get("deployer")
    if deployer is None or not _deploy_state["running"]:
        return {"ok": True, "message": "No deployment running"}
    await deployer.stop()
    _deploy_state["running"] = False
    return {"ok": True, "message": "Deployment stopped"}


@app.post("/api/deploy/supplement/deepwiki")
async def api_supplement_deepwiki(body: dict):
    """Install DeepWiki-Open as a supplementary service after native deployment."""
    if _deploy_state["running"]:
        raise HTTPException(status_code=409, detail="A deployment is already running")

    deepwiki_path = body.get("deepwikiPath", "").strip()
    if not deepwiki_path:
        raise HTTPException(status_code=400, detail="deepwikiPath is required")

    cfg = config_store.load_config()
    cfg["deepwiki_path"] = deepwiki_path

    event_queue: asyncio.Queue = asyncio.Queue()
    deployer = NativeDeployer(cfg, event_queue)
    old_deployer = _deploy_state.get("deployer")
    if old_deployer is not None and hasattr(old_deployer, "_processes"):
        deployer._processes.update(old_deployer._processes)

    _deploy_state.update({
        "job_id": str(uuid.uuid4()),
        "running": True,
        "deployer": deployer,
        "event_queue": event_queue,
    })

    loop = asyncio.get_event_loop()

    def _run_in_thread():
        future = asyncio.run_coroutine_threadsafe(_run_supplement(deployer, deepwiki_path, cfg), loop)
        future.result()

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()

    return {"job_id": _deploy_state["job_id"]}


async def _run_supplement(deployer: NativeDeployer, deepwiki_path: str, cfg: dict) -> None:
    try:
        await deployer.supplement_deepwiki(deepwiki_path)
        cfg["deepwiki_path"] = deepwiki_path
        config_store.save_config(cfg)
    except Exception as exc:
        q = _deploy_state.get("event_queue")
        if q is not None:
            await q.put({"step": "deepwiki_install", "status": "error", "message": str(exc), "progress": {"current": 0, "total": 5}})
    finally:
        _deploy_state["running"] = False
        q = _deploy_state.get("event_queue")
        if q is not None:
            await q.put(None)


@app.get("/api/services/health")
async def api_services_health():
    """Check health of all deployed services."""
    deployer = _deploy_state.get("deployer")
    if deployer is None:
        cfg = config_store.load_config()
        mode = cfg.get("mode", "compose")
        queue: asyncio.Queue = asyncio.Queue()
        if mode == "native":
            deployer = NativeDeployer(cfg, queue)
        elif mode == "k8s":
            deployer = K8sDeployer(cfg, queue)
        else:
            deployer = ComposeDeployer(cfg, queue)
    results = await deployer.check_health()
    return {"services": results}


# ---------------------------------------------------------------------------
# Static files (must be AFTER API routes to avoid shadowing /api/* paths)
# ---------------------------------------------------------------------------
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    print("Deployer running at http://localhost:9000")
    uvicorn.run("server:app", host="0.0.0.0", port=9000, reload=False)
