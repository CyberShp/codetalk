"""Tests for the current frontend startup contract in native deploy mode."""

import asyncio
import sys
import unittest
from pathlib import Path

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

            async def _spawn_process(self, name, cmd, cwd, step_name, step_index, env_extra=None):
                self.spawned.append(name)

        async def run() -> RecordingDeployer:
            deployer = RecordingDeployer()
            result = await deployer.start_service("frontend")
            self.assertEqual(result, {"ok": True, "service": "frontend", "action": "started"})
            return deployer

        deployer = asyncio.run(run())
        self.assertEqual(deployer.spawned, ["frontend"])


if __name__ == "__main__":
    unittest.main()
