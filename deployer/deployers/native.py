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
            if self._config.get("install_gitnexus", True):
                await self._step_install_gitnexus()
                if self._stopped:
                    return
            if self._config.get("install_deepwiki", False):
                await self.supplement_deepwiki(self._config.get("deepwiki_path", ""))
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
            await self._emit("error", "error", f"部署失败：{exc}", 0)
            raise

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

            if self._config.get("install_deepwiki", False):
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

        await self._emit_sup("deepwiki_validate", "running", "验证 DeepWiki-Open 路径...", 1, total)
        if not dw_dir.exists():
            await self._emit_sup("deepwiki_validate", "error", f"路径不存在：{deepwiki_path}", 1, total)
            raise RuntimeError(f"DeepWiki-Open path not found: {deepwiki_path}")

        has_requirements = (dw_dir / "requirements.txt").exists()
        has_package_json = (dw_dir / "package.json").exists()
        if not has_requirements and not has_package_json:
            await self._emit_sup("deepwiki_validate", "error", "无效的 DeepWiki-Open 目录（缺少 requirements.txt 或 package.json）", 1, total)
            raise RuntimeError("Not a valid DeepWiki-Open directory")
        await self._emit_sup("deepwiki_validate", "done", "路径验证通过", 1, total)

        if has_requirements:
            await self._emit_sup("deepwiki_python", "running", "安装 Python 依赖...", 2, total)
            venv_dir = dw_dir / ".venv"
            if sys.platform == "win32":
                venv_python = venv_dir / "Scripts" / "python.exe"
                venv_pip = venv_dir / "Scripts" / "pip.exe"
            else:
                venv_python = venv_dir / "bin" / "python"
                venv_pip = venv_dir / "bin" / "pip"

            if not venv_python.exists():
                await self._emit_sup("deepwiki_python", "running", "创建虚拟环境...", 2, total)
                rc = await self._run_stream("deepwiki_python", 2, "python", "-m", "venv", str(venv_dir))
                if rc != 0:
                    await self._emit_sup("deepwiki_python", "error", "虚拟环境创建失败", 2, total)
                    raise RuntimeError("DeepWiki venv creation failed")

            rc = await self._run_stream("deepwiki_python", 2, str(venv_pip), "install", "-r", str(dw_dir / "requirements.txt"))
            if rc != 0:
                await self._emit_sup("deepwiki_python", "error", "pip install 失败", 2, total)
                raise RuntimeError("DeepWiki pip install failed")
            await self._emit_sup("deepwiki_python", "done", "Python 依赖安装完成", 2, total)
        else:
            await self._emit_sup("deepwiki_python", "done", "无需 Python 依赖", 2, total)

        if has_package_json and not (dw_dir / "node_modules").exists():
            await self._emit_sup("deepwiki_node", "running", "安装 Node 依赖...", 3, total)
            npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
            rc = await self._run_stream("deepwiki_node", 3, npm_cmd, "install", cwd=str(dw_dir))
            if rc != 0:
                await self._emit_sup("deepwiki_node", "error", "npm install 失败", 3, total)
                raise RuntimeError("DeepWiki npm install failed")
            await self._emit_sup("deepwiki_node", "done", "Node 依赖安装完成", 3, total)
        else:
            await self._emit_sup("deepwiki_node", "done", "Node 依赖已存在，跳过", 3, total)

        await self._emit_sup("deepwiki_start", "running", "启动 DeepWiki 服务...", 4, total)
        api_port = self._config_port("deepwiki_api_port", 8091)
        ui_port = self._config_port("deepwiki_ui_port", 3001)

        llm_env: dict[str, str] = {}
        llm_base_url = self._config.get("llm_base_url", "")
        if llm_base_url:
            llm_env = {
                "OPENAI_BASE_URL": llm_base_url,
                "OPENAI_API_KEY": self._config.get("llm_api_key", ""),
                "LLM_MODEL": self._config.get("llm_model", ""),
                "FORCE_DIRECT": "true",
                "TRUST_ENV": "false",
            }

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
                env_extra=llm_env or None,
            )

        if has_package_json:
            npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
            await self._start_process(
                "deepwiki-ui",
                [npm_cmd, "run", "start"],
                cwd=str(dw_dir),
                step_name="deepwiki_start",
                step_index=4,
                env_extra={"PORT": str(ui_port), **llm_env},
            )

        await self._emit_sup("deepwiki_start", "done", "DeepWiki 服务已启动", 4, total)

        self._config["deepwiki_path"] = deepwiki_path
        self._config["deepwiki_api_port"] = api_port
        self._config["deepwiki_ui_port"] = ui_port
        await self._step_generate_config()

        await self._emit_sup("deepwiki_health", "running", "检查 DeepWiki 健康状态...", 5, total)
        await asyncio.sleep(5)

        import httpx
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                resp = await client.get(f"http://localhost:{api_port}/health")
                if resp.status_code < 500:
                    await self._emit_sup("deepwiki_health", "done", f"DeepWiki 健康运行（API:{api_port} UI:{ui_port}）", 5, total)
                else:
                    await self._emit_sup("deepwiki_health", "done", f"DeepWiki 已启动 — 健康检查返回 HTTP {resp.status_code}", 5, total)
        except Exception:
            await self._emit_sup("deepwiki_health", "done", f"DeepWiki 已启动（API:{api_port} UI:{ui_port}）— 健康检查待确认", 5, total)

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
        await self._emit("check_env", "running", "检查运行环境...", step)

        py_ok = await self._check_command("python", ["--version"], "3.10")
        if not py_ok:
            py_ok = await self._check_command("python3", ["--version"], "3.10")
        if not py_ok:
            await self._emit("check_env", "error", "PATH 中未找到 Python 3.10+", step)
            raise RuntimeError("Python 3.10+ required")
        await self._emit("check_env", "running", "Python 检查通过", step)

        node_ok = await self._check_command("node", ["--version"], "18")
        if not node_ok:
            await self._emit("check_env", "error", "PATH 中未找到 Node.js 18+", step)
            raise RuntimeError("Node.js 18+ required")
        await self._emit("check_env", "running", "Node.js 检查通过", step)

        git_ok = await self._check_command("git", ["--version"], None)
        if not git_ok:
            await self._emit("check_env", "error", "PATH 中未找到 Git", step)
            raise RuntimeError("Git required")
        await self._emit("check_env", "running", "Git 检查通过", step)

        await self._emit("check_env", "done", "运行环境检查完成", step)

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
        await self._emit("install_backend", "running", "配置后端...", step)

        if sys.platform == "win32":
            venv_python = venv_dir / "Scripts" / "python.exe"
            venv_pip = venv_dir / "Scripts" / "pip.exe"
        else:
            venv_python = venv_dir / "bin" / "python"
            venv_pip = venv_dir / "bin" / "pip"

        if not venv_python.exists():
            await self._emit("install_backend", "running", "创建虚拟环境...", step)
            rc = await self._run_stream("install_backend", step, "python", "-m", "venv", str(venv_dir))
            if rc != 0:
                await self._emit("install_backend", "error", "虚拟环境创建失败", step)
                raise RuntimeError("venv creation failed")

        await self._emit("install_backend", "running", "安装 Python 依赖...", step)

        pip_args = [str(venv_pip), "install", "-r", str(backend_dir / "requirements.txt")]

        wheels_dir = VENDOR_DIR / "wheels"
        if wheels_dir.exists() and any(wheels_dir.iterdir()):
            pip_args.extend(["--no-index", "--find-links", str(wheels_dir)])

        rc = await self._run_stream("install_backend", step, *pip_args)
        if rc != 0:
            await self._emit("install_backend", "error", "pip install 失败", step)
            raise RuntimeError("Backend dependency installation failed")

        data_dir = backend_dir / "data" / "outputs"
        data_dir.mkdir(parents=True, exist_ok=True)

        await self._emit("install_backend", "done", "后端依赖安装完成", step)

    # ------------------------------------------------------------------
    # Step 3: Install frontend dependencies
    # ------------------------------------------------------------------

    async def _step_install_frontend(self) -> None:
        step = 3
        frontend_dir = PROJECT_ROOT / "frontend"
        await self._emit("install_frontend", "running", "安装前端依赖...", step)

        node_modules = frontend_dir / "node_modules"
        if node_modules.exists() and (node_modules / ".package-lock.json").exists():
            await self._emit("install_frontend", "done", "前端依赖已安装，跳过", step)
            return

        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        rc = await self._run_stream(
            "install_frontend", step,
            npm_cmd, "install",
            cwd=str(frontend_dir),
        )
        if rc != 0:
            await self._emit("install_frontend", "error", "npm install 失败", step)
            raise RuntimeError("Frontend dependency installation failed")

        await self._emit("install_frontend", "done", "前端依赖安装完成", step)

    # ------------------------------------------------------------------
    # Step 4: Install GitNexus
    # ------------------------------------------------------------------

    async def _step_install_gitnexus(self) -> None:
        step = 4
        await self._emit("install_gitnexus", "running", "配置 GitNexus...", step)

        # On Windows asyncio.create_subprocess_exec cannot resolve .cmd shims;
        # use the explicit .cmd extension, same pattern as npm.cmd handling.
        gitnexus_cli = "gitnexus.cmd" if sys.platform == "win32" else "gitnexus"

        rc, stdout, _ = await self._run_capture(gitnexus_cli, "--version")
        if rc == 0 and stdout.strip():
            await self._emit("install_gitnexus", "done", f"GitNexus 已安装 (v{stdout.strip()})，跳过", step)
            self._config["_gitnexus_cmd"] = [gitnexus_cli]
            return

        await self._emit("install_gitnexus", "running", "尝试 npm install -g gitnexus...", step)
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        rc = await self._run_stream("install_gitnexus", step, npm_cmd, "install", "-g", "gitnexus")
        if rc == 0:
            rc2, stdout2, _ = await self._run_capture(gitnexus_cli, "--version")
            if rc2 == 0:
                await self._emit("install_gitnexus", "done", f"GitNexus 已通过 npm 安装 (v{stdout2.strip()})", step)
                self._config["_gitnexus_cmd"] = [gitnexus_cli]
                return

        vendor_entry = VENDOR_DIR / "gitnexus" / "dist" / "cli" / "index.js"
        if vendor_entry.exists():
            rc3, stdout3, _ = await self._run_capture("node", str(vendor_entry), "--version")
            if rc3 == 0:
                await self._emit(
                    "install_gitnexus", "done",
                    f"GitNexus 已从 vendor 加载 (v{stdout3.strip()})", step,
                )
                self._config["_gitnexus_cmd"] = ["node", str(vendor_entry)]
                return

        await self._emit(
            "install_gitnexus", "error",
            "GitNexus 不可用：npm 安装失败且 vendor/gitnexus 未找到", step,
        )
        raise RuntimeError("GitNexus installation failed")

    # ------------------------------------------------------------------
    # Step 5: Generate config files
    # ------------------------------------------------------------------

    async def _step_generate_config(self) -> None:
        step = 5
        await self._emit("generate_config", "running", "生成配置文件...", step)

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

        llm_base_url = cfg.get("llm_base_url", "")
        if llm_base_url:
            env_lines.extend([
                f"OPENAI_BASE_URL={llm_base_url}",
                f"OPENAI_API_KEY={cfg.get('llm_api_key', '')}",
                f"LLM_MODEL={cfg.get('llm_model', '')}",
                "FORCE_DIRECT=true",
                "TRUST_ENV=false",
            ])

        tiktoken_cache = VENDOR_DIR / "tiktoken_cache"
        if tiktoken_cache.exists():
            env_lines.append(f"TIKTOKEN_CACHE_DIR={tiktoken_cache}")

        backend_env = PROJECT_ROOT / "backend" / ".env"
        backend_env.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
        await self._emit("generate_config", "running", f"已写入 {backend_env}", step)

        frontend_env = PROJECT_ROOT / "frontend" / ".env.local"
        frontend_env.write_text(
            f"NEXT_PUBLIC_API_URL=http://localhost:{backend_port}\n",
            encoding="utf-8",
        )
        await self._emit("generate_config", "running", f"已写入 {frontend_env}", step)

        await self._emit("generate_config", "done", "配置文件生成完成", step)

    # ------------------------------------------------------------------
    # Step 6: Start services
    # ------------------------------------------------------------------

    async def _step_start_services(self) -> None:
        step = 6
        await self._emit("start_services", "running", "启动服务...", step)

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

        if cfg.get("install_gitnexus", True):
            _gn_default = ["gitnexus.cmd" if sys.platform == "win32" else "gitnexus"]
            gitnexus_cmd = cfg.get("_gitnexus_cmd", _gn_default)
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
        await self._emit("start_services", "done", "所有核心服务已启动", step)

    async def _start_process(
        self,
        name: str,
        cmd: list,
        cwd: str,
        step_name: str,
        step_index: int,
        env_extra: Optional[dict] = None,
    ) -> None:
        await self._emit(step_name, "running", f"正在启动 {name}...", step_index)

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
        await self._emit(step_name, "running", f"{name} 已启动（PID {proc.pid}）", step_index)

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
        await self._emit("health_check", "running", "等待服务就绪...", step)

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
                await self._emit("health_check", "done", "所有服务健康运行！", step)
                return

            await self._emit("health_check", "running", f"等待中...（{elapsed}s / {max_wait}s）", step)
            await asyncio.sleep(interval)
            elapsed += interval

        await self._emit("health_check", "error", "部分服务未能在规定时间内就绪", step)
        raise RuntimeError("Some services did not become healthy in time")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _run_stream(
        self, step_name: str, step_index: int, *cmd: str,
        cwd: str | None = None, timeout_seconds: int = 600,
    ) -> int:
        import time

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
        except FileNotFoundError:
            await self._emit(step_name, "error", f"命令未找到：{cmd[0]}", step_index)
            return 1

        start_time = time.monotonic()

        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            if self._stopped:
                proc.terminate()
                break
            if time.monotonic() - start_time > timeout_seconds:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                await self._emit(
                    step_name, "error",
                    f"进程超时（{timeout_seconds}s）：{cmd[0]}",
                    step_index,
                )
                return 1
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
            if name == "gitnexus" and not self._config.get("install_gitnexus", True):
                continue
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
