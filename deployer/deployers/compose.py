"""Docker Compose deployer -- orchestrates build, up, and health verification."""

import asyncio
import base64
import os
import socket
from pathlib import Path
from typing import Optional

try:
    import aiohttp
    _AIOHTTP = True
except ImportError:
    _AIOHTTP = False

from jinja2 import Environment, FileSystemLoader

PROJECT_ROOT = Path(__file__).parent.parent.parent
DEPLOYER_DIR = Path(__file__).parent.parent
TEMPLATE_DIR = DEPLOYER_DIR / "templates"

SERVICES = [
    ("postgres", 5433, "tcp", None),
    ("backend", 8000, "http", "/health"),
    ("frontend", 3005, "http", "/"),
    ("deepwiki", 8001, "tcp", None),
    ("gitnexus", 7100, "tcp", None),
    ("joern", 8080, "tcp", None),
    ("codecompass", 16251, "http", "/"),
]
TOTAL_STEPS = 8  # env(1) + build(2) + up(3) + health(4-8)


def _generate_fernet_key() -> str:
    try:
        from cryptography.fernet import Fernet  # type: ignore
        return Fernet.generate_key().decode()
    except ImportError:
        return base64.urlsafe_b64encode(os.urandom(32)).decode()


class ComposeDeployer:
    def __init__(self, config: dict, event_queue: asyncio.Queue):
        self._config = config
        self._queue = event_queue
        self._process: Optional[asyncio.subprocess.Process] = None
        self._stopped = False
    async def deploy(self) -> None:
        self._stopped = False
        try:
            await self._step_env()
            if self._stopped:
                return
            await self._step_build()
            if self._stopped:
                return
            await self._step_up()
            if self._stopped:
                return
            await self._step_health_wait()
        except Exception as exc:
            await self._emit("error", "error", f"Deployment failed: {exc}", 0)
            raise

    async def stop(self) -> None:
        self._stopped = True
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass

    async def restart_service(self, name: str) -> dict:
        """Restart a Docker Compose service by name."""
        code = await self._run_compose_simple("restart", name)
        if code != 0:
            raise RuntimeError(f"docker compose restart {name} failed (exit {code})")
        return {"ok": True, "service": name}

    async def _run_compose_simple(self, *args: str) -> int:
        proc = await asyncio.create_subprocess_exec(
            "docker", "compose", *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(PROJECT_ROOT),
        )
        await proc.wait()
        return proc.returncode or 0

    async def check_health(self) -> list:
        if not _AIOHTTP:
            return [
                {"name": s[0], "healthy": False, "message": "aiohttp not installed"}
                for s in SERVICES
            ]
        import aiohttp as _ah
        results = []
        async with _ah.ClientSession(timeout=_ah.ClientTimeout(total=5)) as session:
            for name, port, kind, path in SERVICES:
                healthy, msg = await self._probe(session, name, port, kind, path)
                results.append({"name": name, "healthy": healthy, "message": msg})
        return results
    async def _step_env(self) -> None:
        await self._emit("env", "running", "Generating .env file...", 1)
        try:
            env_content = self._render_env()
            env_path = PROJECT_ROOT / ".env"
            env_path.write_text(env_content, encoding="utf-8")
            await self._emit("env", "done", ".env file written successfully", 1)
        except Exception as exc:
            await self._emit("env", "error", f"Failed to write .env: {exc}", 1)
            raise

    async def _step_build(self) -> None:
        await self._emit("build", "running", "Building Docker images (this may take a while)...", 2)
        code = await self._run_compose("build", step_index=2, total=TOTAL_STEPS)
        if code != 0:
            await self._emit("build", "error", f"docker compose build failed (exit {code})", 2)
            raise RuntimeError(f"docker compose build exited with code {code}")
        await self._emit("build", "done", "Docker images built successfully", 2)

    async def _step_up(self) -> None:
        await self._emit("up", "running", "Starting services with docker compose up -d...", 3)
        code = await self._run_compose("up", "-d", step_index=3, total=TOTAL_STEPS)
        if code != 0:
            await self._emit("up", "error", f"docker compose up failed (exit {code})", 3)
            raise RuntimeError(f"docker compose up exited with code {code}")
        await self._emit("up", "done", "All containers started", 3)
    async def _step_health_wait(self) -> None:
        await self._emit("health", "running", "Waiting for services to become healthy...", 4)
        timeout_s = 120
        poll_interval = 3
        if not _AIOHTTP:
            await self._emit("health", "error", "aiohttp not installed -- cannot check health", 4)
            raise RuntimeError("aiohttp not installed -- cannot check health")
        import aiohttp as _ah
        failed: list[str] = []
        async with _ah.ClientSession(timeout=_ah.ClientTimeout(total=5)) as session:
            for idx, (name, port, kind, path) in enumerate(SERVICES):
                step_num = 4 + idx
                start = asyncio.get_event_loop().time()
                while True:
                    if self._stopped:
                        return
                    healthy, msg = await self._probe(session, name, port, kind, path)
                    if healthy:
                        await self._emit(f"health_{name}", "done", f"{name} is healthy", step_num)
                        break
                    elapsed = asyncio.get_event_loop().time() - start
                    if elapsed >= timeout_s:
                        await self._emit(
                            f"health_{name}", "error",
                            f"{name} did not become healthy within {timeout_s}s: {msg}",
                            step_num,
                        )
                        failed.append(name)
                        break
                    await self._emit(f"health_{name}", "running", f"Waiting for {name}... ({msg})", step_num)
                    await asyncio.sleep(poll_interval)
        if failed:
            raise RuntimeError(f"Services did not become healthy: {', '.join(failed)}")
        await self._emit("health", "done", "Health checks complete -- deployment finished!", TOTAL_STEPS)
    def _render_env(self) -> str:
        cfg = dict(self._config)
        if not cfg.get("fernet_key"):
            cfg["fernet_key"] = _generate_fernet_key()
        jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            keep_trailing_newline=True,
        )
        tmpl = jinja_env.get_template("env.j2")
        return tmpl.render(**cfg)

    async def _run_compose(self, *args: str, step_index: int, total: int) -> int:
        cmd = ["docker", "compose", *args]
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
        )
        assert self._process.stdout is not None
        async for raw_line in self._process.stdout:
            if self._stopped:
                break
            line = raw_line.decode(errors="replace").rstrip()
            if line:
                await self._queue.put({
                    "step": f"compose_{args[0]}",
                    "status": "running",
                    "message": line,
                    "progress": {"current": step_index, "total": total},
                })
        await self._process.wait()
        return self._process.returncode or 0

    async def _emit(self, step: str, status: str, message: str, step_index: int) -> None:
        await self._queue.put({
            "step": step,
            "status": status,
            "message": message,
            "progress": {"current": step_index, "total": TOTAL_STEPS},
        })

    @staticmethod
    async def _probe(session, name: str, port: int, kind: str, path: Optional[str]) -> tuple:
        if kind == "tcp":
            loop = asyncio.get_event_loop()
            try:
                conn = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: socket.create_connection(("localhost", port), timeout=3),
                    ),
                    timeout=5,
                )
                conn.close()
                return True, "TCP connection OK"
            except Exception as exc:
                return False, str(exc)
        else:
            url = f"http://localhost:{port}{path}"
            try:
                async with session.get(url) as resp:
                    if resp.status < 500:
                        return True, f"HTTP {resp.status}"
                    return False, f"HTTP {resp.status}"
            except Exception as exc:
                return False, str(exc)
