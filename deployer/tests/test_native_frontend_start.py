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

import deployers.native as native_module  # noqa: E402
from deployers.native import NativeDeployer, _frontend_source_fingerprint  # noqa: E402


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

    def test_frontend_build_key_changes_without_git_when_source_changes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            frontend_dir = project_root / "frontend"
            app_dir = frontend_dir / "src" / "app"
            app_dir.mkdir(parents=True)
            (frontend_dir / ".env.local").write_text(
                "NEXT_PUBLIC_API_URL=http://localhost:3004\n",
                encoding="utf-8",
            )
            (frontend_dir / "package.json").write_text('{"scripts":{"build":"next build"}}\n', encoding="utf-8")
            page = app_dir / "page.tsx"
            page.write_text("export default function Page(){return <main>old ui</main>}\n", encoding="utf-8")

            deployer = NativeDeployer({}, asyncio.Queue())

            with patch("deployers.native.PROJECT_ROOT", project_root), patch.object(deployer, "_get_git_hash", return_value=""):
                first = asyncio.run(deployer._frontend_build_key(frontend_dir))
                page.write_text("export default function Page(){return <main>new ui</main>}\n", encoding="utf-8")
                second = asyncio.run(deployer._frontend_build_key(frontend_dir))

            self.assertIn("nogit", first)
            self.assertIn("nogit", second)
            self.assertNotEqual(first, second)

    def test_frontend_source_fingerprint_ignores_next_build_outputs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            frontend_dir = Path(tmpdir) / "frontend"
            app_dir = frontend_dir / "src" / "app"
            next_dir = frontend_dir / ".next"
            app_dir.mkdir(parents=True)
            next_dir.mkdir(parents=True)
            (app_dir / "page.tsx").write_text("export default function Page(){return null}\n", encoding="utf-8")
            (next_dir / "BUILD_ID").write_text("build-1\n", encoding="utf-8")

            first = _frontend_source_fingerprint(frontend_dir)
            (next_dir / "BUILD_ID").write_text("build-2\n", encoding="utf-8")
            second = _frontend_source_fingerprint(frontend_dir)

            self.assertEqual(first, second)

    def test_emit_redacts_secret_values_before_queueing(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        deployer = NativeDeployer({}, queue)
        secret = "sk-deployer-log-secret-1234567890"

        asyncio.run(deployer._emit(
            "install_backend",
            "running",
            f"pip output Authorization: Bearer deployerBearerSecret123 token={secret}",
            2,
        ))

        event = queue.get_nowait()
        message = event["message"]
        self.assertIn("<redacted>", message)
        self.assertNotIn(secret, message)
        self.assertNotIn("deployerBearerSecret123", message)
        self.assertNotIn("Authorization: Bearer deployerBearerSecret123", message)

    def test_optional_cgc_install_failure_does_not_emit_error_status(self) -> None:
        if native_module._cgc is None:
            raise unittest.SkipTest("CGC launcher module is unavailable")

        queue: asyncio.Queue = asyncio.Queue()
        deployer = NativeDeployer({}, queue)

        with patch.object(
            native_module._cgc,
            "ensure_cgc_installed",
            side_effect=native_module._cgc.CGCInstallError("pip failed"),
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(deployer._ensure_cgc(step=6))

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        self.assertTrue(any("CGC 安装失败" in event["message"] for event in events))
        self.assertNotIn("error", [event["status"] for event in events])

    def test_run_stream_redacts_subprocess_output_before_queueing(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        deployer = NativeDeployer({}, queue)
        secret = "sk-deployer-stream-secret-1234567890"

        async def run() -> int:
            return await deployer._run_stream(
                "install_backend",
                2,
                sys.executable,
                "-c",
                (
                    "print('install log token=%s Authorization: Bearer streamBearerSecret123')"
                    % secret
                ),
            )

        rc = asyncio.run(run())
        self.assertEqual(rc, 0)

        messages = []
        while not queue.empty():
            messages.append(queue.get_nowait()["message"])
        joined = "\n".join(messages)
        self.assertIn("<redacted>", joined)
        self.assertNotIn(secret, joined)
        self.assertNotIn("streamBearerSecret123", joined)


if __name__ == "__main__":
    unittest.main()
