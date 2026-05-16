"""Native deployer -- installs deps and runs CodeTalk services as local processes.

No Docker required. Targets Windows intranet environments for black-box testers.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent
DEPLOYER_DIR = Path(__file__).parent.parent
VENDOR_DIR = DEPLOYER_DIR / "vendor"

TOTAL_STEPS = 7

SERVICE_DEFAULTS = [
    ("backend", "backend_port", 8100, "http", "/health"),
    ("frontend", "frontend_port", 3005, "http", "/"),
    ("gitnexus", "gitnexus_port", 7100, "http", "/api/info"),
]


class NativeDeployer:
    def __init__(self, config: dict, event_queue: asyncio.Queue) -> None:
        self._config = config
        self._queue = event_queue
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._stopped = False

    async def deploy(self) -> None:
        self._stopped = False
        try:
            await self._step_check_env()
            if self._stopped:
                return
            await self._step_install_backend()
            if self._stopped:
                return
            await self._step_install_frontend()
            if self._stopped:
                return
            await self._step_install_gitnexus()
            if self._stopped:
                return
            await self._step_generate_config()
            if self._stopped:
                return
            await self._step_start_services()
            if self._stopped:
                return
            await self._step_health_check()
        except Exception as exc:
            await self._emit("error", "error", f"Deployment failed: {exc}", 0)

    async def stop(self) -> None:
        self._stopped = True
        for name, proc in list(self._processes.items()):
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (ProcessLookupError, asyncio.TimeoutError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass

    async def check_health(self) -> list:
        import httpx
        results = []
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            for name, port, _kind, path in self._service_targets():
                try:
                    url = f"http://localhost:{port}{path}"
                    resp = await client.get(url)
                    if resp.status_code < 500:
                        results.append({"name": name, "healthy": True, "message": f"HTTP {resp.status_code}"})
                    else:
                        results.append({"name": name, "healthy": False, "message": f"HTTP {resp.status_code}"})
                except Exception as exc:
                    results.append({"name": name, "healthy": False, "message": str(exc)})

            deepwiki_path = self._config.get("deepwiki_path", "")
            if deepwiki_path:
                port = self._config.get("deepwiki_api_port", 8091)
                try:
                    resp = await client.get(f"http://localhost:{port}/health")
                    healthy = resp.status_code < 500
                    results.append({"name": "deepwiki", "healthy": healthy, "message": f"HTTP {resp.status_code}"})
                except Exception as exc:
                    results.append({"name": "deepwiki", "healthy": False, "message": str(exc)})

        return results

    # ------------------------------------------------------------------
    # Supplementary: DeepWiki install
    # ------------------------------------------------------------------

    async def supplement_deepwiki(self, deepwiki_path: str) -> None:
        dw_dir = Path(deepwiki_path)
        total = 5

        await self._emit_sup("deepwiki_validate", "running", "Validating DeepWiki-Open path...", 1, total)
        if not dw_dir.exists():
            await self._emit_sup("deepwiki_validate", "error", f"Path not found: {deepwiki_path}", 1, total)
            raise RuntimeError(f"DeepWiki-Open path not found: {deepwiki_path}")

        has_requirements = (dw_dir / "requirements.txt").exists()
        has_package_json = (dw_dir / "package.json").exists()
        if not has_requirements and not has_package_json:
            await self._emit_sup("deepwiki_validate", "error", "Not a valid DeepWiki-Open directory (no requirements.txt or package.json)", 1, total)
            raise RuntimeError("Not a valid DeepWiki-Open directory")
        await self._emit_sup("deepwiki_validate", "done", "Path validated", 1, total)

        if has_requirements:
            await self._emit_sup("deepwiki_python", "running", "Installing Python dependencies...", 2, total)
            venv_dir = dw_dir / ".venv"
            if sys.platform == "win32":
                venv_python = venv_dir / "Scripts" / "python.exe"
                venv_pip = venv_dir / "Scripts" / "pip.exe"
            else:
                venv_python = venv_dir / "bin" / "python"
                venv_pip = venv_dir / "bin" / "pip"

            if not venv_python.exists():
                await self._emit_sup("deepwiki_python", "running", "Creating venv...", 2, total)
                rc = await self._run_stream("deepwiki_python", 2, "python", "-m", "venv", str(venv_dir))
                if rc != 0:
                    await self._emit_sup("deepwiki_python", "error", "Failed to create venv", 2, total)
                    raise RuntimeError("DeepWiki venv creation failed")

            rc = await self._run_stream("deepwiki_python", 2, str(venv_pip), "install", "-r", str(dw_dir / "requirements.txt"))
            if rc != 0:
                await self._emit_sup("deepwiki_python", "error", "pip install failed", 2, total)
                raise RuntimeError("DeepWiki pip install failed")
            await self._emit_sup("deepwiki_python", "done", "Python dependencies installed", 2, total)
        else:
            await self._emit_sup("deepwiki_python", "done", "No Python dependencies needed", 2, total)

        if has_package_json and not (dw_dir / "node_modules").exists():
            await self._emit_sup("deepwiki_node", "running", "Installing Node dependencies...", 3, total)
            npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
            rc = await self._run_stream("deepwiki_node", 3, npm_cmd, "install", cwd=str(dw_dir))
            if rc != 0:
                await self._emit_sup("deepwiki_node", "error", "npm install failed", 3, total)
                raise RuntimeError("DeepWiki npm install failed")
            await self._emit_sup("deepwiki_node", "done", "Node dependencies installed", 3, total)
        else:
            await self._emit_sup("deepwiki_node", "done", "Node dependencies already present", 3, total)

        await self._emit_sup("deepwiki_start", "running", "Starting DeepWiki services...", 4, total)
        api_port = self._config_port("deepwiki_api_port", 8091)
        ui_port = self._config_port("deepwiki_ui_port", 3001)

        if has_requirements:
            venv_dir = dw_dir / ".venv"
            venv_python = (venv_dir / "Scripts" / "python.exe") if sys.platform == "win32" else (venv_dir / "bin" / "python")
            api_cwd = str(dw_dir / "api") if (dw_dir / "api").exists() else str(dw_dir)
            await self._start_process(
                "deepwiki-api",
                [str(venv_python), "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(api_port)],
                cwd=api_cwd,
                step_name="deepwiki_start",
                step_index=4,
            )

        if has_package_json:
            npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
            await self._start_process(
                "deepwiki-ui",
                [npm_cmd, "run", "start"],
                cwd=str(dw_dir),
                step_name="deepwiki_start",
                step_index=4,
                env_extra={"PORT": str(ui_port)},
            )

        await self._emit_sup("deepwiki_start", "done", "DeepWiki services started", 4, total)

        self._config["deepwiki_path"] = deepwiki_path
        self._config["deepwiki_api_port"] = api_port
        self._config["deepwiki_ui_port"] = ui_port
        await self._step_generate_config()

        await self._emit_sup("deepwiki_health", "running", "Checking DeepWiki health...", 5, total)
        await asyncio.sleep(5)

        import httpx
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                resp = await client.get(f"http://localhost:{api_port}/health")
                if resp.status_code < 500:
                    await self._emit_sup("deepwiki_health", "done", f"DeepWiki healthy (API:{api_port} UI:{ui_port})", 5, total)
                else:
                    await self._emit_sup("deepwiki_health", "done", f"DeepWiki started — health returned HTTP {resp.status_code}", 5, total)
        except Exception:
            await self._emit_sup("deepwiki_health", "done", f"DeepWiki started (API:{api_port} UI:{ui_port}) — health check pending", 5, total)

    async def _emit_sup(self, step: str, status: str, message: str, current: int, total: int) -> None:
        await self._queue.put({
            "step": step,
            "status": status,
            "message": message,
            "progress": {"current": current, "total": total},
        })

    # ------------------------------------------------------------------
    # Step 1: Check environment
    # ------------------------------------------------------------------

    async def _step_check_env(self) -> None:
        step = 1
        await self._emit("check_env", "running", "Checking prerequisites...", step)

        py_ok = await self._check_command("python", ["--version"], "3.10")
        if not py_ok:
            py_ok = await self._check_command("python3", ["--version"], "3.10")
        if not py_ok:
            await self._emit("check_env", "error", "Python 3.10+ not found on PATH", step)
            raise RuntimeError("Python 3.10+ required")
        await self._emit("check_env", "running", "Python OK", step)

        node_ok = await self._check_command("node", ["--version"], "18")
        if not node_ok:
            await self._emit("check_env", "error", "Node.js 18+ not found on PATH", step)
            raise RuntimeError("Node.js 18+ required")
        await self._emit("check_env", "running", "Node.js OK", step)

        git_ok = await self._check_command("git", ["--version"], None)
        if not git_ok:
            await self._emit("check_env", "error", "Git not found on PATH", step)
            raise RuntimeError("Git required")
        await self._emit("check_env", "running", "Git OK", step)

        await self._emit("check_env", "done", "All prerequisites met", step)

    async def _check_command(self, cmd: str, args: list, min_version: Optional[str]) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                cmd, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return False
            if min_version:
                import re
                version_str = stdout.decode().strip()
                match = re.search(r"(\d+)\.(\d+)", version_str)
                if match:
                    major = int(match.group(1))
                    minor = int(match.group(2))
                    req_parts = min_version.split(".")
                    req_major = int(req_parts[0])
                    req_minor = int(req_parts[1]) if len(req_parts) > 1 else 0
                    return (major, minor) >= (req_major, req_minor)
            return True
        except (FileNotFoundError, asyncio.TimeoutError):
            return False

    # ------------------------------------------------------------------
    # Step 2: Install backend dependencies
    # ------------------------------------------------------------------

    async def _step_install_backend(self) -> None:
        step = 2
        backend_dir = PROJECT_ROOT / "backend"
        venv_dir = backend_dir / ".venv311"
        await self._emit("install_backend", "running", "Setting up backend...", step)

        if sys.platform == "win32":
            venv_python = venv_dir / "Scripts" / "python.exe"
            venv_pip = venv_dir / "Scripts" / "pip.exe"
        else:
            venv_python = venv_dir / "bin" / "python"
            venv_pip = venv_dir / "bin" / "pip"

        if not venv_python.exists():
            await self._emit("install_backend", "running", "Creating virtual environment...", step)
            rc = await self._run_stream("install_backend", step, "python", "-m", "venv", str(venv_dir))
            if rc != 0:
                await self._emit("install_backend", "error", "Failed to create venv", step)
                raise RuntimeError("venv creation failed")

        await self._emit("install_backend", "running", "Installing Python dependencies...", step)

        pip_args = [str(venv_pip), "install", "-r", str(backend_dir / "requirements.txt")]

        wheels_dir = VENDOR_DIR / "wheels"
        if wheels_dir.exists() and any(wheels_dir.iterdir()):
            pip_args.extend(["--no-index", "--find-links", str(wheels_dir)])

        rc = await self._run_stream("install_backend", step, *pip_args)
        if rc != 0:
            await self._emit("install_backend", "error", "pip install failed", step)
            raise RuntimeError("Backend dependency installation failed")

        data_dir = backend_dir / "data" / "outputs"
        data_dir.mkdir(parents=True, exist_ok=True)

        await self._emit("install_backend", "done", "Backend dependencies installed", step)

    # ------------------------------------------------------------------
    # Step 3: Install frontend dependencies
    # ------------------------------------------------------------------

    async def _step_install_frontend(self) -> None:
        step = 3
        frontend_dir = PROJECT_ROOT / "frontend"
        await self._emit("install_frontend", "running", "Installing frontend dependencies...", step)

        node_modules = frontend_dir / "node_modules"
        if node_modules.exists() and (node_modules / ".package-lock.json").exists():
            await self._emit("install_frontend", "done", "Frontend dependencies already installed", step)
            return

        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        rc = await self._run_stream(
            "install_frontend", step,
            npm_cmd, "install",
            cwd=str(frontend_dir),
        )
        if rc != 0:
            await self._emit("install_frontend", "error", "npm install failed", step)
            raise RuntimeError("Frontend dependency installation failed")

        await self._emit("install_frontend", "done", "Frontend dependencies installed", step)

    # ------------------------------------------------------------------
    # Step 4: Install GitNexus
    # ------------------------------------------------------------------

    async def _step_install_gitnexus(self) -> None:
        step = 4
        await self._emit("install_gitnexus", "running", "Setting up GitNexus...", step)

        rc, stdout, _ = await self._run_capture("gitnexus", "--version")
        if rc == 0 and stdout.strip():
            await self._emit("install_gitnexus", "done", f"GitNexus already installed (v{stdout.strip()})", step)
            self._config["_gitnexus_cmd"] = ["gitnexus"]
            return

        await self._emit("install_gitnexus", "running", "Trying npm install -g gitnexus...", step)
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        rc = await self._run_stream("install_gitnexus", step, npm_cmd, "install", "-g", "gitnexus")
        if rc == 0:
            rc2, stdout2, _ = await self._run_capture("gitnexus", "--version")
            if rc2 == 0:
                await self._emit("install_gitnexus", "done", f"GitNexus installed via npm (v{stdout2.strip()})", step)
                self._config["_gitnexus_cmd"] = ["gitnexus"]
                return

        vendor_entry = VENDOR_DIR / "gitnexus" / "dist" / "cli" / "index.js"
        if vendor_entry.exists():
            rc3, stdout3, _ = await self._run_capture("node", str(vendor_entry), "--version")
            if rc3 == 0:
                await self._emit(
                    "install_gitnexus", "done",
                    f"GitNexus loaded from vendor (v{stdout3.strip()})", step,
                )
                self._config["_gitnexus_cmd"] = ["node", str(vendor_entry)]
                return

        await self._emit(
            "install_gitnexus", "error",
            "GitNexus not available: npm install failed and vendor/gitnexus not found", step,
        )
        raise RuntimeError("GitNexus installation failed")

    # ------------------------------------------------------------------
    # Step 5: Generate config files
    # ------------------------------------------------------------------

    async def _step_generate_config(self) -> None:
        step = 5
        await self._emit("generate_config", "running", "Generating configuration files...", step)

        cfg = self._config
        backend_port = cfg.get("backend_port", 8100)
        frontend_port = cfg.get("frontend_port", 3005)
        gitnexus_port = cfg.get("gitnexus_port", 7100)
        deepwiki_api_port = cfg.get("deepwiki_api_port", 8091)
        deepwiki_ui_port = cfg.get("deepwiki_ui_port", 3001)
        deepwiki_path = cfg.get("deepwiki_path", "")

        env_lines = [
            "DATA_DIR=data",
            "SQLITE_DB=data/codetalk.db",
            f"GITNEXUS_BASE_URL=http://localhost:{gitnexus_port}",
            f"GITNEXUS_PORT={gitnexus_port}",
            "GITNEXUS_BIN=gitnexus",
            f"CORS_ORIGINS=http://localhost:{frontend_port},http://127.0.0.1:{frontend_port}",
            "TOOL_HEALTH_INTERVAL=30",
        ]
        if deepwiki_path:
            env_lines.extend([
                f"DEEPWIKI_API_URL=http://localhost:{deepwiki_api_port}",
                f"DEEPWIKI_UI_URL=http://localhost:{deepwiki_ui_port}",
                f"DEEPWIKI_API_PORT={deepwiki_api_port}",
                f"DEEPWIKI_UI_PORT={deepwiki_ui_port}",
                f"DEEPWIKI_PATH={deepwiki_path}",
            ])

        tiktoken_cache = VENDOR_DIR / "tiktoken_cache"
        if tiktoken_cache.exists():
            env_lines.append(f"TIKTOKEN_CACHE_DIR={tiktoken_cache}")

        backend_env = PROJECT_ROOT / "backend" / ".env"
        backend_env.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
        await self._emit("generate_config", "running", f"Written {backend_env}", step)

        frontend_env = PROJECT_ROOT / "frontend" / ".env.local"
        frontend_env.write_text(
            f"NEXT_PUBLIC_API_URL=http://localhost:{backend_port}\n",
            encoding="utf-8",
        )
        await self._emit("generate_config", "running", f"Written {frontend_env}", step)

        await self._emit("generate_config", "done", "Configuration files generated", step)

    # ------------------------------------------------------------------
    # Step 6: Start services
    # ------------------------------------------------------------------

    async def _step_start_services(self) -> None:
        step = 6
        await self._emit("start_services", "running", "Starting services...", step)

        cfg = self._config
        backend_port = cfg.get("backend_port", 8100)
        frontend_port = cfg.get("frontend_port", 3005)
        gitnexus_port = cfg.get("gitnexus_port", 7100)

        backend_dir = PROJECT_ROOT / "backend"
        frontend_dir = PROJECT_ROOT / "frontend"

        if sys.platform == "win32":
            venv_python = backend_dir / ".venv311" / "Scripts" / "python.exe"
        else:
            venv_python = backend_dir / ".venv311" / "bin" / "python"

        await self._start_process(
            "backend",
            [
                str(venv_python), "-m", "uvicorn",
                "app.main:app",
                "--host", "0.0.0.0",
                "--port", str(backend_port),
            ],
            cwd=str(backend_dir),
            step_name="start_services",
            step_index=step,
        )

        gitnexus_cmd = cfg.get("_gitnexus_cmd", ["gitnexus"])
        await self._start_process(
            "gitnexus",
            [*gitnexus_cmd, "serve", "--port", str(gitnexus_port), "--host", "0.0.0.0"],
            cwd=str(PROJECT_ROOT),
            step_name="start_services",
            step_index=step,
        )

        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        await self._start_process(
            "frontend",
            [npm_cmd, "run", "dev"],
            cwd=str(frontend_dir),
            step_name="start_services",
            step_index=step,
            env_extra={"PORT": str(frontend_port)},
        )

        await asyncio.sleep(3)
        await self._emit("start_services", "done", "All core services started", step)

    async def _start_process(
        self,
        name: str,
        cmd: list,
        cwd: str,
        step_name: str,
        step_index: int,
        env_extra: Optional[dict] = None,
    ) -> None:
        await self._emit(step_name, "running", f"Starting {name}...", step_index)

        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=env,
        )
        self._processes[name] = proc

        asyncio.ensure_future(self._drain_output(name, proc, step_name, step_index))
        await self._emit(step_name, "running", f"{name} started (PID {proc.pid})", step_index)

    async def _drain_output(
        self, name: str, proc: asyncio.subprocess.Process, step_name: str, step_index: int
    ) -> None:
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").rstrip()
            if line:
                await self._queue.put({
                    "step": step_name,
                    "status": "running",
                    "message": f"[{name}] {line}",
                    "progress": {"current": step_index, "total": TOTAL_STEPS},
                })

    # ------------------------------------------------------------------
    # Step 7: Health check
    # ------------------------------------------------------------------

    async def _step_health_check(self) -> None:
        step = 7
        await self._emit("health_check", "running", "Waiting for services to become healthy...", step)

        import httpx
        max_wait = 60
        interval = 3
        elapsed = 0

        while elapsed < max_wait:
            if self._stopped:
                return
            all_ok = True
            for name, port, _kind, path in self._service_targets():
                try:
                    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                        resp = await client.get(f"http://localhost:{port}{path}")
                        if resp.status_code >= 500:
                            all_ok = False
                except Exception:
                    all_ok = False

            if all_ok:
                await self._emit("health_check", "done", "All services healthy!", step)
                return

            await self._emit("health_check", "running", f"Waiting... ({elapsed}s / {max_wait}s)", step)
            await asyncio.sleep(interval)
            elapsed += interval

        await self._emit("health_check", "error", "Some services did not become healthy in time", step)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _run_stream(self, step_name: str, step_index: int, *cmd: str, cwd: str | None = None) -> int:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
        except FileNotFoundError:
            await self._emit(step_name, "error", f"Command not found: {cmd[0]}", step_index)
            return 1

        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            if self._stopped:
                proc.terminate()
                break
            line = raw_line.decode(errors="replace").rstrip()
            if line:
                await self._queue.put({
                    "step": step_name,
                    "status": "running",
                    "message": line,
                    "progress": {"current": step_index, "total": TOTAL_STEPS},
                })
        await proc.wait()
        return proc.returncode or 0

    async def _run_capture(self, *cmd: str) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")
        except (FileNotFoundError, asyncio.TimeoutError):
            return 1, "", ""

    def _service_targets(self) -> list[tuple[str, int, str, str]]:
        services: list[tuple[str, int, str, str]] = []
        for name, port_key, default_port, kind, path in SERVICE_DEFAULTS:
            services.append((name, self._config_port(port_key, default_port), kind, path))
        return services

    def _config_port(self, key: str, default: int) -> int:
        try:
            return int(self._config.get(key, default))
        except (TypeError, ValueError):
            return default

    async def _emit(self, step: str, status: str, message: str, step_index: int) -> None:
        await self._queue.put({
            "step": step,
            "status": status,
            "message": message,
            "progress": {"current": step_index, "total": TOTAL_STEPS},
        })
