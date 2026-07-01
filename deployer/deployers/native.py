"""Native deployer -- installs deps and runs CodeTalk services as local processes.

No Docker required. Targets Windows intranet environments for black-box testers.
"""

import asyncio
import hashlib
import os
import re
import signal
import shutil
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent
DEPLOYER_DIR = Path(__file__).parent.parent
VENDOR_DIR = DEPLOYER_DIR / "vendor"

# Make the deployer/ root importable so cgc_launcher can be found.
if str(DEPLOYER_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYER_DIR))

from checks import _format_port_unavailable_message, _probe_port_bind  # noqa: E402

try:
    import cgc_launcher as _cgc  # noqa: E402
    _CGC_DEFAULT_PORT: int = _cgc.CGC_DEFAULT_PORT
except ImportError:  # safety net — shouldn't happen in normal deployment
    _cgc = None  # type: ignore[assignment]
    _CGC_DEFAULT_PORT = 7072

TOTAL_STEPS = 7
REMOVED_LEGACY_ENV_PREFIXES = ("DEEPWIKI_",)
REMOVED_LEGACY_ENV_KEYS = {
    "DEEPWIKI_PATH",
    "DEEPWIKI_API_PORT",
    "DEEPWIKI_UI_PORT",
    "DEEPWIKI_BASE_URL",
    "DEEPWIKI_API_URL",
    "DEEPWIKI_EMBEDDING_BASE_URL",
    "DEEPWIKI_EMBEDDING_API_KEY",
    "DEEPWIKI_EMBEDDING_MODEL",
}

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(authorization\s*:\s*bearer\s+)([^\s\"']+)"),
    re.compile(r"(?i)\b(api[-_ ]?key|token|secret|password)\s*=\s*([^\s\"']+)"),
)

SERVICE_DEFAULTS = [
    ("backend", "backend_port", 3004, "http", "/health"),
    ("frontend", "frontend_port", 3003, "http", "/"),
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
            await self._step_generate_config()
            if self._stopped:
                return
            await self._step_install_frontend()
            if self._stopped:
                return
            if self._config.get("install_gitnexus", True):
                await self._step_install_optional_gitnexus()
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
        for name in list(self._processes):
            await self._terminate_process(name)

    async def check_health(self) -> list:
        import httpx
        results = []
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            for name, port, _kind, path in self._service_targets():
                try:
                    url = f"http://localhost:{port}{path}"
                    resp = await client.get(url)
                    if 200 <= resp.status_code < 400:
                        results.append({"name": name, "healthy": True, "message": f"HTTP {resp.status_code}"})
                    else:
                        results.append({"name": name, "healthy": False, "message": f"HTTP {resp.status_code}"})
                except Exception as exc:
                    results.append({"name": name, "healthy": False, "message": str(exc)})

            if self._config.get("install_cgc", True) and self._processes.get("cgc") is not None:
                cgc_port = self._config_port("cgc_port", _CGC_DEFAULT_PORT)
                try:
                    resp = await client.get(f"http://localhost:{cgc_port}/api/v1/status")
                    healthy = 200 <= resp.status_code < 400
                    results.append({"name": "cgc", "healthy": healthy, "message": f"HTTP {resp.status_code}"})
                except Exception as exc:
                    results.append({"name": "cgc", "healthy": False, "message": str(exc)})


        return results


    async def _step_check_env(self) -> None:
        step = 1
        await self._emit("check_env", "running", "检查运行环境...", step)

        py_ok = await self._check_command(sys.executable, ["--version"], "3.10")
        for candidate in ("python", "python3", "python3.12", "python3.11", "python3.10"):
            if py_ok:
                break
            py_ok = await self._check_command(candidate, ["--version"], "3.10")
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
            rc = await self._run_stream("install_backend", step, sys.executable, "-m", "venv", str(venv_dir))
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
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"

        await self._emit("install_frontend", "running", "安装前端依赖...", step)

        node_modules = frontend_dir / "node_modules"
        if not (node_modules.exists() and (node_modules / ".package-lock.json").exists()):
            rc = await self._run_stream("install_frontend", step, npm_cmd, "install", cwd=str(frontend_dir))
            if rc != 0:
                await self._emit("install_frontend", "error", "npm install 失败", step)
                raise RuntimeError("Frontend dependency installation failed")

        next_build_dir = frontend_dir / ".next"
        if next_build_dir.exists():
            build_key = await self._frontend_build_key(frontend_dir)
            marker = next_build_dir / ".codetalk-git-hash"
            try:
                prev_key = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
            except OSError:
                prev_key = ""

            if prev_key == build_key:
                await self._emit("install_frontend", "done",
                                 "前端依赖已安装，构建产物与当前代码和端口配置一致，跳过构建", step)
                return

            await self._emit(
                "install_frontend", "running",
                "检测到代码或前端端口配置已变更，清理旧构建...",
                step,
            )
            shutil.rmtree(next_build_dir, ignore_errors=True)

        await self._emit("install_frontend", "running", "构建前端（npm run build）...", step)
        rc = await self._run_stream("install_frontend", step, npm_cmd, "run", "build", cwd=str(frontend_dir))
        if rc != 0:
            await self._emit("install_frontend", "error", "npm run build 失败", step)
            raise RuntimeError("Frontend build failed")

        try:
            (next_build_dir / ".codetalk-git-hash").write_text(
                await self._frontend_build_key(frontend_dir),
                encoding="utf-8",
            )
        except OSError as exc:
            await self._emit("install_frontend", "running", f"（警告）写入前端构建标记失败：{exc}", step)

        await self._emit("install_frontend", "done", "前端依赖安装并构建完成", step)

    # ------------------------------------------------------------------
    # Step 4: Install GitNexus
    # ------------------------------------------------------------------

    async def _frontend_build_key(self, frontend_dir: Path) -> str:
        git_hash = await self._get_git_hash(PROJECT_ROOT) or "nogit"
        env_file = frontend_dir / ".env.local"
        try:
            env_text = env_file.read_text(encoding="utf-8")
        except OSError:
            env_text = ""
        source_hash = await asyncio.to_thread(_frontend_source_fingerprint, frontend_dir)
        return f"{git_hash}\n{source_hash}\n{env_text.strip()}"

    def _resolve_cgc_cmd(self) -> list[str] | None:
        """Resolve the cgc startup command, or None if no usable venv is found."""
        if _cgc is not None:
            return _cgc.resolve_cgc_cmd(self._config)
        # Fallback: probe the default venv location directly.
        scripts = "Scripts" if sys.platform == "win32" else "bin"
        python_name = "python.exe" if sys.platform == "win32" else "python"
        for venv_name in ("cgc-venv", "cgc-venv-throwaway"):
            python_exe = PROJECT_ROOT.parent / venv_name / scripts / python_name
            if python_exe.exists():
                return [str(python_exe), "-m", "codegraphcontext"]
        return None

    def _cgc_cwd(self) -> str:
        """Working directory for cgc (avoids GBK codec error from non-ASCII .env)."""
        if _cgc is not None:
            return _cgc.cgc_cwd()
        import os as _os
        cwd = Path(_os.path.expanduser("~")) / ".codegraphcontext"
        cwd.mkdir(parents=True, exist_ok=True)
        return str(cwd)

    def _resolve_gitnexus_cmd(self) -> list[str]:
        """Resolve the gitnexus binary path by probing install locations."""
        if cached := self._config.get("_gitnexus_cmd"):
            if isinstance(cached, list) and cached:
                return cached

        workspace = self._workspace_path()
        gn_dir = workspace / "gitnexus"
        if sys.platform == "win32":
            local_bin = gn_dir / "node_modules" / ".bin" / "gitnexus.cmd"
        else:
            local_bin = gn_dir / "node_modules" / ".bin" / "gitnexus"

        if local_bin.exists():
            cmd = [str(local_bin)]
            self._config["_gitnexus_cmd"] = cmd
            print(f"[deployer] Resolved gitnexus binary: {local_bin}")
            return cmd

        vendor_entry = VENDOR_DIR / "gitnexus" / "dist" / "cli" / "index.js"
        if vendor_entry.exists():
            cmd = ["node", str(vendor_entry)]
            self._config["_gitnexus_cmd"] = cmd
            print(f"[deployer] Resolved gitnexus binary: vendor ({vendor_entry})")
            return cmd

        fallback = "gitnexus.cmd" if sys.platform == "win32" else "gitnexus"
        print(f"[deployer] WARNING: No local/vendor gitnexus found, falling back to PATH: {fallback}")
        return [fallback]

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

    async def _step_install_optional_gitnexus(self) -> None:
        """Install GitNexus when available, but do not block core CodeTalk startup."""
        try:
            await self._step_install_gitnexus()
        except Exception as exc:
            self._config["install_gitnexus"] = False
            await self._emit(
                "install_gitnexus",
                "running",
                f"GitNexus 安装已跳过：{exc}。核心 backend/frontend 将继续部署；需要代码图谱增强时再补充安装 GitNexus。",
                4,
            )

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

        backend_port = cfg.get("backend_port", 3004)
        frontend_port = cfg.get("frontend_port", 3003)
        gitnexus_port = cfg.get("gitnexus_port", 7100)

        cgc_port = self._config_port("cgc_port", _CGC_DEFAULT_PORT)
        env_lines = [
            "DATA_DIR=data",
            "SQLITE_DB=data/codetalk.db",
            f"REPOS_BASE_PATH={repos_dir}",
            f"GITNEXUS_BASE_URL=http://localhost:{gitnexus_port}",
            f"GITNEXUS_PORT={gitnexus_port}",
            "GITNEXUS_BIN=gitnexus",
            f"CGC_BASE_URL=http://localhost:{cgc_port}",
            f"CGC_PORT={cgc_port}",
            f"CORS_ORIGINS=http://localhost:{frontend_port},http://127.0.0.1:{frontend_port}",
            "TOOL_HEALTH_INTERVAL=30",
        ]

        backend_env = PROJECT_ROOT / "backend" / ".env"
        known_keys = {ln.split("=", 1)[0] for ln in env_lines if "=" in ln}
        existing_lines = backend_env.read_text(encoding="utf-8").splitlines() if backend_env.exists() else []
        kept = [
            ln for ln in existing_lines
            if _keep_existing_backend_env_line(ln, known_keys)
        ]
        backend_env.write_text("\n".join(kept + env_lines) + "\n", encoding="utf-8")
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

    async def _scan_port_conflicts(self, ports: list[int]) -> list[dict]:
        """Scan ports for conflicts without killing anything.

        Returns a list of dicts: {port, pid, process_name, is_own}.
        is_own=True means the process is tracked in _processes (our child).
        """
        own_pids: set[int] = {
            proc.pid
            for proc in self._processes.values()
            if proc.returncode is None and proc.pid is not None
        }
        conflicts: list[dict] = []
        own_pid = os.getpid()
        for port in ports:
            found_listener = False
            if sys.platform == "win32":
                try:
                    scan = await asyncio.create_subprocess_exec(
                        "netstat", "-ano", "-p", "TCP",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    stdout, _ = await asyncio.wait_for(scan.communicate(), timeout=10)
                    for line in stdout.decode(errors="replace").splitlines():
                        if f":{port} " not in line or "LISTENING" not in line:
                            continue
                        parts = line.split()
                        pid_str = parts[-1]
                        if not pid_str.isdigit():
                            continue
                        pid = int(pid_str)
                        if pid == own_pid:
                            continue
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
                        found_listener = True
                        conflicts.append({
                            "port": port,
                            "pid": pid,
                            "process_name": proc_name,
                            "is_own": pid in own_pids,
                        })
                except (asyncio.TimeoutError, Exception):
                    pass
            else:
                try:
                    scan = await asyncio.create_subprocess_exec(
                        "lsof", "-ti", f":{port}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    stdout, _ = await asyncio.wait_for(scan.communicate(), timeout=10)
                    for pid_str in stdout.decode(errors="replace").split():
                        if not pid_str.isdigit():
                            continue
                        pid = int(pid_str)
                        if pid == own_pid:
                            continue
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
                        found_listener = True
                        conflicts.append({
                            "port": port,
                            "pid": pid,
                            "process_name": proc_name,
                            "is_own": pid in own_pids,
                        })
                except (asyncio.TimeoutError, Exception):
                    pass
            if not found_listener:
                probe = _probe_port_bind(port)
                if not probe["available"]:
                    conflicts.append({
                        "port": port,
                        "pid": None,
                        "process_name": "unavailable",
                        "is_own": False,
                        "reason": probe.get("reason", "unavailable"),
                        "message": _format_port_unavailable_message(port, probe),
                    })
        return conflicts

    async def _release_ports(self, ports: list[int], step: int, force_takeover: bool = False) -> None:
        """Release ports before starting services.

        Kills processes we own (tracked in _processes). Skips unknown processes
        unless force_takeover=True, in which case any occupant is killed.
        """
        own_pid = os.getpid()
        own_pids: set[int] = {
            proc.pid
            for proc in self._processes.values()
            if proc.returncode is None and proc.pid is not None
        }
        for port in ports:
            if sys.platform == "win32":
                try:
                    scan = await asyncio.create_subprocess_exec(
                        "netstat", "-ano", "-p", "TCP",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    stdout, _ = await asyncio.wait_for(scan.communicate(), timeout=10)
                    for line in stdout.decode(errors="replace").splitlines():
                        if f":{port} " not in line or "LISTENING" not in line:
                            continue
                        parts = line.split()
                        pid_str = parts[-1]
                        if not pid_str.isdigit():
                            continue
                        pid = int(pid_str)
                        if pid == own_pid:
                            continue
                        if pid not in own_pids and not force_takeover:
                            await self._emit("start_services", "running",
                                             f"端口 {port} 被未知进程占用（PID {pid}），跳过释放", step)
                            continue
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
                        await self._emit("start_services", "running",
                                         f"已释放端口 {port}（PID {pid_str}, {proc_name}）", step)
                except (asyncio.TimeoutError, Exception):
                    pass
            else:
                try:
                    scan = await asyncio.create_subprocess_exec(
                        "lsof", "-ti", f":{port}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    stdout, _ = await asyncio.wait_for(scan.communicate(), timeout=10)
                    for pid_str in stdout.decode(errors="replace").split():
                        if not pid_str.isdigit():
                            continue
                        pid = int(pid_str)
                        if pid == own_pid:
                            continue
                        if pid not in own_pids and not force_takeover:
                            await self._emit("start_services", "running",
                                             f"端口 {port} 被未知进程占用（PID {pid}），跳过释放", step)
                            continue
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
                        await self._emit("start_services", "running",
                                         f"已释放端口 {port}（PID {pid_str}, {proc_name}）", step)
                except (asyncio.TimeoutError, Exception):
                    pass

    async def _step_start_services(self) -> None:
        step = 6
        await self._emit("start_services", "running", "启动服务...", step)

        cfg = self._config
        backend_port = cfg.get("backend_port", 3004)
        frontend_port = cfg.get("frontend_port", 3003)
        gitnexus_port = cfg.get("gitnexus_port", 7100)

        ports_to_clear = [backend_port, frontend_port]
        if cfg.get("install_gitnexus", True):
            ports_to_clear.append(gitnexus_port)
        await self._emit("start_services", "running", f"清理占用端口 {ports_to_clear}...", step)
        force_takeover = bool(cfg.get("force_takeover", False))
        await self._release_ports(ports_to_clear, step, force_takeover=force_takeover)
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
            gitnexus_cmd = self._resolve_gitnexus_cmd()
            await self._start_process(
                "gitnexus",
                [*gitnexus_cmd, "serve", "--port", str(gitnexus_port), "--host", "0.0.0.0"],
                cwd=str(PROJECT_ROOT),
                step_name="start_services",
                step_index=step,
            )

        if cfg.get("install_cgc", True):
            try:
                await self._ensure_cgc(step)
                cgc_cmd = self._resolve_cgc_cmd()
                if cgc_cmd:
                    cgc_port = self._config_port("cgc_port", _CGC_DEFAULT_PORT)
                    await self._release_ports([cgc_port], step, force_takeover=force_takeover)
                    # Tell CGC which paths it may index; otherwise its path guard rejects
                    # repos outside the CGC cwd.
                    _cgc_allowed_roots = ";".join([
                        str(PROJECT_ROOT.parent),
                        os.path.expanduser("~"),
                    ])
                    await self._start_process(
                        "cgc",
                        [*cgc_cmd, "api", "start", "--host", "127.0.0.1", "--port", str(cgc_port)],
                        cwd=self._cgc_cwd(),
                        step_name="start_services",
                        step_index=step,
                        env_extra={"CGC_ALLOWED_ROOTS": _cgc_allowed_roots},
                    )
                else:
                    await self._emit(
                        "start_services", "running",
                        "CGC 启动已跳过：Python 解释器未找到（安装尝试后仍缺失或路径未配置）。"
                        "请确认 cgc-venv 存在且包含有效的 python.exe，或在部署配置中设置 cgcVenvPath。",
                        step,
                    )
            except Exception as exc:
                self._processes.pop("cgc", None)
                self._start_args.pop("cgc", None)
                await self._emit(
                    "start_services",
                    "running",
                    f"CGC 启动已跳过：{exc}。核心服务将继续启动；需要调用链/符号图能力时再修复 CGC 配置。",
                    step,
                )

        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        next_build_dir = frontend_dir / ".next"
        dev_mode = bool(cfg.get("dev_mode", False))
        # Standalone output mode was removed (see frontend/next.config.ts comment).
        # Always run via `next start` from `.next/`, or `next dev` if explicitly requested.
        if next_build_dir.exists():
            frontend_start_cmd = [npm_cmd, "run", "start"]
        elif dev_mode:
            await self._emit("start_services", "running", "以开发模式启动前端（dev_mode=true）", step)
            frontend_start_cmd = [npm_cmd, "run", "dev"]
        else:
            raise RuntimeError("needs_build: 未找到前端构建产物（.next/），请先运行 npm run build")
        await self._start_process(
            "frontend",
            frontend_start_cmd,
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
        await self._spawn_process(name, cmd, cwd, step_name, step_index, env_extra)

    async def _spawn_process(
        self,
        name: str,
        cmd: list,
        cwd: str,
        step_name: str,
        step_index: int,
        env_extra: Optional[dict] = None,
    ) -> None:
        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)
        try:
            create_kwargs = {}
            if sys.platform != "win32":
                create_kwargs["start_new_session"] = True
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=env,
                **create_kwargs,
            )
        except FileNotFoundError as exc:
            await self._emit(step_name, "error", f"命令未找到：{cmd[0]}", step_index)
            raise RuntimeError(f"Command not found: {cmd[0]}") from exc
        self._processes[name] = proc
        self._start_args[name] = {"cmd": cmd, "cwd": cwd, "env_extra": env_extra}
        asyncio.ensure_future(self._drain_output(name, proc, step_name, step_index))
        await self._emit(step_name, "running", f"{name} 已启动（PID {proc.pid}）", step_index)
        await asyncio.sleep(2)
        if proc.returncode is not None:
            await self._emit(step_name, "error", f"{name} 启动后立即退出（退出码 {proc.returncode}）", step_index)
            raise RuntimeError(f"{name} exited immediately with code {proc.returncode}")

    async def _terminate_process(self, name: str, timeout: float = 5) -> None:
        proc = self._processes.get(name)
        if proc is None or proc.returncode is not None:
            return
        if sys.platform == "win32":
            try:
                kill_tree = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/T",
                    "/F",
                    "/PID",
                    str(proc.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(kill_tree.wait(), timeout=timeout)
                await asyncio.wait_for(proc.wait(), timeout=timeout)
                return
            except (ProcessLookupError, asyncio.TimeoutError, FileNotFoundError):
                pass
        try:
            if sys.platform != "win32" and proc.pid is not None:
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                if sys.platform != "win32" and proc.pid is not None:
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except ProcessLookupError:
                pass

    async def restart_service(self, name: str) -> dict:
        """Restart a named service using stored startup args."""
        targets = [name]
        for t in targets:
            if t not in self._start_args:
                defaults = self._default_start_args(t)
                if defaults:
                    self._start_args[t] = defaults
        missing = [t for t in targets if t not in self._start_args]
        if missing:
            raise KeyError(f"Service not started by this deployer: {', '.join(missing)}")

        for target in targets:
            await self._terminate_process(target)

        if "frontend" in targets:
            await self._step_install_frontend()

        for target in targets:
            args = self._start_args[target]
            await self._spawn_process(target, args["cmd"], args["cwd"], "restart", 0, args.get("env_extra"))

        return {"ok": True, "service": name}

    async def stop_service(self, name: str) -> dict:
        """Stop a named service."""
        targets = [name]
        missing = [t for t in targets if t not in self._start_args]
        if missing:
            raise KeyError(f"Service not started by this deployer: {', '.join(missing)}")

        for target in targets:
            await self._terminate_process(target)

        return {"ok": True, "service": name, "action": "stopped"}

    def _default_start_args(self, name: str) -> dict | None:
        """Reconstruct startup args for known services from config."""
        cfg = self._config
        if name == "backend":
            backend_dir = PROJECT_ROOT / "backend"
            venv_python = backend_dir / (".venv311/Scripts/python.exe" if sys.platform == "win32" else ".venv311/bin/python")
            return {
                "cmd": [str(venv_python), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(cfg.get("backend_port", 3004))],
                "cwd": str(backend_dir),
                "env_extra": None,
            }
        if name == "frontend":
            npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
            return {
                "cmd": [npm_cmd, "run", "start"],
                "cwd": str(PROJECT_ROOT / "frontend"),
                "env_extra": {"PORT": str(cfg.get("frontend_port", 3003))},
            }
        if name == "gitnexus":
            gn_cmd = self._resolve_gitnexus_cmd()
            return {
                "cmd": [*gn_cmd, "serve", "--port", str(cfg.get("gitnexus_port", 7100)), "--host", "0.0.0.0"],
                "cwd": str(PROJECT_ROOT),
                "env_extra": None,
            }
        if name == "cgc":
            cgc_cmd = self._resolve_cgc_cmd()
            if cgc_cmd:
                # Match CGC_ALLOWED_ROOTS injection from the quickstart path.
                _cgc_allowed_roots = ";".join([
                    str(PROJECT_ROOT.parent),
                    os.path.expanduser("~"),
                ])
                return {
                    "cmd": [*cgc_cmd, "api", "start", "--host", "127.0.0.1", "--port", str(self._config_port("cgc_port", _CGC_DEFAULT_PORT))],
                    "cwd": self._cgc_cwd(),
                    "env_extra": {"CGC_ALLOWED_ROOTS": _cgc_allowed_roots},
                }

        return None

    async def start_service(self, name: str) -> dict:
        """Start a previously-stopped service using stored startup args."""
        targets = [name]
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

            if target == "frontend":
                await self._step_install_frontend()

            args = self._start_args[target]
            await self._spawn_process(target, args["cmd"], args["cwd"], "start", 0, args.get("env_extra"))

        return {"ok": True, "service": name, "action": "started"}

    async def _drain_output(
        self, name: str, proc: asyncio.subprocess.Process, step_name: str, step_index: int
    ) -> None:
        assert proc.stdout is not None
        reader = proc.stdout
        # asyncio.StreamReader.readline() has a default 64KB buffer cap and
        # raises LimitOverrunError when a single line exceeds it. The
        # default `async for line in stdout` re-raises that as ValueError
        # and kills the drain task entirely — after which NO further output
        # from this subprocess reaches the deploy panel (including the
        # actual error message we wanted to see).
        #
        # Large single-line payloads from backend logging used to make failures
        # silent in the UI.
        #
        # Recover by falling back to a raw chunk read and continuing.
        # We also wrap the whole loop in try/except so a totally unexpected
        # failure surfaces one line and exits gracefully instead of
        # producing an opaque "Task exception was never retrieved".
        try:
            while True:
                try:
                    raw_line = await reader.readline()
                except ValueError:
                    # Single line > 64KB. Drain a chunk's worth and resume.
                    # Multiple iterations may be needed to walk past it.
                    raw_line = await reader.read(65536)
                    if not raw_line:
                        break
                if not raw_line:
                    break
                line = raw_line.decode(errors="replace").rstrip()
                if line:
                    await self._queue.put({
                        "step": step_name,
                        "status": "running",
                        "message": _redact_deployer_message(f"[{name}] {line}"),
                        "progress": {"current": step_index, "total": TOTAL_STEPS},
                    })
        except Exception as exc:
            await self._queue.put({
                "step": step_name,
                "status": "running",
                "message": _redact_deployer_message(f"[{name}] (drain stopped: {exc!r})"),
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
        cgc_warning_emitted = False

        while elapsed < max_wait:
            if self._stopped:
                return
            all_ok = True
            for name, port, _kind, path in self._service_targets():
                try:
                    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                        resp = await client.get(f"http://localhost:{port}{path}")
                        if not (200 <= resp.status_code < 400):
                            all_ok = False
                except Exception:
                    all_ok = False

            if self._config.get("install_cgc", True) and self._processes.get("cgc") is not None:
                cgc_port = self._config_port("cgc_port", _CGC_DEFAULT_PORT)
                try:
                    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                        resp = await client.get(f"http://localhost:{cgc_port}/api/v1/status")
                        if not (200 <= resp.status_code < 400):
                            if not cgc_warning_emitted:
                                await self._emit(
                                    "health_check",
                                    "running",
                                    f"CGC 健康检查未通过（HTTP {resp.status_code}），核心服务继续可用；调用链/符号图能力可能暂不可用。",
                                    step,
                                )
                                cgc_warning_emitted = True
                except Exception:
                    if not cgc_warning_emitted:
                        await self._emit(
                            "health_check",
                            "running",
                            "CGC 健康检查未通过，核心服务继续可用；调用链/符号图能力可能暂不可用。",
                            step,
                        )
                        cgc_warning_emitted = True


            if all_ok:
                await self._emit("health_check", "done", "所有核心服务健康运行！", step)
                return

            await self._emit("health_check", "running", f"等待中...（{elapsed}s / {max_wait}s）", step)
            await asyncio.sleep(interval)
            elapsed += interval

        await self._emit("health_check", "error", "部分服务未能在规定时间内就绪", step)
        raise RuntimeError("Some services did not become healthy in time")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ensure_cgc(self, step: int) -> None:
        """Install CGC venv + codegraphcontext + mcp if not already present."""
        if _cgc is None:
            return
        venv_path_str = str(self._config.get("cgc_venv_path", "")).strip()
        venv_path = Path(venv_path_str) if venv_path_str else None
        await self._emit("start_services", "running", "正在安装 CGC（codegraphcontext + mcp）...", step)
        try:
            await asyncio.to_thread(_cgc.ensure_cgc_installed, venv_path)
            await self._emit("start_services", "running", "CGC 安装完成", step)
        except _cgc.CGCInstallError as exc:
            await self._emit("start_services", "error", f"CGC 安装失败：{exc}", step)
            raise RuntimeError(str(exc)) from exc

    async def _get_git_hash(self, cwd: Path) -> str:
        """Return the current HEAD commit hash, or '' if git is unavailable."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(cwd),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                return stdout.decode(errors="replace").strip()
        except (FileNotFoundError, asyncio.TimeoutError):
            pass
        return ""

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
                    "message": _redact_deployer_message(line),
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
            "message": _redact_deployer_message(message),
            "progress": {"current": step_index, "total": TOTAL_STEPS},
        })


def _keep_existing_backend_env_line(line: str, managed_keys: set[str]) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("#"):
        return "deepwiki" not in stripped.lower()
    key = line.split("=", 1)[0].strip()
    if key in managed_keys:
        return False
    if key in REMOVED_LEGACY_ENV_KEYS:
        return False
    if any(key.startswith(prefix) for prefix in REMOVED_LEGACY_ENV_PREFIXES):
        return False
    return "deepwiki" not in key.lower()


def _frontend_source_fingerprint(frontend_dir: Path) -> str:
    """Hash frontend inputs so deploy packages without .git still rebuild stale .next."""
    hasher = hashlib.sha256()
    roots = [
        "src",
        "public",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "next.config.js",
        "next.config.mjs",
        "next.config.ts",
        "postcss.config.js",
        "postcss.config.mjs",
        "tailwind.config.js",
        "tailwind.config.ts",
        "tsconfig.json",
    ]
    for rel_root in roots:
        root = frontend_dir / rel_root
        if not root.exists():
            continue
        files = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in files:
            try:
                rel_path = path.relative_to(frontend_dir).as_posix()
            except ValueError:
                rel_path = str(path)
            hasher.update(rel_path.encode("utf-8", errors="replace"))
            hasher.update(b"\0")
            try:
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(chunk)
            except OSError:
                hasher.update(b"<unreadable>")
            hasher.update(b"\0")
    return hasher.hexdigest()


def _redact_deployer_message(message: object) -> str:
    text = str(message)
    text = SECRET_PATTERNS[0].sub("<redacted>", text)
    text = SECRET_PATTERNS[1].sub(lambda match: match.group(1) + "<redacted>", text)
    text = SECRET_PATTERNS[2].sub(lambda match: f"{match.group(1)}=<redacted>", text)
    return text
