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
                        "portFrontend": "3003",
                        "portBackend": "3004",
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
                "backend_port": 3004,
                "frontend_port": 3003,
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

    async def test_start_services_keeps_core_running_when_cgc_install_fails(self) -> None:
        """CGC is an optional enhancer; install failure must not fail CodeTalk startup."""
        queue: asyncio.Queue = asyncio.Queue()
        deployer = NativeDeployer(
            {
                "backend_port": 3004,
                "frontend_port": 3003,
                "install_gitnexus": False,
                "install_cgc": True,
                "dev_mode": True,
            },
            queue,
        )
        started: list[str] = []

        async def fake_release_ports(*args, **kwargs) -> None:
            return None

        async def fake_start_process(name, *args, **kwargs) -> None:
            started.append(name)

        async def fake_ensure_cgc(step: int) -> None:
            raise RuntimeError("offline wheelhouse missing")

        with (
            patch.object(deployer, "_release_ports", fake_release_ports),
            patch.object(deployer, "_start_process", fake_start_process),
            patch.object(deployer, "_ensure_cgc", fake_ensure_cgc),
        ):
            await deployer._step_start_services()

        self.assertEqual(started, ["backend", "frontend"])

        events = []
        while not queue.empty():
            events.append(await queue.get())
        messages = "\n".join(str(event.get("message", "")) for event in events)
        self.assertIn("CGC 启动已跳过", messages)
        self.assertIn("offline wheelhouse missing", messages)

    async def test_deploy_keeps_core_running_when_gitnexus_install_fails(self) -> None:
        """GitNexus is optional in intranet deployments; install failure must not block core services."""
        queue: asyncio.Queue = asyncio.Queue()
        deployer = NativeDeployer(
            {
                "backend_port": 3004,
                "frontend_port": 3003,
                "install_gitnexus": True,
                "install_cgc": False,
            },
            queue,
        )
        calls: list[str] = []

        async def record(name: str) -> None:
            calls.append(name)

        async def fail_gitnexus() -> None:
            calls.append("install_gitnexus")
            raise RuntimeError("npm registry unavailable")

        with (
            patch.object(deployer, "_step_check_env", lambda: record("check_env")),
            patch.object(deployer, "_step_install_backend", lambda: record("install_backend")),
            patch.object(deployer, "_step_generate_config", lambda: record("generate_config")),
            patch.object(deployer, "_step_install_frontend", lambda: record("install_frontend")),
            patch.object(deployer, "_step_install_gitnexus", fail_gitnexus),
            patch.object(deployer, "_step_start_services", lambda: record("start_services")),
            patch.object(deployer, "_step_health_check", lambda: record("health_check")),
        ):
            await deployer.deploy()

        self.assertEqual(
            calls,
            [
                "check_env",
                "install_backend",
                "generate_config",
                "install_frontend",
                "install_gitnexus",
                "start_services",
                "health_check",
            ],
        )
        self.assertFalse(deployer._config["install_gitnexus"])

        events = []
        while not queue.empty():
            events.append(await queue.get())
        messages = "\n".join(str(event.get("message", "")) for event in events)
        self.assertIn("GitNexus 安装已跳过", messages)
        self.assertIn("npm registry unavailable", messages)

    async def test_health_check_does_not_fail_deployment_when_optional_cgc_is_unhealthy(self) -> None:
        """CGC health is diagnostic only; backend/frontend readiness is enough to deploy."""
        queue: asyncio.Queue = asyncio.Queue()
        deployer = NativeDeployer(
            {
                "backend_port": 3004,
                "frontend_port": 3003,
                "install_gitnexus": False,
                "install_cgc": True,
                "cgc_port": 7072,
            },
            queue,
        )
        deployer._processes["cgc"] = object()  # type: ignore[assignment]

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
                if url.endswith("/api/v1/status"):
                    return FakeResponse(503)
                return FakeResponse(200)

        fake_httpx = types.SimpleNamespace(AsyncClient=FakeAsyncClient)

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            await deployer._step_health_check()

        events = []
        while not queue.empty():
            events.append(await queue.get())
        messages = "\n".join(str(event.get("message", "")) for event in events)
        self.assertIn("CGC 健康检查未通过", messages)
        self.assertIn("所有核心服务健康运行", messages)


if __name__ == "__main__":
    unittest.main()
