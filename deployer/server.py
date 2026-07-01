"""FastAPI deployment wizard server -- serves UI and SSE deployment events."""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

import checks as checks_module
import config_store
from deployers.native import NativeDeployer

STATIC_DIR = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).parent.parent
REMOVED_DEEPWIKI_BYTECODE_PREFIXES = {
    "deepwiki",
    "deepwiki_pages",
    "repo_wiki",
    "wiki",
    "wiki_artifacts",
    "wiki_cache_meta",
    "wiki_orchestrator",
    "wiki_prompts",
}

# ---------------------------------------------------------------------------
# Module-level deployment state
# ---------------------------------------------------------------------------

@dataclass
class DeploymentState:
    job_id: str | None = None
    running: bool = False
    deployer: object = None
    event_queue: asyncio.Queue | None = None
    task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_state = DeploymentState()
KNOWN_SERVICES = ("backend", "frontend", "gitnexus", "cgc")


def purge_removed_deepwiki_bytecode(root: Path = PROJECT_ROOT) -> list[Path]:
    """Delete stale bytecode for removed DeepWiki modules from deploy/start runtimes."""
    removed: list[Path] = []
    app_root = root / "backend" / "app"
    if not app_root.exists():
        return removed
    for path in app_root.rglob("*.pyc"):
        stem = path.name.split(".", 1)[0].lower()
        if stem not in REMOVED_DEEPWIKI_BYTECODE_PREFIXES:
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    return removed


purge_removed_deepwiki_bytecode()


def _enabled_service_ports(cfg: dict) -> list[int]:
    """Return ports whose conflicts block the core deployment.

    CGC is an optional enhancer and is already started inside a best-effort
    branch. Its port conflict should degrade CGC only, not prevent
    backend/frontend from starting.
    """
    ports = [
        int(cfg.get("backend_port", 3004)),
        int(cfg.get("frontend_port", 3003)),
    ]
    if cfg.get("install_gitnexus", True):
        ports.append(int(cfg.get("gitnexus_port", 7100)))
    return ports


def _launch_job(coro) -> str:
    """Start a deployment coroutine as an asyncio task and return the job_id."""
    job_id = str(uuid.uuid4())
    _state.job_id = job_id
    _state.running = True
    _state.task = asyncio.create_task(coro)
    return job_id

app = FastAPI(title="CodeTalk Deployer", version="1.0.0")


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Prevent browsers from caching the deployer shell and stale startup JS."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        ct = response.headers.get("content-type", "")
        if (
            "text/html" in ct
            or path.endswith((".js", ".css"))
            or path in {"/", "/deploy.html", "/start.html"}
        ):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


app.add_middleware(NoCacheStaticMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:9000",
        "http://127.0.0.1:9000",
        "http://localhost:3000",
        "http://localhost:3003",
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
    try:
        config_store.save_config(config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True}


@app.post("/api/deploy")
async def api_deploy(body: dict):
    """Start a deployment and return a job_id."""
    async with _state.lock:
        if _state.running:
            raise HTTPException(status_code=409, detail="A deployment is already running")

        cfg = config_store.load_config()
        cfg.update(config_store.normalize_to_snake(body))
        force_takeover: bool = bool(cfg.get("force_takeover", False))
        dev_mode: bool = bool(cfg.get("dev_mode", False))
        cfg["force_takeover"] = force_takeover
        cfg["dev_mode"] = dev_mode

        event_queue: asyncio.Queue = asyncio.Queue()
        mode = cfg.get("mode", "native")

        if mode == "native":
            deployer = NativeDeployer(cfg, event_queue)
            old_deployer = _state.deployer
            if old_deployer is not None and hasattr(old_deployer, "_processes"):
                deployer._processes.update(old_deployer._processes)
            if old_deployer is not None and hasattr(old_deployer, "_start_args"):
                deployer._start_args.update(old_deployer._start_args)
            if not force_takeover:
                ports = _enabled_service_ports(cfg)
                conflicts = await deployer._scan_port_conflicts(ports)
                if conflicts:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "Port conflicts detected",
                            "conflicts": conflicts,
                            "hint": "retry with force_takeover=true or change the conflicting port in deployer settings",
                        },
                    )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Deployment mode '{mode}' is not supported. Use 'native'.",
            )

        _state.deployer = deployer
        _state.event_queue = event_queue
        job_id = _launch_job(_run_deployment(deployer))
    return {"job_id": job_id}


async def _run_deployment(deployer) -> None:
    error_occurred = False
    cancelled = False
    try:
        await deployer.deploy()
    except asyncio.CancelledError:
        cancelled = True
    except Exception as exc:
        error_occurred = True
        error_msg = str(exc) or type(exc).__name__
    finally:
        _state.running = False
        q = _state.event_queue
        if q is not None:
            if cancelled:
                await q.put({"step": "done", "status": "cancelled", "message": "Deployment cancelled"})
            elif error_occurred:
                await q.put({"step": "done", "status": "error", "message": error_msg})
            else:
                await q.put({"step": "done", "status": "done", "message": "Deployment complete"})
            await q.put(None)  # sentinel -- signals SSE stream end


@app.get("/api/deploy/stream")
async def api_deploy_stream():
    """SSE endpoint that streams deployment progress events."""

    async def event_generator() -> AsyncGenerator[str, None]:
        queue = _state.event_queue
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
    deployer = _state.deployer
    if deployer is None or not _state.running:
        return {"ok": True, "message": "No deployment running"}
    task = _state.task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    await deployer.stop()
    _state.running = False
    return {"ok": True, "message": "Deployment stopped"}


@app.post("/api/deploy/supplement/gitnexus")
async def api_supplement_gitnexus():
    """Install GitNexus as a supplementary service after native deployment."""
    if _state.running:
        raise HTTPException(status_code=409, detail="A deployment is already running")

    cfg = config_store.load_config()
    event_queue: asyncio.Queue = asyncio.Queue()
    deployer = NativeDeployer(cfg, event_queue)
    old_deployer = _state.deployer
    if old_deployer is not None and hasattr(old_deployer, "_processes"):
        deployer._processes.update(old_deployer._processes)
    if old_deployer is not None and hasattr(old_deployer, "_start_args"):
        deployer._start_args.update(old_deployer._start_args)

    _state.deployer = deployer
    _state.event_queue = event_queue
    job_id = _launch_job(_run_supplement_gitnexus(deployer, cfg))
    return {"job_id": job_id}


async def _run_supplement_gitnexus(deployer: NativeDeployer, cfg: dict) -> None:
    cancelled = False
    error_msg = ""
    try:
        await deployer._step_install_gitnexus()
        await deployer._step_generate_config()
        config_store.save_config(cfg)
    except asyncio.CancelledError:
        cancelled = True
        q = _state.event_queue
        if q is not None:
            await q.put({"step": "install_gitnexus", "status": "cancelled", "message": "Cancelled"})
    except Exception as exc:
        error_msg = str(exc)
        q = _state.event_queue
        if q is not None:
            await q.put({"step": "install_gitnexus", "status": "error", "message": error_msg})
    finally:
        _state.running = False
        q = _state.event_queue
        if q is not None:
            if cancelled:
                await q.put({"step": "done", "status": "cancelled", "message": "GitNexus install cancelled"})
            elif error_msg:
                await q.put({"step": "done", "status": "error", "message": "GitNexus install failed"})
            else:
                await q.put({"step": "done", "status": "done", "message": "GitNexus installed"})
            await q.put(None)


@app.post("/api/quickstart")
async def api_quickstart(request: Request):
    """Quick-start services using saved config, bootstrapping missing core deps."""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}

    async with _state.lock:
        if _state.running:
            raise HTTPException(status_code=409, detail="A deployment is already running")

        force_takeover: bool = bool(body.get("force_takeover") or body.get("forceTakeover", False))
        dev_mode: bool = bool(body.get("dev_mode") or body.get("devMode", False))

        cfg = config_store.load_config()
        cfg["force_takeover"] = force_takeover
        cfg["dev_mode"] = dev_mode

        event_queue: asyncio.Queue = asyncio.Queue()
        deployer = NativeDeployer(cfg, event_queue)
        old_deployer = _state.deployer
        if old_deployer is not None and hasattr(old_deployer, "_processes"):
            deployer._processes.update(old_deployer._processes)
        if old_deployer is not None and hasattr(old_deployer, "_start_args"):
            deployer._start_args.update(old_deployer._start_args)

        if not force_takeover:
            ports = _enabled_service_ports(cfg)
            conflicts = await deployer._scan_port_conflicts(ports)
            if conflicts:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Port conflicts detected",
                        "conflicts": conflicts,
                        "hint": "retry with force_takeover=true",
                    },
                )

        _state.deployer = deployer
        _state.event_queue = event_queue
        job_id = _launch_job(_run_quickstart(deployer))
    return {"job_id": job_id}


async def _run_quickstart(deployer: NativeDeployer) -> None:
    """Ensure core runtime exists, then start services."""
    error_msg = ""
    cancelled = False
    try:
        await deployer._step_install_backend()
        await deployer._step_generate_config()
        await deployer._step_install_frontend()
        if deployer._config.get("install_gitnexus", True):
            await deployer._step_install_optional_gitnexus()
        await deployer._step_start_services()
        await deployer._step_health_check()
    except asyncio.CancelledError:
        cancelled = True
    except Exception as exc:
        error_msg = str(exc) or type(exc).__name__
    finally:
        _state.running = False
        q = _state.event_queue
        if q is not None:
            if cancelled:
                await q.put({"step": "done", "status": "cancelled", "message": "Quickstart cancelled"})
            elif error_msg:
                await q.put({"step": "done", "status": "error", "message": f"Quickstart failed: {error_msg}"})
            else:
                await q.put({"step": "done", "status": "done", "message": "All services started"})
            await q.put(None)


def _service_action_error(service: str, action: str, message: str, status_code: int = 404) -> HTTPException:
    """Build a deployer service-action error that the UI can render directly."""
    return HTTPException(
        status_code=status_code,
        detail={
            "message": message.strip("'\""),
            "service": service,
            "action": action,
            "available_services": list(KNOWN_SERVICES),
        },
    )


def _reject_unknown_service(service: str, action: str) -> None:
    if service not in KNOWN_SERVICES:
        raise _service_action_error(
            service,
            action,
            f"Unknown service '{service}'. Available services: {', '.join(KNOWN_SERVICES)}",
        )


@app.post("/api/services/{service}/restart")
async def api_service_restart(service: str):
    """Restart a specific deployed service by name."""
    _reject_unknown_service(service, "restart")
    deployer = _state.deployer
    if deployer is None:
        raise HTTPException(status_code=400, detail="No deployer instance — run a deployment first")
    if not hasattr(deployer, "restart_service"):
        raise HTTPException(status_code=400, detail="Restart not supported in this deployment mode")
    try:
        return await deployer.restart_service(service)
    except KeyError as exc:
        raise _service_action_error(service, "restart", str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/services/{service}/stop")
async def api_service_stop(service: str):
    """Stop a specific deployed service by name."""
    _reject_unknown_service(service, "stop")
    deployer = _state.deployer
    if deployer is None:
        raise HTTPException(status_code=400, detail="No deployer instance — run a deployment first")
    if not hasattr(deployer, "stop_service"):
        raise HTTPException(status_code=400, detail="Individual stop not supported in this deployment mode")
    try:
        return await deployer.stop_service(service)
    except KeyError as exc:
        raise _service_action_error(service, "stop", str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/services/{service}/start")
async def api_service_start(service: str):
    """Start a specific deployed service by name (must have been started at least once before)."""
    _reject_unknown_service(service, "start")
    deployer = _state.deployer
    if deployer is None:
        raise HTTPException(status_code=400, detail="No deployer instance — run a deployment first")
    if not hasattr(deployer, "start_service"):
        raise HTTPException(status_code=400, detail="Individual start not supported in this deployment mode")
    try:
        return await deployer.start_service(service)
    except KeyError as exc:
        raise _service_action_error(service, "start", str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/services/stop")
async def api_services_stop():
    """Stop all running service processes."""
    deployer = _state.deployer
    if deployer is None:
        return {"ok": True, "message": "No services running"}
    await deployer.stop()
    _state.running = False
    return {"ok": True, "message": "All services stopped"}


@app.get("/api/deploy/status")
async def api_deploy_status_compat():
    """Compatibility shim for cached old pages that poll this endpoint."""
    return await api_services_status()


@app.get("/api/services/status")
async def api_services_status():
    """Quick status of all known services."""
    deployer = _state.deployer
    running = _state.running
    processes: dict = {}
    if deployer and hasattr(deployer, "_processes"):
        for name, proc in deployer._processes.items():
            if name not in KNOWN_SERVICES:
                continue
            processes[name] = {
                "pid": proc.pid if proc.returncode is None else None,
                "running": proc.returncode is None,
            }
    return {"running": running, "processes": processes}


@app.get("/api/services/health")
async def api_services_health():
    """Check health of all deployed services."""
    deployer = _state.deployer
    if deployer is None:
        cfg = config_store.load_config()
        mode = cfg.get("mode", "native")
        queue: asyncio.Queue = asyncio.Queue()
        if mode == "native":
            deployer = NativeDeployer(cfg, queue)
        else:
            return {"services": {}, "error": f"Deployment mode '{mode}' is not supported"}
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
