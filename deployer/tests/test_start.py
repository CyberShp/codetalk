"""Tests for start.py — covers _venv_python(), _check_python_version(),
and other pure functions that do not spawn subprocesses."""

import sys
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


def test_check_python_version_exits_on_old_python():
    """Simulate Python 3.9 — function must call sys.exit(1)."""
    mock_vi = MagicMock(major=3, minor=9)
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


def test_deployer_url_uses_configured_host_port(monkeypatch):
    monkeypatch.setenv("CODETALK_DEPLOYER_HOST", "127.0.0.1")
    monkeypatch.setenv("CODETALK_DEPLOYER_PORT", "9041")
    import importlib

    reloaded = importlib.reload(start)
    assert reloaded.HOST == "127.0.0.1"
    assert reloaded.PORT == 9041
    assert reloaded.URL == "http://127.0.0.1:9041"
