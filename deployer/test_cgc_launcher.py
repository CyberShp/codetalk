"""Tests for cgc_launcher.

Covers ensure_cgc_installed:
- cgc.exe + mcp both present + module ok → idempotent (pip --version, cgc --version, pip show)
- cgc.exe present, mcp missing → installs only mcp
- cgc.exe present, mcp missing, pip install fails → CGCInstallError
- cgc.exe present but interpreter broken → recreates venv + installs both packages
- cgc.exe present, module entrypoint broken → full reinstall
- venv absent → creates venv + installs codegraphcontext + mcp
- venv creation fails → CGCInstallError
- pip install fails → CGCInstallError

Covers resolve_cgc_cmd:
- venv with python.exe exists → returns [python_exe, "-m", "codegraphcontext"]
- venv exists but no python.exe → returns None
- no venv at all → returns None
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cgc_launcher import CGCInstallError, ensure_cgc_installed, resolve_cgc_cmd


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
                # pip --version health check + codegraphcontext --version + pip show mcp
                self.assertEqual(mock_run.call_count, 3)
                calls = [c.args[0] for c in mock_run.call_args_list]
                self.assertIn("--version", calls[0])
                self.assertIn("codegraphcontext", calls[1])
                self.assertIn("show", calls[2])


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
                # pip show mcp → not found; everything else (--version, cgc --version, install) → ok
                result.returncode = 1 if "show" in cmd else 0
                result.stderr = ""
                result.stdout = ""
                return result

            with unittest.mock.patch("subprocess.run", side_effect=_fake):
                ensure_cgc_installed(venv)

            # pip --version + codegraphcontext --version + pip show mcp + pip install mcp
            self.assertEqual(len(call_log), 4)
            self.assertIn("--version", call_log[0])
            self.assertIn("codegraphcontext", call_log[1])
            self.assertIn("show", call_log[2])
            self.assertIn("install", call_log[3])
            self.assertIn("mcp", call_log[3])

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

    def test_reinstalls_when_module_entrypoint_broken(self) -> None:
        """Regression: cgc.exe + healthy pip, but python -m codegraphcontext fails.

        This mirrors the case where the package was partially uninstalled or
        the module's own import chain is broken.  The module entrypoint check
        must trigger a full reinstall rather than an early-exit.
        """
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
                # pip --version ok, codegraphcontext --version fails, venv/install ok
                if "--version" in cmd and "codegraphcontext" in cmd:
                    result.returncode = 1
                elif "--version" in cmd:
                    result.returncode = 0
                else:
                    result.returncode = 0
                result.stderr = ""
                result.stdout = ""
                return result

            with unittest.mock.patch("subprocess.run", side_effect=_fake):
                ensure_cgc_installed(venv)

            # pip --version (ok) → cgc --version (fail) → no venv recreate (pip ok)
            # → pip install codegraphcontext → pip install mcp
            self.assertEqual(len(call_log), 4)
            self.assertIn("--version", call_log[0])   # pip --version
            self.assertIn("codegraphcontext", call_log[1])  # cgc --version (fails)
            self.assertIn("codegraphcontext", call_log[2])  # pip install codegraphcontext
            self.assertIn("mcp", call_log[3])               # pip install mcp
            # Must NOT have called pip show (no partial install path when module broken)
            for cmd in call_log:
                self.assertNotIn("show", cmd)

    def test_recreates_venv_when_cgc_exists_but_interpreter_broken(self) -> None:
        """Regression: cgc.exe + python.exe present but python -m pip --version fails.

        This mirrors the real cgc-venv state where pyvenv.cfg points to a deleted
        Python install.  _pip_healthy() must return False, triggering full venv
        recreation rather than a pip show/install attempt through the broken exe.
        """
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
                # python -m pip --version fails (broken interpreter)
                result.returncode = 1 if "--version" in cmd else 0
                result.stderr = "No Python at ..."
                result.stdout = ""
                return result

            with unittest.mock.patch("subprocess.run", side_effect=_fake):
                ensure_cgc_installed(venv)

            # health-check (fails) → venv recreate → install codegraphcontext → install mcp
            self.assertEqual(len(call_log), 4)
            self.assertIn("--version", call_log[0])
            self.assertIn("venv", call_log[1])
            self.assertIn("codegraphcontext", call_log[2])
            self.assertIn("mcp", call_log[3])
            # Must NOT have tried pip show/install through the broken interpreter
            for cmd in call_log:
                self.assertNotIn("show", cmd)


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


class ResolveCGCCmdTests(unittest.TestCase):
    """Tests for resolve_cgc_cmd."""

    def test_returns_cmd_when_python_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "cgc-venv"
            scripts_dir = venv / _scripts()
            scripts_dir.mkdir(parents=True)
            python_exe = scripts_dir / _exe("python")
            python_exe.touch()

            cmd = resolve_cgc_cmd({"cgc_venv_path": str(venv)})
            self.assertIsNotNone(cmd)
            assert cmd is not None
            self.assertEqual(cmd[0], str(python_exe))
            self.assertEqual(cmd[1:], ["-m", "codegraphcontext"])

    def test_returns_none_when_python_missing(self) -> None:
        import cgc_launcher as _mod

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            venv = tmp_path / "cgc-venv"
            (venv / _scripts()).mkdir(parents=True)
            # No python.exe — scripts dir exists but interpreter absent

            # Patch fallback paths so the real cgc-venv on disk isn't found
            with unittest.mock.patch.object(_mod, "CGC_DEFAULT_VENV", tmp_path / "cgc-venv"), \
                 unittest.mock.patch.object(_mod, "_COWORKERS", tmp_path):
                cmd = resolve_cgc_cmd({"cgc_venv_path": str(venv)})
            self.assertIsNone(cmd)

    def test_returns_none_when_venv_absent(self) -> None:
        import cgc_launcher as _mod

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            nonexistent = tmp_path / "no-such-venv"

            with unittest.mock.patch.object(_mod, "CGC_DEFAULT_VENV", tmp_path / "cgc-venv"), \
                 unittest.mock.patch.object(_mod, "_COWORKERS", tmp_path):
                cmd = resolve_cgc_cmd({"cgc_venv_path": str(nonexistent)})
            self.assertIsNone(cmd)


if __name__ == "__main__":
    unittest.main()
