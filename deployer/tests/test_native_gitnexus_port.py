import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import config_store
from deployers.native import NativeDeployer


class ConfigStoreTests(unittest.TestCase):
    def test_load_config_normalizes_legacy_gitnexus_key(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "deploy-config.json"
            config_path.write_text(json.dumps({"mode": "native", "portGitnexus": "7111"}), encoding="utf-8")

            with patch.object(config_store, "CONFIG_PATH", config_path):
                saved = config_store.load_config()
                frontend_cfg = config_store.load_config_for_frontend()

        self.assertEqual(saved["gitnexus_port"], "7111")
        self.assertEqual(frontend_cfg["portGitnexus"], "7111")

    def test_save_and_load_preserves_gitnexus_port_in_canonical_key(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "deploy-config.json"

            with patch.object(config_store, "CONFIG_PATH", config_path):
                config_store.save_config(
                    {
                        "mode": "native",
                        "portGitnexus": "7111",
                        "portFrontend": "3005",
                        "portBackend": "8100",
                    }
                )

                saved = config_store.load_config()
                frontend_cfg = config_store.load_config_for_frontend()

        self.assertEqual(saved["gitnexus_port"], 7111)
        self.assertNotIn("portGitnexus", saved)
        self.assertEqual(frontend_cfg["portGitnexus"], 7111)


class NativeDeployerTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_health_uses_configured_gitnexus_port(self) -> None:
        calls: list[str] = []

        class FakeResponse:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

            async def get(self, url: str) -> FakeResponse:
                calls.append(url)
                return FakeResponse(200)

        fake_httpx = types.SimpleNamespace(AsyncClient=FakeAsyncClient)
        deployer = NativeDeployer(
            {
                "backend_port": 8100,
                "frontend_port": 3005,
                "gitnexus_port": 7111,
            },
            asyncio.Queue(),
        )

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            results = await deployer.check_health()

        self.assertIn("http://localhost:7111/api/info", calls)
        self.assertTrue(any(item["name"] == "gitnexus" and item["healthy"] for item in results))

    async def test_scan_port_conflicts_reports_bind_denied_without_listener(self) -> None:
        class FakeScan:
            async def communicate(self):
                return b"", b""

        async def fake_create_subprocess_exec(*args, **kwargs):
            return FakeScan()

        deployer = NativeDeployer({"gitnexus_port": 7100}, asyncio.Queue())

        with (
            patch("deployers.native.sys.platform", "win32"),
            patch("deployers.native.asyncio.create_subprocess_exec", fake_create_subprocess_exec),
            patch(
                "deployers.native._probe_port_bind",
                return_value={
                    "available": False,
                    "reason": "access_denied",
                    "error": "access denied",
                },
                create=True,
            ),
        ):
            conflicts = await deployer._scan_port_conflicts([7100])

        self.assertEqual(
            conflicts,
            [
                {
                    "port": 7100,
                    "pid": None,
                    "process_name": "unavailable",
                    "is_own": False,
                    "reason": "access_denied",
                    "message": (
                        "Port 7100 cannot be bound. On Windows this can happen "
                        "when the port is in an excluded/reserved range."
                    ),
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
