"""CodeTalk Deployer launcher — sets up venv, installs deps, and starts the server."""

import subprocess
import sys
import threading
import time
import webbrowser
import os
from pathlib import Path


DEPLOYER_DIR = Path(__file__).parent
VENV_DIR = DEPLOYER_DIR / ".venv"
REQUIREMENTS = DEPLOYER_DIR / "requirements.txt"
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
    subprocess.run(
        [str(_venv_python()), "-m", "pip", "install", "-r", str(REQUIREMENTS), "--quiet"],
        check=True,
    )


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
