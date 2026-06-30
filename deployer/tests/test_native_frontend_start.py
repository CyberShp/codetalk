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


if __name__ == "__main__":
    unittest.main()
