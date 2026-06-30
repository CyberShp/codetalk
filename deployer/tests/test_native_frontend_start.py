"""Tests for the current frontend startup contract in native deploy mode."""

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

DEPLOYER_DIR = Path(__file__).parent.parent
if str(DEPLOYER_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYER_DIR))

from deployers.native import NativeDeployer  # noqa: E402


class NativeFrontendStartTests(unittest.TestCase):
    def test_frontend_default_start_uses_next_start_script(self) -> None:
        deployer = NativeDeployer({"frontend_port": 3003}, asyncio.Queue())

        args = deployer._default_start_args("frontend")

        self.assertIsNotNone(args)
        assert args is not None
        self.assertEqual(args["cmd"], ["npm", "run", "start"])
        self.assertEqual(args["env_extra"], {"PORT": "3003"})
        self.assertNotIn("standalone", " ".join(args["cmd"]))

    def test_start_service_spawns_frontend_once(self) -> None:
        class RecordingDeployer(NativeDeployer):
            def __init__(self) -> None:
                super().__init__({"frontend_port": 3003}, asyncio.Queue())
                self.spawned: list[str] = []
                self.installed_frontend = 0

            async def _step_install_frontend(self) -> None:
                self.installed_frontend += 1

            async def _spawn_process(self, name, cmd, cwd, step_name, step_index, env_extra=None):
                self.spawned.append(name)

        async def run() -> RecordingDeployer:
            deployer = RecordingDeployer()
            result = await deployer.start_service("frontend")
            self.assertEqual(result, {"ok": True, "service": "frontend", "action": "started"})
            return deployer

        deployer = asyncio.run(run())
        self.assertEqual(deployer.spawned, ["frontend"])
        self.assertEqual(deployer.installed_frontend, 1)

    def test_restart_service_rebuilds_frontend_before_spawn(self) -> None:
        class RecordingDeployer(NativeDeployer):
            def __init__(self) -> None:
                super().__init__({"frontend_port": 3003}, asyncio.Queue())
                self._start_args["frontend"] = {
                    "cmd": ["npm", "run", "start"],
                    "cwd": "/tmp/frontend",
                    "env_extra": {"PORT": "3003"},
                }
                self.events: list[str] = []

            async def _terminate_process(self, name: str, timeout: float = 5) -> None:
                self.events.append(f"terminate:{name}")

            async def _step_install_frontend(self) -> None:
                self.events.append("install_frontend")

            async def _spawn_process(self, name, cmd, cwd, step_name, step_index, env_extra=None):
                self.events.append(f"spawn:{name}")

        async def run() -> RecordingDeployer:
            deployer = RecordingDeployer()
            result = await deployer.restart_service("frontend")
            self.assertEqual(result, {"ok": True, "service": "frontend"})
            return deployer

        deployer = asyncio.run(run())
        self.assertEqual(deployer.events, ["terminate:frontend", "install_frontend", "spawn:frontend"])

    def test_generate_config_removes_legacy_deepwiki_env(self) -> None:
        with TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            backend_dir = project_root / "backend"
            frontend_dir = project_root / "frontend"
            backend_dir.mkdir()
            frontend_dir.mkdir()
            env_path = backend_dir / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "KEEP_ME=1",
                        "DEEPWIKI_PATH=/tmp/deepwiki",
                        "DEEPWIKI_EMBEDDING_API_KEY=sk-old",
                        "# legacy DeepWiki runtime",
                        "NOT_DEEPWIKI_BUT_HAS_deepwiki_NAME=bad",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            deployer = NativeDeployer(
                {
                    "backend_port": 3004,
                    "frontend_port": 3003,
                    "gitnexus_port": 7100,
                    "cgc_port": 7072,
                    "workspace_path": str(project_root / "workspace"),
                },
                asyncio.Queue(),
            )

            with patch("deployers.native.PROJECT_ROOT", project_root):
                asyncio.run(deployer._step_generate_config())

            generated = env_path.read_text(encoding="utf-8")
            self.assertIn("KEEP_ME=1", generated)
            self.assertIn("GITNEXUS_BASE_URL=http://localhost:7100", generated)
            self.assertNotIn("DEEPWIKI", generated)
            self.assertNotIn("deepwiki", generated.lower())


if __name__ == "__main__":
    unittest.main()
