"""Unit tests for app/api/tools.py.

Covers the three lines missed by E2E tests:
- Line 19: _get_pm returns pm from app.state when it is set
- Line 37: start_tool returns success dict when pm.start() returns True
- Line 57: restart_tool returns success dict when pm.restart() returns True
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import asyncio
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from app.api import tools

pytestmark = [pytest.mark.asyncio]


@asynccontextmanager
async def _no_lifespan(app: FastAPI):
    yield


def _make_app(mock_pm) -> FastAPI:
    app = FastAPI(lifespan=_no_lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(tools.router)
    app.state.process_manager = mock_pm
    return app


@pytest.fixture
async def tools_client():
    mock_pm = MagicMock()
    mock_pm.get_all_status = AsyncMock(return_value=[{"name": "gitnexus", "status": "stopped"}])
    mock_pm.start = AsyncMock(return_value=True)
    mock_pm.stop = AsyncMock(return_value=True)
    mock_pm.restart = AsyncMock(return_value=True)

    app = _make_app(mock_pm)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, mock_pm


class TestGetPmFromAppState:
    async def test_pm_returned_from_app_state(self, tools_client):
        """Line 19: when app.state.process_manager is set, _get_pm returns it
        without falling through to ProcessManager.get_instance()."""
        client, mock_pm = tools_client
        resp = await client.get("/api/tools/procs")
        assert resp.status_code == 200
        mock_pm.get_all_status.assert_called_once()

    async def test_procs_includes_adapter_only_external_agents(self, tools_client, monkeypatch):
        """Adapter-only tools such as claude-code should appear in the tools page."""
        from app.adapters.base import ToolCapability, ToolHealth

        class FakeAgentAdapter:
            def name(self):
                return "claude-code"

            def capabilities(self):
                return [ToolCapability.CODE_SEARCH]

            async def health_check(self):
                return ToolHealth(
                    True,
                    "available",
                    version="C:/tools/claude.cmd",
                    last_check="primary command unavailable; using fallback: claude -p",
                )

        client, mock_pm = tools_client
        monkeypatch.setattr(tools, "get_all_adapters", lambda: [FakeAgentAdapter()])

        resp = await client.get("/api/tools/procs")

        assert resp.status_code == 200
        body = resp.json()
        agent = next(item for item in body if item["name"] == "claude-code")
        assert agent["managed"] is False
        assert agent["healthy"] is True
        assert agent["status"] == "available"
        assert "fallback" in agent["message"]
        assert agent["last_check"] == "primary command unavailable; using fallback: claude -p"


class TestStartToolSuccess:
    async def test_start_returns_success(self, tools_client):
        """Line 37: start_tool returns success dict when pm.start() returns True."""
        client, mock_pm = tools_client
        resp = await client.post("/api/tools/gitnexus/start")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "gitnexus" in body["message"]

    async def test_start_failure_includes_process_last_error(self):
        """Failed starts should surface ProcessManager.last_error to the UI."""
        mock_pm = MagicMock()
        mock_pm.start = AsyncMock(return_value=False)
        managed = MagicMock()
        managed.last_error = "Working directory does not exist: X"
        mock_pm._processes = {"deepwiki-api": managed}

        app = _make_app(mock_pm)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/tools/deepwiki-api/start")

        assert resp.status_code == 400
        assert "Working directory does not exist: X" in resp.json()["detail"]


class TestRestartToolSuccess:
    async def test_restart_returns_success(self, tools_client):
        """Line 57: restart_tool returns success dict when pm.restart() returns True."""
        client, mock_pm = tools_client
        resp = await client.post("/api/tools/gitnexus/restart")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "gitnexus" in body["message"]


async def test_tool_health_exposes_adapter_last_check(tools_client, monkeypatch):
    """Direct health endpoint should expose the same diagnostic message as tools page."""
    from app.adapters.base import ToolCapability, ToolHealth

    class FakeAgentAdapter:
        def name(self):
            return "claude-code"

        def capabilities(self):
            return [ToolCapability.CODE_SEARCH]

        async def health_check(self):
            return ToolHealth(
                False,
                "unavailable",
                version="no agent command found",
                last_check="ccr code -p => unavailable; PATH entries: C:/agent-bin",
            )

    client, _mock_pm = tools_client
    monkeypatch.setattr(tools, "get_adapter", lambda _name: FakeAgentAdapter())

    resp = await client.get("/api/tools/claude-code/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["healthy"] is False
    assert body["message"] == "ccr code -p => unavailable; PATH entries: C:/agent-bin"
    assert body["last_check"] == body["message"]


async def test_tools_status_exposes_adapter_diagnostics(tools_client, monkeypatch):
    """Status endpoint should keep old fields while exposing actionable diagnostics."""
    from app.adapters.base import ToolCapability, ToolHealth

    class FakeAgentAdapter:
        def name(self):
            return "claude-code"

        def capabilities(self):
            return [ToolCapability.CODE_SEARCH]

        async def health_check(self):
            return ToolHealth(
                False,
                "unavailable",
                version="no agent command found",
                last_check="ccr code -p => unavailable; PATH entries: C:/agent-bin",
            )

    client, _mock_pm = tools_client
    monkeypatch.setattr(tools, "get_all_adapters", lambda: [FakeAgentAdapter()])

    resp = await client.get("/api/tools/status")

    assert resp.status_code == 200
    body = resp.json()["claude-code"]
    assert body["healthy"] is False
    assert body["indexed_repos"] == 0
    assert body["last_index_error"] is None
    assert body["container_status"] == "unavailable"
    assert body["version"] == "no agent command found"
    assert body["last_check"] == "ccr code -p => unavailable; PATH entries: C:/agent-bin"
    assert body["message"] == body["last_check"]
    assert body["capabilities"] == ["code_search"]


async def test_tool_health_exception_exposes_diagnostic_message(tools_client, monkeypatch):
    """Health endpoint errors should remain actionable instead of returning null text."""

    class BrokenAgentAdapter:
        def name(self):
            return "claude-code"

        async def health_check(self):
            raise RuntimeError(
                "settings parse failed: CLAUDE_CODE_FALLBACK_COMMANDS --api-key sk-health-secret-123"
            )

    client, _mock_pm = tools_client
    monkeypatch.setattr(tools, "get_adapter", lambda _name: BrokenAgentAdapter())

    resp = await client.get("/api/tools/claude-code/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["healthy"] is False
    assert body["container_status"] == "error"
    assert "settings parse failed" in body["message"]
    assert "sk-health-secret-123" not in body["message"]
    assert "<redacted>" in body["message"]
    assert body["last_check"] == body["message"]


async def test_tools_status_exception_redacts_diagnostic_message(tools_client, monkeypatch):
    """Status endpoint adapter failures should not leak command secrets."""

    from app.adapters.base import ToolCapability

    class BrokenAgentAdapter:
        def name(self):
            return "claude-code"

        def capabilities(self):
            return [ToolCapability.CODE_SEARCH]

        async def health_check(self):
            raise RuntimeError("adapter failed --token sk-status-secret-123")

    client, _mock_pm = tools_client
    monkeypatch.setattr(tools, "get_all_adapters", lambda: [BrokenAgentAdapter()])

    resp = await client.get("/api/tools/status")

    assert resp.status_code == 200
    body = resp.json()["claude-code"]
    assert body["container_status"] == "error"
    assert "adapter failed" in body["message"]
    assert "sk-status-secret-123" not in body["message"]
    assert "<redacted>" in body["message"]


async def test_tools_procs_adapter_exception_redacts_diagnostic_message(tools_client, monkeypatch):
    """Tools page adapter-only status should redact failed health-check details."""

    from app.adapters.base import ToolCapability

    class BrokenAgentAdapter:
        def name(self):
            return "claude-code"

        def capabilities(self):
            return [ToolCapability.CODE_SEARCH]

        async def health_check(self):
            raise RuntimeError("adapter failed --api-key sk-procs-secret-123")

    client, _mock_pm = tools_client
    monkeypatch.setattr(tools, "get_all_adapters", lambda: [BrokenAgentAdapter()])

    resp = await client.get("/api/tools/procs")

    assert resp.status_code == 200
    agent = next(item for item in resp.json() if item["name"] == "claude-code")
    assert agent["status"] == "error"
    assert "adapter failed" in agent["message"]
    assert "sk-procs-secret-123" not in agent["message"]
    assert "<redacted>" in agent["message"]


async def test_external_agent_health_keeps_default_ccr_config_hint_non_blocking(monkeypatch):
    """Default CCR config absence is a diagnostic hint; explicit bad config is misconfigured."""
    from app.adapters.external_agent import ExternalAgentAdapter

    monkeypatch.setattr(
        "app.adapters.external_agent.check_provider_health",
        lambda *_args, **_kwargs: {
            "provider": "claude-code",
            "status": "available",
            "path": "C:/Users/me/AppData/Roaming/npm/ccr.cmd",
            "launch_kind": "exec",
            "attempts": [
                {
                    "command": "ccr code -p",
                    "status": "available",
                    "launch_kind": "exec",
                    "config_hint": (
                        "CCR_CONFIG_PATH is not set and default config not found: "
                        "C:/Users/me/.claude-code-router/config-router.json"
                    ),
                }
            ],
        },
    )

    health = await ExternalAgentAdapter("claude-code", "claude_code_command").health_check()

    assert health.is_healthy is True
    assert health.container_status == "available"
    assert "CCR_CONFIG_PATH" in health.last_check


async def test_external_agent_startup_probe_endpoint_returns_diagnostics(tools_client, monkeypatch):
    """Startup probe should actually delegate to the adapter diagnostic method."""

    class FakeAgentAdapter:
        def name(self):
            return "claude-code"

        async def startup_probe(self, repo_path=None):
            return {
                "provider": "claude-code",
                "healthy": True,
                "status": "ok",
                "message": f"startup_probe_ok at {repo_path}",
            }

    client, _mock_pm = tools_client
    monkeypatch.setattr(tools, "get_adapter", lambda _name: FakeAgentAdapter())

    resp = await client.post(
        "/api/tools/claude-code/startup-probe",
        params={"repo_path": "E:/repo"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["healthy"] is True
    assert body["status"] == "ok"
    assert body["message"] == "startup_probe_ok at E:/repo"


async def test_external_agent_startup_probe_exception_returns_diagnostics(tools_client, monkeypatch):
    """Startup probe adapter failures should be visible as structured diagnostics."""

    class BrokenAgentAdapter:
        def name(self):
            return "claude-code"

        async def startup_probe(self, repo_path=None):
            raise RuntimeError("spawn failed: ccr --api-key sk-tool-secret-123 is not on backend PATH")

    client, _mock_pm = tools_client
    monkeypatch.setattr(tools, "get_adapter", lambda _name: BrokenAgentAdapter())

    resp = await client.post(
        "/api/tools/claude-code/startup-probe",
        params={"repo_path": "E:/repo"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "claude-code"
    assert body["healthy"] is False
    assert body["status"] == "error"
    assert "spawn failed" in body["message"]
    assert "sk-tool-secret-123" not in body["message"]
    assert "<redacted>" in body["message"]


async def test_gitnexus_startup_probe_reports_managed_process_diagnostics(monkeypatch):
    mock_pm = MagicMock()
    mock_pm.start = AsyncMock(return_value=True)
    mock_pm.health_check = AsyncMock(return_value={
        "name": "gitnexus",
        "healthy": False,
        "status": "error",
        "last_error": "Health endpoint unreachable",
    })
    managed = MagicMock()
    managed.last_error = "Health endpoint unreachable"
    mock_pm._processes = {"gitnexus": managed}

    app = _make_app(mock_pm)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/tools/gitnexus/startup-probe")

    assert resp.status_code == 200
    body = resp.json()
    assert body["tool"] == "gitnexus"
    assert body["healthy"] is False
    assert body["status"] == "error"
    assert "Health endpoint unreachable" in body["message"]
    mock_pm.start.assert_awaited_once_with("gitnexus")
    assert mock_pm.health_check.await_count == 2
    mock_pm.health_check.assert_any_await("gitnexus")


async def test_gitnexus_startup_probe_reuses_existing_healthy_service(monkeypatch):
    mock_pm = MagicMock()
    mock_pm.start = AsyncMock(return_value=True)
    mock_pm.health_check = AsyncMock(return_value={
        "name": "gitnexus",
        "healthy": True,
        "status": "running",
        "version": "1.6.5",
    })

    app = _make_app(mock_pm)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/tools/gitnexus/startup-probe")

    assert resp.status_code == 200
    body = resp.json()
    assert body["tool"] == "gitnexus"
    assert body["healthy"] is True
    assert body["status"] == "ok"
    assert body["started"] is False
    assert "already reachable" in body["message"]
    mock_pm.start.assert_not_awaited()
    mock_pm.health_check.assert_awaited_once_with("gitnexus")


async def test_startup_probe_rejects_tools_without_probe_support(tools_client, monkeypatch):
    class FakeAdapter:
        def name(self):
            return "deepwiki-api"

    client, _mock_pm = tools_client
    monkeypatch.setattr(tools, "get_adapter", lambda _name: FakeAdapter())

    resp = await client.post("/api/tools/deepwiki-api/startup-probe")

    assert resp.status_code == 400
    assert "does not support startup probe" in resp.json()["detail"]


async def test_deepwiki_registry_uses_venv_launcher_and_declared_ports(tmp_path, monkeypatch):
    """DeepWiki native process config should start the real venv launcher on configured ports."""
    from app.config import settings
    from app.services import process_manager

    deepwiki_dir = tmp_path / "deepwiki-open"
    scripts_dir = "Scripts" if process_manager.sys.platform == "win32" else "bin"
    python_name = "python.exe" if process_manager.sys.platform == "win32" else "python"
    venv_python = deepwiki_dir / ".venv" / scripts_dir / python_name
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    (deepwiki_dir / "package.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(settings, "deepwiki_path", str(deepwiki_dir))
    monkeypatch.setattr(settings, "deepwiki_api_port", 8091)
    monkeypatch.setattr(settings, "deepwiki_ui_port", 3001)

    registry = process_manager._build_registry()

    api = registry["deepwiki-api"]
    assert api["command"][0] == str(venv_python)
    assert api["command"][1].endswith("deepwiki_launcher.py")
    assert api["env"]["DEEPWIKI_API_PORT"] == "8091"
    assert api["env"]["PORT"] == "8091"
    assert api["restart_on_health_failure"] is False

    ui = registry["deepwiki-ui"]
    assert ui["env"]["PORT"] == "3001"
    assert ui["env"]["SERVER_BASE_URL"] == "http://localhost:8091"


async def test_deepwiki_process_env_loads_synced_dotenv(tmp_path, monkeypatch):
    """ProcessManager should pass DeepWiki's synced .env to the subprocess."""
    from app.services import process_manager

    (tmp_path / ".env").write_text(
        "OPENAI_BASE_URL=http://internal.ai/v1\n"
        "OPENAI_API_KEY=fresh-key\n"
        "LLM_MODEL=qwen-test\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "stale-system-key")

    env = process_manager._build_process_env(
        "deepwiki-api",
        {"env": {"PORT": "8091"}},
        str(tmp_path),
    )

    assert env["OPENAI_API_KEY"] == "fresh-key"
    assert env["OPENAI_BASE_URL"] == "http://internal.ai/v1"
    assert env["LLM_MODEL"] == "qwen-test"
    assert env["PORT"] == "8091"


async def test_process_manager_resolves_windows_npm_shim_before_spawn(monkeypatch):
    """Managed tools should spawn the real Windows wrapper instead of a bare npm shim."""
    from app.services import process_manager

    monkeypatch.setattr(process_manager.sys, "platform", "win32")
    monkeypatch.setattr(
        process_manager.shutil,
        "which",
        lambda command: "C:/Users/me/AppData/Roaming/npm/gitnexus.cmd"
        if command == "gitnexus"
        else None,
    )

    cmd = process_manager._resolve_spawn_command(["gitnexus", "serve", "--port", "7100"])

    assert cmd[0].replace("\\", "/") == "C:/Users/me/AppData/Roaming/npm/gitnexus.cmd"
    assert cmd[1:] == ["serve", "--port", "7100"]


async def test_process_log_streams_write_to_named_files(tmp_path, monkeypatch):
    """Managed subprocess stdout/stderr should be inspectable from log files."""
    from app.config import settings
    from app.services import process_manager

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    stdout, stderr = process_manager._open_process_log_streams("deepwiki-api")
    try:
        assert stdout is not asyncio.subprocess.DEVNULL
        assert stderr is not asyncio.subprocess.DEVNULL
        stdout.write(b"hello out\n")
        stderr.write(b"hello err\n")
    finally:
        stdout.close()
        stderr.close()

    log_dir = tmp_path / "logs" / "processes"
    assert (log_dir / "deepwiki-api.out.log").read_text(encoding="utf-8") == "hello out\n"
    assert (log_dir / "deepwiki-api.err.log").read_text(encoding="utf-8") == "hello err\n"
