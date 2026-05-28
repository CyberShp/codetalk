"""Tests for cgc_launcher.ensure_cgc_installed.

Covers three paths:
- cgc.exe already present → idempotent, no subprocess calls
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
    def test_returns_immediately_when_cgc_exe_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "cgc-venv"
            scripts_dir = venv / _scripts()
            scripts_dir.mkdir(parents=True)
            (scripts_dir / _exe("cgc")).touch()

            with unittest.mock.patch("subprocess.run") as mock_run:
                ensure_cgc_installed(venv)
                mock_run.assert_not_called()


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
            (scripts_dir / _exe("pip")).touch()

            def _fake(cmd, **kwargs):
                result = unittest.mock.MagicMock()
                result.returncode = 0
                result.stderr = ""
                result.stdout = ""
                return result

            with unittest.mock.patch("subprocess.run", side_effect=_fake) as mock_run:
                ensure_cgc_installed(venv)
                # only pip install codegraphcontext + mcp (no venv creation)
                self.assertEqual(mock_run.call_count, 2)


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
            (scripts_dir / _exe("pip")).touch()

            def _fail(cmd, **kwargs):
                result = unittest.mock.MagicMock()
                result.returncode = 1
                result.stderr = "pip error"
                result.stdout = ""
                return result

            with unittest.mock.patch("subprocess.run", side_effect=_fail):
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
