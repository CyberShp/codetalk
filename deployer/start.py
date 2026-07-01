"""CodeTalk Deployer launcher — sets up venv, installs deps, and starts the server."""

import subprocess
import sys
import threading
import time
import webbrowser
import os
import socket
from pathlib import Path


DEPLOYER_DIR = Path(__file__).parent
VENV_DIR = DEPLOYER_DIR / ".venv"
REQUIREMENTS = DEPLOYER_DIR / "requirements.txt"
VENDOR_WHEELS_DIR = DEPLOYER_DIR / "vendor" / "wheels"
HOST = os.environ.get("CODETALK_DEPLOYER_HOST", "0.0.0.0")
PORT = int(os.environ.get("CODETALK_DEPLOYER_PORT", "9000"))
DISPLAY_HOST = "localhost" if HOST in {"0.0.0.0", "::"} else HOST
URL = f"http://{DISPLAY_HOST}:{PORT}"


def _venv_python() -> Path:
    """Return the path to the venv Python executable (platform-aware)."""
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _check_python_version() -> None:
    """Exit with an error if the host Python is older than 3.10."""
    major, minor = sys.version_info.major, sys.version_info.minor
    if (major, minor) < (3, 10):
        print(f"Error: Python 3.10+ is required (found {major}.{minor}). Please upgrade.")
        sys.exit(1)


def _create_venv() -> None:
    """Create a virtual environment at VENV_DIR if one does not already exist."""
    if _venv_python().exists():
        return
    print("Creating virtual environment...")
    subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)


def _install_dependencies() -> None:
    """Install packages from requirements.txt into the venv."""
    print("Installing dependencies...")
    cmd = [
        str(_venv_python()),
        "-m",
        "pip",
        "install",
        "-r",
        str(REQUIREMENTS),
        "--quiet",
        "--disable-pip-version-check",
    ]
    if VENDOR_WHEELS_DIR.exists() and any(VENDOR_WHEELS_DIR.iterdir()):
        cmd.extend(["--no-index", "--find-links", str(VENDOR_WHEELS_DIR)])
    subprocess.run(cmd, check=True)


def _open_browser_after_delay(delay: float) -> None:
    """Open the deployer URL in the default browser after *delay* seconds."""
    time.sleep(delay)
    webbrowser.open(URL)


def _format_command(command: object) -> str:
    if isinstance(command, (list, tuple)):
        return " ".join(str(part) for part in command)
    return str(command)


def _exit_on_subprocess_error(stage: str, exc: subprocess.CalledProcessError) -> None:
    command = _format_command(exc.cmd)
    code = exc.returncode or 1
    print(
        f"\nError: {stage}（退出码 {code}）。\n"
        f"Command: {command}\n"
        "请检查上方完整日志；常见原因包括 Python/pip 无法联网、依赖源不可达、端口被占用或当前目录权限不足。",
        file=sys.stderr,
    )
    sys.exit(code)


def _port_listener_summary(port: int) -> str:
    """Return a best-effort description of processes listening on *port*."""
    try:
        import psutil
    except ImportError:
        return "unknown process"

    listeners: list[str] = []
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != "LISTEN" or not conn.laddr or conn.laddr.port != port:
                continue
            pid = conn.pid
            if pid is None:
                listeners.append("unknown PID")
                continue
            try:
                proc = psutil.Process(pid)
                name = proc.name() or "unknown"
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                name = "unknown"
            listeners.append(f"{name}(PID {pid})")
    except (psutil.AccessDenied, OSError):
        return "unknown process"
    return ", ".join(sorted(set(listeners))) or "unknown process"


def _assert_deployer_port_available() -> None:
    """Fail early with an actionable message when the deployer port is occupied."""
    bind_host = HOST if HOST not in {"::"} else "::"
    family = socket.AF_INET6 if ":" in bind_host and bind_host != "0.0.0.0" else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((bind_host, PORT))
        except OSError as exc:
            listener = _port_listener_summary(PORT)
            print(
                f"\nError: 部署器端口 {PORT} 无法绑定：{exc}\n"
                f"当前监听进程：{listener}\n"
                f"处理方式：关闭占用端口的进程，或设置 CODETALK_DEPLOYER_PORT 为其他端口后重试。\n"
                f"例如：CODETALK_DEPLOYER_PORT=9060 python start.py",
                file=sys.stderr,
            )
            sys.exit(1)


def main() -> None:
    """Entry point: bootstrap the venv and launch uvicorn."""
    _check_python_version()
    try:
        _create_venv()
    except subprocess.CalledProcessError as exc:
        _exit_on_subprocess_error("创建部署器虚拟环境失败", exc)
    try:
        _install_dependencies()
    except subprocess.CalledProcessError as exc:
        _exit_on_subprocess_error("安装部署器依赖失败", exc)

    _assert_deployer_port_available()
    print(f"Starting CodeTalk Deployer at {URL}")

    # Open the browser a couple of seconds after uvicorn starts binding.
    if os.environ.get("CODETALK_DEPLOYER_NO_BROWSER") != "1":
        threading.Thread(target=_open_browser_after_delay, args=(2.0,), daemon=True).start()

    try:
        subprocess.run(
            [
                str(_venv_python()),
                "-m",
                "uvicorn",
                "server:app",
                "--host",
                HOST,
                "--port",
                str(PORT),
            ],
            check=True,
            cwd=str(DEPLOYER_DIR),
        )
    except subprocess.CalledProcessError as exc:
        _exit_on_subprocess_error("启动部署器服务失败", exc)
    except KeyboardInterrupt:
        print("\nDeployer stopped.")


if __name__ == "__main__":
    main()
