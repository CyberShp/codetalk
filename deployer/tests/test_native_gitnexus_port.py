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
    def test_load_config_drops_legacy_gitnexus_key(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "deploy-config.json"
            config_path.write_text(json.dumps({"mode": "native", "portGitnexus": "7111"}), encoding="utf-8")

            with patch.object(config_store, "CONFIG_PATH", config_path):
                saved = config_store.load_config()
                frontend_cfg = config_store.load_config_for_frontend()

        self.assertNotIn("gitnexus_port", saved)
        self.assertNotIn("portGitnexus", frontend_cfg)

    def test_save_and_load_drops_gitnexus_port(self) -> None:
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

        self.assertNotIn("gitnexus_port", saved)
        self.assertNotIn("portGitnexus", saved)
        self.assertNotIn("portGitnexus", frontend_cfg)


class NativeDeployerTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_health_ignores_removed_gitnexus_port(self) -> None:
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

        self.assertNotIn("http://localhost:7111/api/info", calls)
        self.assertFalse(any(item["name"] == "gitnexus" for item in results))

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
                    "message": "端口 7100 无法绑定；在 Windows 上通常是端口位于系统排除或保留范围",
                }
            ],
        )

    async def test_scan_port_conflicts_on_unix_filters_to_listening_processes(self) -> None:
        calls: list[tuple[str, ...]] = []

        class FakeProcess:
            returncode = 0

            def __init__(self, stdout: bytes = b"") -> None:
                self._stdout = stdout

            async def communicate(self):
                return self._stdout, b""

            async def wait(self):
                return 0

            def kill(self):
                return None

        async def fake_create_subprocess_exec(*args, **kwargs):
            calls.append(tuple(str(arg) for arg in args))
            if args[:2] == ("lsof", "-ti"):
                return FakeProcess(b"53181\n")
            if args[:2] == ("ps", "-p"):
                return FakeProcess(b"Python\n")
            return FakeProcess()

        deployer = NativeDeployer({"backend_port": 3004}, asyncio.Queue())

        with (
            patch("deployers.native.sys.platform", "darwin"),
            patch("deployers.native.asyncio.create_subprocess_exec", fake_create_subprocess_exec),
        ):
            conflicts = await deployer._scan_port_conflicts([3004])

        self.assertEqual(
            conflicts,
            [{"port": 3004, "pid": 53181, "process_name": "Python", "is_own": False}],
        )
        self.assertIn(("lsof", "-ti", ":3004", "-sTCP:LISTEN"), calls)

    async def test_release_ports_on_unix_filters_to_listening_processes(self) -> None:
        calls: list[tuple[str, ...]] = []

        class FakeProcess:
            returncode = 0

            def __init__(self, stdout: bytes = b"") -> None:
                self._stdout = stdout

            async def communicate(self):
                return self._stdout, b""

            async def wait(self):
                return 0

        async def fake_create_subprocess_exec(*args, **kwargs):
            calls.append(tuple(str(arg) for arg in args))
            if args[:2] == ("lsof", "-ti"):
                return FakeProcess(b"53181\n")
            if args[:2] == ("ps", "-p"):
                return FakeProcess(b"Python\n")
            return FakeProcess()

        queue: asyncio.Queue = asyncio.Queue()
        deployer = NativeDeployer({"backend_port": 3004}, queue)

        with (
            patch("deployers.native.sys.platform", "darwin"),
            patch("deployers.native.asyncio.create_subprocess_exec", fake_create_subprocess_exec),
        ):
            await deployer._release_ports([3004], step=1, force_takeover=True)

        self.assertIn(("lsof", "-ti", ":3004", "-sTCP:LISTEN"), calls)
        self.assertIn(("kill", "-9", "53181"), calls)
        self.assertNotIn(("kill", "-9", "4741"), calls)

    async def test_start_services_only_starts_backend_and_frontend(self) -> None:
        """Removed tool-service flags cannot put CGC/GitNexus back in the start path."""
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

        async def fake_ensure_core_ports(*args, **kwargs) -> None:
            return None

        async def fake_start_process(name, *args, **kwargs) -> None:
            started.append(name)

        with (
            patch.object(deployer, "_ensure_core_ports", fake_ensure_core_ports),
            patch.object(deployer, "_start_process", fake_start_process),
        ):
            await deployer._step_start_services()

        self.assertEqual(started, ["backend", "frontend"])

    async def test_deploy_does_not_call_removed_gitnexus_install_step(self) -> None:
        """Deploy now runs only the frontend/backend lifecycle."""
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

        with (
            patch.object(deployer, "_step_check_env", lambda: record("check_env")),
            patch.object(deployer, "_step_install_backend", lambda: record("install_backend")),
            patch.object(deployer, "_step_generate_config", lambda: record("generate_config")),
            patch.object(deployer, "_step_install_frontend", lambda: record("install_frontend")),
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
                "start_services",
                "health_check",
            ],
        )

    async def test_health_check_ignores_removed_cgc_process(self) -> None:
        """A stale cgc process entry cannot affect backend/frontend readiness."""
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
        self.assertNotIn("CGC 健康检查未通过", messages)
        self.assertIn("所有核心服务健康运行", messages)


if __name__ == "__main__":
    unittest.main()
