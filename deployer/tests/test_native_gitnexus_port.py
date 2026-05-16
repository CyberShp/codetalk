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
    def test_load_config_normalizes_legacy_deepwiki_port_key(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "deploy-config.json"
            config_path.write_text(json.dumps({"mode": "native", "deepwiki_port": "8123"}), encoding="utf-8")

            with patch.object(config_store, "CONFIG_PATH", config_path):
                saved = config_store.load_config()
                frontend_cfg = config_store.load_config_for_frontend()

        self.assertEqual(saved["deepwiki_api_port"], "8123")
        self.assertEqual(frontend_cfg["portDeepwiki"], "8123")

    def test_load_config_exposes_deepwiki_supplement_state_to_frontend(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "deploy-config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "mode": "native",
                        "deepwiki_path": "C:/deepwiki-open",
                        "deepwiki_api_port": "8123",
                        "deepwiki_ui_port": "3001",
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(config_store, "CONFIG_PATH", config_path):
                frontend_cfg = config_store.load_config_for_frontend()

        self.assertEqual(frontend_cfg["deepwikiPath"], "C:/deepwiki-open")
        self.assertEqual(frontend_cfg["portDeepwiki"], "8123")
        self.assertEqual(frontend_cfg["deepwikiUiPort"], "3001")

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

        self.assertEqual(saved["gitnexus_port"], "7111")
        self.assertNotIn("portGitnexus", saved)
        self.assertEqual(frontend_cfg["portGitnexus"], "7111")


class NativeDeployerTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_health_uses_configured_deepwiki_port_when_enabled(self) -> None:
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
                "deepwiki_path": "C:/deepwiki-open",
                "deepwiki_api_port": 8123,
            },
            asyncio.Queue(),
        )

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            results = await deployer.check_health()

        self.assertIn("http://localhost:8123/health", calls)
        self.assertTrue(any(item["name"] == "deepwiki" and item["healthy"] for item in results))

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


if __name__ == "__main__":
    unittest.main()
