"""Tests for cgc_launcher.ensure_cgc_installed.

Covers:
- cgc.exe + mcp both present → idempotent, only pip show called
- cgc.exe present, mcp missing → installs only mcp
- cgc.exe present, mcp missing, pip install fails → CGCInstallError
- venv absent → creates venv + installs codegraphcontext + mcp
- venv creation fails → CGCInstallError
- pip install fails → CGCInstallError
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cgc_launcher import CGCInstallError, ensure_cgc_installed


def _scripts() -> str:
    return "Scripts" if sys.platform == "win32" else "bin"


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


class EnsureCGCInstalledAlreadyPresentTests(unittest.TestCase):
    def test_returns_immediately_when_cgc_and_mcp_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "cgc-venv"
            scripts_dir = venv / _scripts()
            scripts_dir.mkdir(parents=True)
            (scripts_dir / _exe("cgc")).touch()
            (scripts_dir / _exe("python")).touch()  # interpreter must exist for health check

            def _ok(cmd, **kwargs):
                result = unittest.mock.MagicMock()
                result.returncode = 0
                return result

            with unittest.mock.patch("subprocess.run", side_effect=_ok) as mock_run:
                ensure_cgc_installed(venv)
                # --version health check + pip show mcp
                self.assertEqual(mock_run.call_count, 2)
                calls = [c.args[0] for c in mock_run.call_args_list]
                self.assertIn("--version", calls[0])
                self.assertIn("show", calls[1])


class EnsureCGCInstalledPartialInstallTests(unittest.TestCase):
    """cgc.exe exists but mcp is missing — should install only mcp."""

    def test_installs_only_mcp_when_cgc_present_but_mcp_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "cgc-venv"
            scripts_dir = venv / _scripts()
            scripts_dir.mkdir(parents=True)
            (scripts_dir / _exe("cgc")).touch()
            (scripts_dir / _exe("python")).touch()

            call_log: list = []

            def _fake(cmd, **kwargs):
                call_log.append(cmd)
                result = unittest.mock.MagicMock()
                # pip show mcp → not found; everything else (--version, install) → ok
                result.returncode = 1 if "show" in cmd else 0
                result.stderr = ""
                result.stdout = ""
                return result

            with unittest.mock.patch("subprocess.run", side_effect=_fake):
                ensure_cgc_installed(venv)

            # --version health check + pip show mcp + pip install mcp
            self.assertEqual(len(call_log), 3)
            self.assertIn("--version", call_log[0])
            self.assertIn("show", call_log[1])
            self.assertIn("install", call_log[2])
            self.assertIn("mcp", call_log[2])

    def test_raises_when_cgc_present_mcp_install_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "cgc-venv"
            scripts_dir = venv / _scripts()
            scripts_dir.mkdir(parents=True)
            (scripts_dir / _exe("cgc")).touch()
            (scripts_dir / _exe("python")).touch()

            def _fake(cmd, **kwargs):
                result = unittest.mock.MagicMock()
                # interpreter healthy, mcp absent, mcp install fails
                result.returncode = 0 if "--version" in cmd else 1
                result.stderr = "network error"
                result.stdout = ""
                return result

            with unittest.mock.patch("subprocess.run", side_effect=_fake):
                with self.assertRaises(CGCInstallError) as cm:
                    ensure_cgc_installed(venv)
                self.assertIn("mcp", str(cm.exception))


class EnsureCGCInstalledFreshVenvTests(unittest.TestCase):
    def _make_fake_run(self, pip_exe: Path):
        """Return a fake subprocess.run that creates pip on venv creation."""
        def _fake(cmd, **kwargs):
            if "-m" in cmd and "venv" in cmd:
                pip_exe.parent.mkdir(parents=True, exist_ok=True)
                pip_exe.touch()
            result = unittest.mock.MagicMock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""
            return result
        return _fake

    def test_creates_venv_and_installs_both_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "cgc-venv"
            pip_exe = venv / _scripts() / _exe("pip")

            with unittest.mock.patch("subprocess.run", side_effect=self._make_fake_run(pip_exe)) as mock_run:
                ensure_cgc_installed(venv)
                # expect: python -m venv, pip install codegraphcontext, pip install mcp
                self.assertEqual(mock_run.call_count, 3)
                calls = [c.args[0] for c in mock_run.call_args_list]
                self.assertIn("venv", calls[0])
                self.assertIn("codegraphcontext", calls[1])
                self.assertIn("mcp", calls[2])

    def test_skips_venv_creation_when_python_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "cgc-venv"
            scripts_dir = venv / _scripts()
            scripts_dir.mkdir(parents=True)
            (scripts_dir / _exe("python")).touch()

            def _fake(cmd, **kwargs):
                result = unittest.mock.MagicMock()
                result.returncode = 0
                result.stderr = ""
                result.stdout = ""
                return result

            with unittest.mock.patch("subprocess.run", side_effect=_fake) as mock_run:
                ensure_cgc_installed(venv)
                # --version health check + pip install codegraphcontext + pip install mcp
                self.assertEqual(mock_run.call_count, 3)


class EnsureCGCInstalledFailureTests(unittest.TestCase):
    def test_raises_on_venv_creation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "cgc-venv"

            def _fail(cmd, **kwargs):
                result = unittest.mock.MagicMock()
                result.returncode = 1
                result.stderr = "venv error"
                result.stdout = ""
                return result

            with unittest.mock.patch("subprocess.run", side_effect=_fail):
                with self.assertRaises(CGCInstallError) as cm:
                    ensure_cgc_installed(venv)
                self.assertIn("虚拟环境创建失败", str(cm.exception))

    def test_raises_on_pip_install_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "cgc-venv"
            scripts_dir = venv / _scripts()
            scripts_dir.mkdir(parents=True)
            (scripts_dir / _exe("python")).touch()

            def _fake(cmd, **kwargs):
                result = unittest.mock.MagicMock()
                # interpreter healthy so we skip venv creation; installs fail
                result.returncode = 0 if "--version" in cmd else 1
                result.stderr = "pip error"
                result.stdout = ""
                return result

            with unittest.mock.patch("subprocess.run", side_effect=_fake):
                with self.assertRaises(CGCInstallError) as cm:
                    ensure_cgc_installed(venv)
                self.assertIn("依赖安装失败", str(cm.exception))

    def test_error_message_includes_manual_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "cgc-venv"

            def _fail(cmd, **kwargs):
                result = unittest.mock.MagicMock()
                result.returncode = 1
                result.stderr = "network error"
                result.stdout = ""
                return result

            with unittest.mock.patch("subprocess.run", side_effect=_fail):
                with self.assertRaises(CGCInstallError) as cm:
                    ensure_cgc_installed(venv)
                msg = str(cm.exception)
                self.assertIn("请手动运行", msg)


if __name__ == "__main__":
    unittest.main()
