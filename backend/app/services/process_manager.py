"""Process manager for spawning, monitoring, and restarting local tool processes.

Singleton that manages GitNexus, DeepWiki-API, and DeepWiki-UI as subprocesses.
Health is checked periodically; crashed processes are auto-restarted.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool registry -- static definitions for each managed tool
# ---------------------------------------------------------------------------

def _build_registry() -> dict[str, dict[str, Any]]:
    """Build the tool registry from current settings.

    Called once at import time.  Values that depend on ``settings`` are
    resolved here so the module works regardless of import order.
    """
    return {
        "gitnexus": {
            "display_name": "GitNexus",
            "command": [
                settings.gitnexus_bin, "serve",
                "--port", str(settings.gitnexus_port),
                "--host", "0.0.0.0",
            ],
            "health_url": f"http://localhost:{settings.gitnexus_port}/api/info",
            # /api/info returns 500 on some versions; /api/analyze (POST) proves reachable
            "health_fallback_url": f"http://localhost:{settings.gitnexus_port}/api/analyze",
            "cwd": None,
            "env": {},
        },
        "deepwiki-api": {
            "display_name": "DeepWiki API",
            "command": ["python", "-m", "api.main"],
            "health_url": f"http://localhost:{settings.deepwiki_api_port}/health",
            "cwd": settings.deepwiki_path or None,
            "env": {"TIKTOKEN_CACHE_DIR": str(settings.tiktoken_cache_path)},
        },
        "deepwiki-ui": {
            "display_name": "DeepWiki UI",
            "command": ["npm", "run", "start"],
            "health_url": f"http://localhost:{settings.deepwiki_ui_port}",
            "cwd": settings.deepwiki_path or None,
            "env": {},
        },
    }


TOOL_REGISTRY: dict[str, dict[str, Any]] = _build_registry()


# ---------------------------------------------------------------------------
# Data container for a managed process
# ---------------------------------------------------------------------------

@dataclass
class ManagedProcess:
    """Runtime state for one managed subprocess."""

    name: str
    display_name: str
    process: asyncio.subprocess.Process | None = None
    status: str = "stopped"  # stopped | starting | running | error
    pid: int | None = None
    started_at: float | None = None
    restart_count: int = 0
    last_error: str | None = None
    _config: dict[str, Any] = field(default_factory=dict)

    @property
    def uptime_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        return time.time() - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "status": self.status,
            "healthy": self.status == "running",
            "pid": self.pid,
            "uptime": round(self.uptime_seconds, 1),
            "restart_count": self.restart_count,
            "last_error": self.last_error,
        }


# ---------------------------------------------------------------------------
# ProcessManager singleton
# ---------------------------------------------------------------------------

class ProcessManager:
    """Manages tool subprocesses with health monitoring and auto-restart."""

    _instance: "ProcessManager | None" = None

    def __init__(self) -> None:
        self._processes: dict[str, ManagedProcess] = {}
        self._health_task: asyncio.Task[None] | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._shutdown_event: asyncio.Event = asyncio.Event()

        for name, cfg in TOOL_REGISTRY.items():
            self._processes[name] = ManagedProcess(
                name=name,
                display_name=cfg["display_name"],
                _config=cfg,
            )

    @classmethod
    def get_instance(cls) -> "ProcessManager":
        """Return the singleton instance, creating it on first call."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(10, connect=5),
                trust_env=False,
            )
        return self._http_client

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_monitoring(self) -> None:
        """Start the background health-check loop."""
        if self._health_task is None or self._health_task.done():
            self._shutdown_event.clear()
            self._health_task = asyncio.create_task(self._health_loop())
            logger.info(
                "ProcessManager: health monitoring started (interval=%ds)",
                settings.tool_health_interval,
            )

    async def shutdown_all(self) -> None:
        """Stop all managed processes and cancel the health loop."""
        self._shutdown_event.set()

        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None

        for name in list(self._processes.keys()):
            await self.stop(name)

        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

        logger.info("ProcessManager: all processes shut down")

    # ------------------------------------------------------------------
    # Process control
    # ------------------------------------------------------------------

    async def start(self, name: str) -> bool:
        """Spawn a tool process. Returns True on success."""
        mp = self._processes.get(name)
        if mp is None:
            logger.warning("ProcessManager: unknown tool '%s'", name)
            return False

        # Already running check
        if mp.status == "running" and mp.process is not None and mp.process.returncode is None:
            logger.info("ProcessManager: '%s' is already running (pid=%s)", name, mp.pid)
            return True

        cfg = mp._config
        cmd: list[str] = cfg["command"]
        cwd: str | None = cfg.get("cwd") or None

        # Validate working directory when required
        if cwd is not None and not os.path.isdir(cwd):
            mp.status = "error"
            mp.last_error = f"Working directory does not exist: {cwd}"
            logger.error("ProcessManager: cannot start '%s' -- %s", name, mp.last_error)
            return False

        env = {**os.environ, **cfg.get("env", {})}
        mp.status = "starting"

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            mp.process = proc
            mp.pid = proc.pid
            mp.started_at = time.time()
            mp.status = "running"
            mp.last_error = None
            logger.info("ProcessManager: started '%s' (pid=%d)", name, proc.pid)
            return True
        except Exception as exc:
            mp.status = "error"
            mp.last_error = str(exc)
            logger.error("ProcessManager: failed to start '%s': %s", name, exc)
            return False

    async def stop(self, name: str) -> bool:
        """Gracefully stop a tool process. Returns True on success."""
        mp = self._processes.get(name)
        if mp is None:
            logger.warning("ProcessManager: unknown tool '%s'", name)
            return False

        if mp.process is None or mp.process.returncode is not None:
            mp.status = "stopped"
            mp.pid = None
            mp.started_at = None
            return True

        try:
            mp.process.terminate()
            try:
                await asyncio.wait_for(mp.process.wait(), timeout=10)
            except asyncio.TimeoutError:
                mp.process.kill()
                await mp.process.wait()
            logger.info("ProcessManager: stopped '%s' (pid=%s)", name, mp.pid)
        except ProcessLookupError:
            logger.debug("ProcessManager: '%s' already exited", name)
        except Exception as exc:
            logger.error("ProcessManager: error stopping '%s': %s", name, exc)
            return False
        finally:
            mp.status = "stopped"
            mp.pid = None
            mp.started_at = None
            mp.process = None

        return True

    async def restart(self, name: str) -> bool:
        """Stop then start a tool process."""
        mp = self._processes.get(name)
        if mp is None:
            logger.warning("ProcessManager: unknown tool '%s'", name)
            return False

        await self.stop(name)
        mp.restart_count += 1
        return await self.start(name)

    # ------------------------------------------------------------------
    # Health checking
    # ------------------------------------------------------------------

    async def health_check(self, name: str) -> dict[str, Any]:
        """Check a single tool's health via its HTTP endpoint.

        If the primary health_url returns 5xx (or throws a non-connection error),
        and a health_fallback_url is configured, a POST probe is attempted against
        the fallback URL.  A 4xx response on the fallback still proves the service
        is reachable and is treated as healthy.  ConnectError on the primary is
        never retried via the fallback (the service is genuinely unreachable).
        """
        mp = self._processes.get(name)
        if mp is None:
            return {"name": name, "healthy": False, "error": "unknown tool"}

        cfg = mp._config
        health_url: str = cfg.get("health_url", "")
        fallback_url: str = cfg.get("health_fallback_url", "")

        # Detect already-exited subprocess
        if mp.process is not None and mp.process.returncode is not None:
            mp.status = "error"
            mp.last_error = f"Process exited with code {mp.process.returncode}"
            return {**mp.to_dict(), "healthy": False}

        # No health URL or process not started
        if not health_url or mp.status == "stopped":
            return mp.to_dict()

        need_fallback = False
        last_error = ""
        try:
            resp = await self.http_client.get(health_url)
            if resp.status_code < 300:
                mp.status = "running"
                mp.last_error = None
                return {**mp.to_dict(), "healthy": True}
            # Any non-2xx (4xx, 5xx) → try fallback; some GitNexus versions return
            # 404/405 on /api/info even when the service is running.
            need_fallback = True
            last_error = f"Health check HTTP {resp.status_code}"
        except httpx.ConnectError:
            # Service is genuinely unreachable — no fallback
            if mp.status == "running":
                mp.status = "error"
                mp.last_error = "Health endpoint unreachable"
            return {**mp.to_dict(), "healthy": False}
        except Exception as exc:
            need_fallback = True
            last_error = str(exc)

        if need_fallback and fallback_url:
            try:
                resp = await self.http_client.post(fallback_url, json={})
                if resp.status_code < 500:
                    mp.status = "running"
                    mp.last_error = None
                    logger.info(
                        "ProcessManager: '%s' fallback probe healthy (HTTP %d)",
                        name, resp.status_code,
                    )
                    return {**mp.to_dict(), "healthy": True}
                last_error = f"Fallback probe HTTP {resp.status_code}"
            except Exception as exc:
                last_error = str(exc)

        mp.status = "error"
        mp.last_error = last_error
        return {**mp.to_dict(), "healthy": False}

    async def get_all_status(self) -> list[dict[str, Any]]:
        """Return status of all registered tools with live health checks."""
        results: list[dict[str, Any]] = []
        for name in self._processes:
            status = await self.health_check(name)
            results.append(status)
        return results

    # ------------------------------------------------------------------
    # Background health loop
    # ------------------------------------------------------------------

    async def _health_loop(self) -> None:
        """Periodically check health and auto-restart crashed processes."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(settings.tool_health_interval)
            except asyncio.CancelledError:
                return

            for name, mp in self._processes.items():
                if mp.status == "stopped":
                    continue

                # Detect crashed subprocess
                if mp.process is not None and mp.process.returncode is not None:
                    exit_code = mp.process.returncode
                    logger.warning(
                        "ProcessManager: '%s' exited unexpectedly (code=%d), auto-restarting",
                        name,
                        exit_code,
                    )
                    mp.last_error = f"Exited with code {exit_code}"
                    mp.restart_count += 1
                    await self.start(name)
                    continue

                # HTTP health check for running processes
                if mp.status == "running":
                    health = await self.health_check(name)
                    if not health.get("healthy", False) and mp.status == "error":
                        logger.warning(
                            "ProcessManager: '%s' failed health check, auto-restarting",
                            name,
                        )
                        mp.restart_count += 1
                        await self.restart(name)
