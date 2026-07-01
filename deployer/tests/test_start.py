"""Tests for start.py — covers _venv_python(), _check_python_version(),
and other pure functions that do not spawn subprocesses."""

import sys
import subprocess
import socket
from unittest.mock import MagicMock, patch

import pytest

import start


def test_venv_python_returns_windows_exe_on_win32():
    if sys.platform != "win32":
        pytest.skip("Windows-only path check")
    result = start._venv_python()
    assert result.name == "python.exe"
    assert "Scripts" in str(result)


def test_venv_python_returns_unix_path_on_non_windows():
    if sys.platform == "win32":
        pytest.skip("Non-Windows path check")
    result = start._venv_python()
    assert result.name == "python"
    assert "bin" in str(result)


def test_venv_python_is_under_venv_dir():
    result = start._venv_python()
    assert str(result).startswith(str(start.VENV_DIR))


def test_check_python_version_passes_on_310_plus():
    """Current interpreter is 3.10+ so this must not raise."""
    start._check_python_version()


def test_check_python_version_reexecs_with_newer_python(monkeypatch):
    """Simulate Python 3.9 — launcher must recover when Python 3.10+ exists."""
    mock_vi = MagicMock(major=3, minor=9)
    exec_calls: list[tuple[str, list[str]]] = []

    def fake_execv(executable, argv):
        exec_calls.append((executable, argv))
        raise SystemExit(0)

    monkeypatch.setattr(start, "_find_compatible_python", lambda: "/opt/homebrew/bin/python3.11")
    monkeypatch.setattr(start.os, "execv", fake_execv)

    with patch.object(sys, "version_info", mock_vi):
        with pytest.raises(SystemExit) as exc_info:
            start._check_python_version()

    assert exc_info.value.code == 0
    assert exec_calls == [
        ("/opt/homebrew/bin/python3.11", ["/opt/homebrew/bin/python3.11", *sys.argv])
    ]


def test_check_python_version_exits_on_old_python_when_no_newer_candidate(monkeypatch):
    """Simulate Python 3.9 with no compatible interpreter — show actionable error."""
    mock_vi = MagicMock(major=3, minor=9)
    monkeypatch.setattr(start, "_find_compatible_python", lambda: None)
    with patch.object(sys, "version_info", mock_vi):
        with pytest.raises(SystemExit) as exc_info:
            start._check_python_version()
    assert exc_info.value.code == 1


def test_create_venv_skips_if_python_exists(tmp_path, monkeypatch):
    """_create_venv() is a no-op when the venv Python already exists."""
    monkeypatch.setattr(start, "VENV_DIR", tmp_path / ".venv")
    fake_python = start._venv_python()
    fake_python.parent.mkdir(parents=True)
    fake_python.touch()

    calls: list[str] = []

    with patch("subprocess.run", side_effect=lambda *a, **kw: calls.append("run")):
        start._create_venv()

    assert calls == [], "subprocess.run must not be called when venv already exists"


def test_open_browser_after_delay_opens_url(monkeypatch):
    """_open_browser_after_delay opens the deployer URL via webbrowser."""
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    monkeypatch.setattr("time.sleep", lambda _: None)
    start._open_browser_after_delay(0)
    assert start.URL in opened


def test_exit_on_subprocess_error_reports_actionable_stage(capsys):
    exc = subprocess.CalledProcessError(
        returncode=17,
        cmd=["python", "-m", "pip", "install", "-r", "requirements.txt"],
    )

    with pytest.raises(SystemExit) as exc_info:
        start._exit_on_subprocess_error("安装部署器依赖失败", exc)

    assert exc_info.value.code == 17
    err = capsys.readouterr().err
    assert "安装部署器依赖失败" in err
    assert "退出码 17" in err
    assert "python -m pip install -r requirements.txt" in err
    assert "依赖源不可达" in err


def test_install_dependencies_uses_vendor_wheels_when_available(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor" / "wheels"
    vendor.mkdir(parents=True)
    (vendor / "fastapi-0.0.0-py3-none-any.whl").touch()
    fake_python = tmp_path / ".venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.touch()

    monkeypatch.setattr(start, "VENDOR_WHEELS_DIR", vendor)
    monkeypatch.setattr(start, "REQUIREMENTS", tmp_path / "requirements.txt")
    monkeypatch.setattr(start, "_venv_python", lambda: fake_python)

    calls: list[list[str]] = []

    def fake_run(cmd, check):
        calls.append(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)

    start._install_dependencies()

    assert calls
    cmd = calls[0]
    assert "--disable-pip-version-check" in cmd
    assert "--no-index" in cmd
    assert "--find-links" in cmd
    assert str(vendor) in cmd


def test_deployer_port_preflight_reports_occupied_port(monkeypatch, capsys):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        occupied_port = listener.getsockname()[1]

        monkeypatch.setattr(start, "HOST", "127.0.0.1")
        monkeypatch.setattr(start, "PORT", occupied_port)

        with pytest.raises(SystemExit) as exc_info:
            start._assert_deployer_port_available()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert f"部署器端口 {occupied_port} 无法绑定" in err
    assert "CODETALK_DEPLOYER_PORT" in err


def test_deployer_url_uses_configured_host_port(monkeypatch):
    monkeypatch.setenv("CODETALK_DEPLOYER_HOST", "127.0.0.1")
    monkeypatch.setenv("CODETALK_DEPLOYER_PORT", "9041")
    import importlib

    reloaded = importlib.reload(start)
    assert reloaded.HOST == "127.0.0.1"
    assert reloaded.PORT == 9041
    assert reloaded.URL == "http://127.0.0.1:9041"


def test_unix_start_script_prefers_python_310_plus():
    script = (start.DEPLOYER_DIR / "start.sh").read_text(encoding="utf-8")
    assert "python3.12 python3.11 python3.10 python3 python" in script
    assert "sys.version_info >= (3, 10)" in script
    assert 'exec "$candidate" start.py' in script


def test_windows_start_script_prefers_py_launcher_310_plus():
    script = (start.DEPLOYER_DIR / "start.bat").read_text(encoding="utf-8")
    assert '"py -3.12" "py -3.11" "py -3.10"' in script
    assert '"python" "python3"' in script
