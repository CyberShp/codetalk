"""Native deployer -- installs deps and runs CodeTalk services as local processes.

No Docker required. Targets Windows intranet environments for black-box testers.
"""

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent
DEPLOYER_DIR = Path(__file__).parent.parent
VENDOR_DIR = DEPLOYER_DIR / "vendor"

DEEPWIKI_REPO = "https://github.com/AsyncFuncAI/deepwiki-open.git"
_CL100K_BPE = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"

TOTAL_STEPS = 7

_TIKTOKEN_COMMITTED = PROJECT_ROOT / "docker" / "deepwiki" / "tiktoken"
_TIKTOKEN_VENDOR = VENDOR_DIR / "tiktoken_cache"


def _best_tiktoken_cache() -> Optional[Path]:
    """Return the tiktoken cache dir with the most files, or None."""
    committed = _TIKTOKEN_COMMITTED if _TIKTOKEN_COMMITTED.is_dir() else None
    vendor = _TIKTOKEN_VENDOR if _TIKTOKEN_VENDOR.is_dir() else None
    if committed and vendor:
        c_count = sum(1 for _ in committed.iterdir())
        v_count = sum(1 for _ in vendor.iterdir())
        return vendor if v_count >= c_count else committed
    return committed or vendor

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
        self._start_args: dict[str, dict] = {}
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
            else:
                self._config.pop("deepwiki_path", None)
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

            if self._config.get("install_deepwiki", False) or bool(self._config.get("deepwiki_path", "")):
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
        total = 6

        await self._emit_sup("deepwiki_validate", "running", "验证 DeepWiki-Open 路径...", 1, total)
        if not dw_dir.exists():
            await self._emit_sup("deepwiki_validate", "running", f"目录不存在，正在克隆 DeepWiki-Open 到 {deepwiki_path}...", 1, total)
            dw_dir.parent.mkdir(parents=True, exist_ok=True)
            rc = await self._run_stream(
                "deepwiki_validate", 1,
                "git", "clone", "--depth", "1", DEEPWIKI_REPO, str(dw_dir),
            )
            if rc != 0:
                await self._emit_sup("deepwiki_validate", "error", "DeepWiki-Open 克隆失败，请检查网络或手动指定已克隆的目录", 1, total)
                raise RuntimeError("git clone deepwiki-open failed")
            await self._emit_sup("deepwiki_validate", "done", "DeepWiki-Open 克隆完成", 1, total)

        has_requirements = (dw_dir / "requirements.txt").exists()
        has_pyproject = (dw_dir / "api" / "pyproject.toml").exists()
        has_python_api = has_requirements or has_pyproject
        has_package_json = (dw_dir / "package.json").exists()
        if not has_python_api and not has_package_json:
            await self._emit_sup("deepwiki_validate", "error", "无效的 DeepWiki-Open 目录（缺少 requirements.txt/pyproject.toml 或 package.json）", 1, total)
            raise RuntimeError("Not a valid DeepWiki-Open directory")
        await self._emit_sup("deepwiki_validate", "done", "路径验证通过", 1, total)

        if has_python_api:
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

            if has_requirements:
                rc = await self._run_stream("deepwiki_python", 2, str(venv_pip), "install", "-r", str(dw_dir / "requirements.txt"))
                if rc != 0:
                    await self._emit_sup("deepwiki_python", "error", "pip install 失败", 2, total)
                    raise RuntimeError("DeepWiki pip install failed")
            else:
                import tomllib
                with open(dw_dir / "api" / "pyproject.toml", "rb") as _f:
                    _pyproject = tomllib.load(_f)
                _deps = _pyproject.get("tool", {}).get("poetry", {}).get("dependencies", {})
                _lines: list[str] = []
                for _name, _spec in _deps.items():
                    if _name == "python":
                        continue
                    if isinstance(_spec, str):
                        _lines.append(f"{_name}{_spec}")
                    elif isinstance(_spec, dict):
                        _extras = _spec.get("extras", [])
                        _version = _spec.get("version", "")
                        _extra_str = "[" + ",".join(_extras) + "]" if _extras else ""
                        _lines.append(f"{_name}{_extra_str}{_version}")
                _req_file = dw_dir / "api" / ".requirements.txt"
                _req_file.write_text("\n".join(_lines), encoding="utf-8")
                rc = await self._run_stream("deepwiki_python", 2, str(venv_pip), "install", "-r", str(_req_file))
                if rc != 0:
                    await self._emit_sup("deepwiki_python", "error", "pip install 失败", 2, total)
                    raise RuntimeError("DeepWiki pip install failed")
            compat_file = dw_dir / "api" / "api.py"
            if compat_file.exists():
                _content = compat_file.read_text(encoding="utf-8")
                if "add_websocket_route" in _content and "add_api_websocket_route" not in _content:
                    compat_file.write_text(
                        _content.replace("add_websocket_route", "add_api_websocket_route"),
                        encoding="utf-8",
                    )
                    await self._emit_sup("deepwiki_python", "running", "已修补 FastAPI 兼容性（add_websocket_route → add_api_websocket_route）", 2, total)
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

        if has_package_json and not (dw_dir / ".next").exists():
            await self._emit_sup("deepwiki_build", "running", "构建 DeepWiki 前端（next build）...", 4, total)
            npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
            rc = await self._run_stream("deepwiki_build", 4, npm_cmd, "run", "build", cwd=str(dw_dir))
            if rc != 0:
                await self._emit_sup("deepwiki_build", "error", "next build 失败", 4, total)
                raise RuntimeError("DeepWiki next build failed")
            await self._emit_sup("deepwiki_build", "done", "前端构建完成", 4, total)
        else:
            await self._emit_sup("deepwiki_build", "done", "前端已构建，跳过", 4, total)

        await self._emit_sup("deepwiki_start", "running", "启动 DeepWiki 服务...", 5, total)
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

        for _proc_name in ("deepwiki-api", "deepwiki-ui"):
            _old = self._processes.get(_proc_name)
            if _old is not None and _old.returncode is None:
                _old.terminate()
        await self._kill_port_processes([api_port, ui_port], 5)

        await self._start_deepwiki_processes(dw_dir, api_port, ui_port, llm_env, "deepwiki_start", 5)

        await self._emit_sup("deepwiki_start", "done", "DeepWiki 服务已启动", 5, total)

        self._config["deepwiki_path"] = deepwiki_path
        self._config["deepwiki_api_port"] = api_port
        self._config["deepwiki_ui_port"] = ui_port
        await self._step_generate_config()

        # Restart backend so it reloads .env with the new DeepWiki port settings.
        backend_proc = self._processes.get("backend")
        if backend_proc is not None and backend_proc.returncode is None:
            await self._emit_sup("deepwiki_start", "running", "重启后端以加载新 DeepWiki 配置...", 5, total)
            try:
                backend_proc.terminate()
                await asyncio.wait_for(backend_proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                backend_proc.kill()
            backend_dir = PROJECT_ROOT / "backend"
            backend_port = self._config.get("backend_port", 8100)
            venv_python = (
                backend_dir / ".venv311" / "Scripts" / "python.exe"
                if sys.platform == "win32"
                else backend_dir / ".venv311" / "bin" / "python"
            )
            await self._start_process(
                "backend",
                [str(venv_python), "-m", "uvicorn", "app.main:app",
                 "--host", "0.0.0.0", "--port", str(backend_port)],
                cwd=str(backend_dir),
                step_name="deepwiki_start",
                step_index=5,
            )
            await asyncio.sleep(5)
            await self._emit_sup("deepwiki_start", "running", "后端已重启并加载新配置", 5, total)

        await self._emit_sup("deepwiki_health", "running", "检查 DeepWiki 健康状态...", 6, total)
        await asyncio.sleep(5)

        import httpx
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                resp = await client.get(f"http://localhost:{api_port}/health")
                if resp.status_code < 500:
                    await self._emit_sup("deepwiki_health", "done", f"DeepWiki 健康运行（API:{api_port} UI:{ui_port}）", 6, total)
                else:
                    await self._emit_sup("deepwiki_health", "error", f"DeepWiki API 健康检查失败（HTTP {resp.status_code}）", 6, total)
                    raise RuntimeError(f"DeepWiki API unhealthy: HTTP {resp.status_code}")
        except RuntimeError:
            raise
        except Exception:
            await self._emit_sup("deepwiki_health", "error", f"DeepWiki API 未响应（端口 {api_port}），请检查日志", 6, total)
            raise RuntimeError(f"DeepWiki API health check failed: no response on port {api_port}")

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

    def _workspace_path(self) -> Path:
        ws = self._config.get("workspace_path", "./workspace")
        p = Path(ws)
        if not p.is_absolute():
            p = (PROJECT_ROOT / p).resolve()
        return p

    async def _step_install_gitnexus(self) -> None:
        step = 4
        await self._emit("install_gitnexus", "running", "配置 GitNexus...", step)

        workspace = self._workspace_path()
        gn_dir = workspace / "gitnexus"
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"

        if sys.platform == "win32":
            local_bin = gn_dir / "node_modules" / ".bin" / "gitnexus.cmd"
        else:
            local_bin = gn_dir / "node_modules" / ".bin" / "gitnexus"

        if local_bin.exists():
            rc, stdout, _ = await self._run_capture(str(local_bin), "--version")
            if rc == 0 and stdout.strip():
                await self._emit("install_gitnexus", "done", f"GitNexus 已安装于工作目录 (v{stdout.strip()})，跳过", step)
                self._config["_gitnexus_cmd"] = [str(local_bin)]
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

        gn_dir.mkdir(parents=True, exist_ok=True)
        await self._emit("install_gitnexus", "running", f"安装 GitNexus 到 {gn_dir}...", step)
        rc = await self._run_stream("install_gitnexus", step, npm_cmd, "install", "--prefix", str(gn_dir), "gitnexus")
        if rc == 0 and local_bin.exists():
            rc2, stdout2, _ = await self._run_capture(str(local_bin), "--version")
            if rc2 == 0:
                await self._emit("install_gitnexus", "done", f"GitNexus 已安装到工作目录 (v{stdout2.strip()})", step)
                self._config["_gitnexus_cmd"] = [str(local_bin)]
                return

        await self._emit(
            "install_gitnexus", "error",
            "GitNexus 不可用：本地安装失败且 vendor/gitnexus 未找到", step,
        )
        raise RuntimeError("GitNexus installation failed")

    # ------------------------------------------------------------------
    # Step 5: Generate config files
    # ------------------------------------------------------------------

    async def _step_generate_config(self) -> None:
        step = 5
        await self._emit("generate_config", "running", "生成配置文件...", step)

        cfg = self._config
        workspace = self._workspace_path()
        workspace.mkdir(parents=True, exist_ok=True)

        repos_dir = workspace / "repos"
        repos_dir.mkdir(parents=True, exist_ok=True)
        cfg["repos_path"] = str(repos_dir)

        backend_port = cfg.get("backend_port", 8100)
        frontend_port = cfg.get("frontend_port", 3005)
        gitnexus_port = cfg.get("gitnexus_port", 7100)
        deepwiki_api_port = cfg.get("deepwiki_api_port", 8091)
        deepwiki_ui_port = cfg.get("deepwiki_ui_port", 3001)
        deepwiki_path = cfg.get("deepwiki_path", "")

        env_lines = [
            "DATA_DIR=data",
            "SQLITE_DB=data/codetalk.db",
            f"REPOS_BASE_PATH={repos_dir}",
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

        tiktoken_cache = _best_tiktoken_cache()
        if tiktoken_cache is not None:
            env_lines.append(f"TIKTOKEN_CACHE_DIR={tiktoken_cache.resolve()}")

        backend_env = PROJECT_ROOT / "backend" / ".env"
        backend_env.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
        await self._emit("generate_config", "running", f"已写入 {backend_env}", step)

        frontend_env = PROJECT_ROOT / "frontend" / ".env.local"
        frontend_env.write_text(
            f"NEXT_PUBLIC_API_URL=http://localhost:{backend_port}\n",
            encoding="utf-8",
        )
        await self._emit("generate_config", "running", f"已写入 {frontend_env}", step)

        if deepwiki_path and tiktoken_cache is not None:
            dw_dir = Path(deepwiki_path)
            if not dw_dir.is_dir():
                await self._emit("generate_config", "running",
                                 f"deepwiki_path 不存在，跳过写入 TIKTOKEN_CACHE_DIR：{deepwiki_path}", step)
            else:
                dw_env = dw_dir / ".env"
                existing = dw_env.read_text(encoding="utf-8").splitlines() if dw_env.exists() else []
                kept = [ln for ln in existing if not ln.startswith("TIKTOKEN_CACHE_DIR=")]
                kept.append(f"TIKTOKEN_CACHE_DIR={tiktoken_cache.resolve()}")
                dw_env.write_text("\n".join(kept) + "\n", encoding="utf-8")
                await self._emit("generate_config", "running", f"已写入 {dw_env}", step)

        await self._emit("generate_config", "done", "配置文件生成完成", step)

    # ------------------------------------------------------------------
    # Step 6: Start services
    # ------------------------------------------------------------------

    async def _kill_port_processes(self, ports: list[int], step: int) -> None:
        """Kill any processes listening on the given ports before starting services."""
        own_pid = os.getpid()
        for port in ports:
            if sys.platform == "win32":
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "netstat", "-ano", "-p", "TCP",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                    for line in stdout.decode(errors="replace").splitlines():
                        if f":{port} " in line and "LISTENING" in line:
                            parts = line.split()
                            pid_str = parts[-1]
                            if pid_str.isdigit() and int(pid_str) != own_pid:
                                proc_name = pid_str
                                try:
                                    name_proc = await asyncio.create_subprocess_exec(
                                        "tasklist", "/FI", f"PID eq {pid_str}", "/FO", "CSV", "/NH",
                                        stdout=asyncio.subprocess.PIPE,
                                        stderr=asyncio.subprocess.DEVNULL,
                                    )
                                    name_out, _ = await asyncio.wait_for(name_proc.communicate(), timeout=5)
                                    first_line = name_out.decode(errors="replace").strip().splitlines()[0]
                                    proc_name = first_line.split(",")[0].strip('"') or pid_str
                                except Exception:
                                    pass
                                kill_proc = await asyncio.create_subprocess_exec(
                                    "taskkill", "/F", "/PID", pid_str,
                                    stdout=asyncio.subprocess.DEVNULL,
                                    stderr=asyncio.subprocess.DEVNULL,
                                )
                                await asyncio.wait_for(kill_proc.wait(), timeout=5)
                                await self._emit("start_services", "running", f"已释放端口 {port}（PID {pid_str}, {proc_name}）", step)
                except (asyncio.TimeoutError, Exception):
                    pass
            else:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "lsof", "-ti", f":{port}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                    for pid_str in stdout.decode(errors="replace").split():
                        if pid_str.isdigit() and int(pid_str) != own_pid:
                            proc_name = pid_str
                            try:
                                name_proc = await asyncio.create_subprocess_exec(
                                    "ps", "-p", pid_str, "-o", "comm=",
                                    stdout=asyncio.subprocess.PIPE,
                                    stderr=asyncio.subprocess.DEVNULL,
                                )
                                name_out, _ = await asyncio.wait_for(name_proc.communicate(), timeout=5)
                                proc_name = name_out.decode(errors="replace").strip() or pid_str
                            except Exception:
                                pass
                            kill_proc = await asyncio.create_subprocess_exec(
                                "kill", "-9", pid_str,
                                stdout=asyncio.subprocess.DEVNULL,
                                stderr=asyncio.subprocess.DEVNULL,
                            )
                            await asyncio.wait_for(kill_proc.wait(), timeout=5)
                            await self._emit("start_services", "running", f"已释放端口 {port}（PID {pid_str}, {proc_name}）", step)
                except (asyncio.TimeoutError, Exception):
                    pass

    async def _step_start_services(self) -> None:
        step = 6
        await self._emit("start_services", "running", "启动服务...", step)

        cfg = self._config
        backend_port = cfg.get("backend_port", 8100)
        frontend_port = cfg.get("frontend_port", 3005)
        gitnexus_port = cfg.get("gitnexus_port", 7100)

        ports_to_clear = [backend_port, frontend_port]
        if cfg.get("install_gitnexus", True):
            ports_to_clear.append(gitnexus_port)
        await self._emit("start_services", "running", f"清理占用端口 {ports_to_clear}...", step)
        await self._kill_port_processes(ports_to_clear, step)
        await asyncio.sleep(1)

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

        deepwiki_path = self._config.get("deepwiki_path", "")
        if self._config.get("install_deepwiki", False) or deepwiki_path:
            dw_dir = Path(deepwiki_path) if deepwiki_path else None
            _existing_api = self._processes.get("deepwiki-api")
            if _existing_api is not None and _existing_api.returncode is None:
                await self._emit("start_services", "running", "DeepWiki 已在运行，跳过重复启动", step)
            elif dw_dir and dw_dir.exists():
                dw_api_port = self._config_port("deepwiki_api_port", 8091)
                dw_ui_port = self._config_port("deepwiki_ui_port", 3001)
                llm_env: dict[str, str] = {
                    "DEEPWIKI_EMBEDDER_TYPE": self._config.get("deepwiki_embedder_type", "openai"),
                }
                llm_base_url = self._config.get("llm_base_url", "")
                if llm_base_url:
                    llm_env.update({
                        "OPENAI_BASE_URL": llm_base_url,
                        "OPENAI_API_KEY": self._config.get("llm_api_key", ""),
                        "LLM_MODEL": self._config.get("llm_model", ""),
                        "FORCE_DIRECT": "true",
                        "TRUST_ENV": "false",
                    })
                await self._kill_port_processes([dw_api_port, dw_ui_port], step)
                await self._start_deepwiki_processes(dw_dir, dw_api_port, dw_ui_port, llm_env, "start_services", step)
            elif dw_dir:
                await self._emit("start_services", "error", f"DeepWiki 路径无效：{dw_dir} 不存在", step)
                raise RuntimeError(f"DeepWiki path does not exist: {dw_dir}")

        await self._emit("start_services", "done", "所有核心服务已启动", step)

    async def _start_deepwiki_processes(
        self,
        dw_dir: "Path",
        api_port: int,
        ui_port: int,
        llm_env: dict,
        step_name: str,
        step_index: int,
    ) -> None:
        tiktoken_cache = _best_tiktoken_cache()
        if tiktoken_cache is not None:
            llm_env["TIKTOKEN_CACHE_DIR"] = str(tiktoken_cache.resolve())
        has_python_api = (dw_dir / "api" / "pyproject.toml").exists() or (dw_dir / "requirements.txt").exists()
        has_package_json = (dw_dir / "package.json").exists()

        if has_python_api:
            venv_dir = dw_dir / ".venv"
            venv_python = (venv_dir / "Scripts" / "python.exe") if sys.platform == "win32" else (venv_dir / "bin" / "python")
            await self._start_process(
                "deepwiki-api",
                [str(venv_python), "-m", "uvicorn", "api.api:app", "--host", "0.0.0.0", "--port", str(api_port)],
                cwd=str(dw_dir),
                step_name=step_name,
                step_index=step_index,
                env_extra=llm_env or None,
            )

        if has_package_json:
            standalone_server = dw_dir / ".next" / "standalone" / "server.js"
            if standalone_server.exists():
                ui_cmd = ["node", str(standalone_server)]
            else:
                npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
                ui_cmd = [npm_cmd, "run", "start"]
            await self._start_process(
                "deepwiki-ui",
                ui_cmd,
                cwd=str(dw_dir),
                step_name=step_name,
                step_index=step_index,
                env_extra={"PORT": str(ui_port), "SERVER_BASE_URL": f"http://localhost:{api_port}", **llm_env},
            )

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

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=env,
            )
        except FileNotFoundError:
            await self._emit(step_name, "error", f"命令未找到：{cmd[0]}（尝试 shell 模式）", step_index)
            proc = await asyncio.create_subprocess_shell(
                " ".join(cmd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=env,
            )
        self._processes[name] = proc
        self._start_args[name] = {"cmd": cmd, "cwd": cwd, "env_extra": env_extra}

        asyncio.ensure_future(self._drain_output(name, proc, step_name, step_index))
        await self._emit(step_name, "running", f"{name} 已启动（PID {proc.pid}）", step_index)
        await asyncio.sleep(2)
        if proc.returncode is not None:
            await self._emit(step_name, "error", f"{name} 启动后立即退出（退出码 {proc.returncode}）", step_index)
            raise RuntimeError(f"{name} exited immediately with code {proc.returncode}")

    async def restart_service(self, name: str) -> dict:
        """Restart a named service (or deepwiki pair) using stored startup args."""
        targets = ["deepwiki-api", "deepwiki-ui"] if name == "deepwiki" else [name]
        for t in targets:
            if t not in self._start_args:
                defaults = self._default_start_args(t)
                if defaults:
                    self._start_args[t] = defaults
        missing = [t for t in targets if t not in self._start_args]
        if missing:
            raise KeyError(f"Service not started by this deployer: {', '.join(missing)}")

        for target in targets:
            proc = self._processes.get(target)
            if proc is not None and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass

        for target in targets:
            args = self._start_args[target]
            env = os.environ.copy()
            if args.get("env_extra"):
                env.update(args["env_extra"])
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args["cmd"],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=args["cwd"],
                    env=env,
                )
            except FileNotFoundError:
                proc = await asyncio.create_subprocess_shell(
                    " ".join(args["cmd"]),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=args["cwd"],
                    env=env,
                )
            self._processes[target] = proc
            asyncio.ensure_future(self._drain_output(target, proc, "restart", 0))
            await asyncio.sleep(2)
            if proc.returncode is not None:
                raise RuntimeError(
                    f"{target} exited immediately after restart (code {proc.returncode})"
                )

        return {"ok": True, "service": name}

    async def stop_service(self, name: str) -> dict:
        """Stop a named service (or deepwiki pair)."""
        targets = ["deepwiki-api", "deepwiki-ui"] if name == "deepwiki" else [name]
        missing = [t for t in targets if t not in self._start_args]
        if missing:
            raise KeyError(f"Service not started by this deployer: {', '.join(missing)}")

        for target in targets:
            proc = self._processes.get(target)
            if proc is not None and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass

        return {"ok": True, "service": name, "action": "stopped"}

    def _default_start_args(self, name: str) -> dict | None:
        """Reconstruct startup args for known services from config."""
        cfg = self._config
        if name == "backend":
            backend_dir = PROJECT_ROOT / "backend"
            venv_python = backend_dir / (".venv311/Scripts/python.exe" if sys.platform == "win32" else ".venv311/bin/python")
            return {
                "cmd": [str(venv_python), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(cfg.get("backend_port", 8100))],
                "cwd": str(backend_dir),
                "env_extra": None,
            }
        if name == "frontend":
            npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
            return {
                "cmd": [npm_cmd, "run", "dev"],
                "cwd": str(PROJECT_ROOT / "frontend"),
                "env_extra": {"PORT": str(cfg.get("frontend_port", 3005))},
            }
        if name == "gitnexus":
            gn_cmd = cfg.get("_gitnexus_cmd", ["gitnexus.cmd" if sys.platform == "win32" else "gitnexus"])
            return {
                "cmd": [*gn_cmd, "serve", "--port", str(cfg.get("gitnexus_port", 7100)), "--host", "0.0.0.0"],
                "cwd": str(PROJECT_ROOT),
                "env_extra": None,
            }
        return None

    async def start_service(self, name: str) -> dict:
        """Start a previously-stopped service using stored startup args."""
        targets = ["deepwiki-api", "deepwiki-ui"] if name == "deepwiki" else [name]
        for t in targets:
            if t not in self._start_args:
                defaults = self._default_start_args(t)
                if defaults:
                    self._start_args[t] = defaults
        missing = [t for t in targets if t not in self._start_args]
        if missing:
            raise KeyError(f"Service not started by this deployer: {', '.join(missing)}")

        for target in targets:
            proc = self._processes.get(target)
            if proc is not None and proc.returncode is None:
                continue

            args = self._start_args[target]
            env = os.environ.copy()
            if args.get("env_extra"):
                env.update(args["env_extra"])
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args["cmd"],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=args["cwd"],
                    env=env,
                )
            except FileNotFoundError:
                proc = await asyncio.create_subprocess_shell(
                    " ".join(args["cmd"]),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=args["cwd"],
                    env=env,
                )
            self._processes[target] = proc
            asyncio.ensure_future(self._drain_output(target, proc, "start", 0))
            await asyncio.sleep(2)
            if proc.returncode is not None:
                raise RuntimeError(
                    f"{target} exited immediately after start (code {proc.returncode})"
                )

        return {"ok": True, "service": name, "action": "started"}

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

            if self._config.get("install_deepwiki", False) or bool(self._config.get("deepwiki_path", "")):
                dw_port = self._config_port("deepwiki_api_port", 8091)
                try:
                    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                        resp = await client.get(f"http://localhost:{dw_port}/health")
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
        env_extra: dict | None = None,
    ) -> int:
        import time

        env = None
        if env_extra:
            env = os.environ.copy()
            env.update(env_extra)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=env,
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
