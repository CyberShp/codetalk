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
        monkeypatch.setattr(
            tools,
            "check_provider_health",
            lambda provider, command, fallback_commands=None: {
                "provider": provider,
                "status": "available",
                "configured_command": command,
                "command": "ccr code",
                "path": "C:/tools/ccr.cmd",
                "launch_kind": "powershell-profile",
                "used_fallback": False,
                "attempts": [
                    {
                        "command": command,
                        "status": "available",
                        "path": "C:/tools/ccr.cmd",
                        "launch_kind": "powershell-profile",
                    }
                ],
            },
        )

        resp = await client.get("/api/tools/procs")

        assert resp.status_code == 200
        body = resp.json()
        agent = next(item for item in body if item["name"] == "claude-code")
        assert agent["managed"] is False
        assert agent["healthy"] is True
        assert agent["status"] == "available"
        assert "fallback" in agent["message"]
        assert agent["last_check"] == "primary command unavailable; using fallback: claude -p"
        diagnostics = agent["agent_provider_diagnostics"]
        assert diagnostics["configured_command_text"] == "ccr code"
        assert diagnostics["startup_probe_endpoint"] == "/api/tools/claude-code/startup-probe"
        assert diagnostics["command_resolution"]["status"] == "available"
        assert diagnostics["command_resolution"]["attempts"][0]["launch_kind"] == "powershell-profile"

    async def test_procs_includes_runtime_custom_external_agent_provider(
        self,
        tools_client,
        monkeypatch,
    ):
        """Providers configured after module import should still appear in tool status."""
        from app.config import settings

        monkeypatch.setattr(settings, "external_agent_custom_providers", [
            {"id": "corp-agent", "command": "corp-agent run --json"}
        ])
        client, _mock_pm = tools_client
        monkeypatch.setattr(tools, "get_all_adapters", lambda: [])

        async def fake_adapter_status(adapter):
            return {
                "name": adapter.name(),
                "display_name": adapter.name(),
                "healthy": False,
                "status": "unavailable",
                "managed": False,
                "message": "command not found: corp-agent",
            }

        monkeypatch.setattr(tools, "_adapter_proc_status", fake_adapter_status)

        resp = await client.get("/api/tools/procs")

        assert resp.status_code == 200
        body = resp.json()
        corp = next(item for item in body if item["name"] == "corp-agent")
        assert corp["managed"] is False
        assert corp["message"] == "command not found: corp-agent"

    async def test_procs_loads_persisted_external_agent_provider_after_restart(
        self,
        tools_client,
        tmp_path,
        monkeypatch,
    ):
        """Tools page should see Agent providers persisted before backend restart."""
        import aiosqlite
        from app.config import settings
        from app.database import _MIGRATIONS, _SCHEMA

        db_path = tmp_path / "settings.db"
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_SCHEMA)
            for stmt in _MIGRATIONS:
                try:
                    await db.execute(stmt)
                except aiosqlite.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                (
                    "external_agent_custom_providers",
                    '[{"id":"persisted-agent","command":"persisted-agent run --json"}]',
                ),
            )
            await db.commit()

        monkeypatch.setattr(settings, "sqlite_db", str(db_path))
        monkeypatch.setattr(settings, "external_agent_custom_providers", [])
        client, _mock_pm = tools_client
        monkeypatch.setattr(tools, "get_all_adapters", lambda: [])

        async def fake_adapter_status(adapter):
            return {
                "name": adapter.name(),
                "display_name": adapter.name(),
                "healthy": True,
                "status": "available",
                "managed": False,
                "message": "loaded",
            }

        monkeypatch.setattr(tools, "_adapter_proc_status", fake_adapter_status)

        resp = await client.get("/api/tools/procs")

        assert resp.status_code == 200
        body = resp.json()
        persisted = next(item for item in body if item["name"] == "persisted-agent")
        assert persisted["managed"] is False
        assert persisted["message"] == "loaded"


class TestStartToolSuccess:
    async def test_start_returns_success(self, tools_client):
        """Line 37: start_tool returns success dict when pm.start() returns True."""
        client, mock_pm = tools_client
        resp = await client.post("/api/tools/gitnexus/start")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "gitnexus" in body["message"]

    async def test_start_reuses_existing_healthy_managed_service(self):
        """Starting an already reachable GitNexus should not spawn a second copy."""
        mock_pm = MagicMock()
        mock_pm.health_check = AsyncMock(return_value={
            "name": "gitnexus",
            "healthy": True,
            "status": "running",
        })
        mock_pm.start = AsyncMock(return_value=True)

        app = _make_app(mock_pm)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/tools/gitnexus/start")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "already running" in body["message"]
        mock_pm.health_check.assert_awaited_once_with("gitnexus")
        mock_pm.start.assert_not_awaited()

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


async def test_tools_status_exposes_external_agent_provider_capabilities(tools_client, monkeypatch):
    """Agent workbench UI needs MCP/artifact capability hints, not just health."""
    from app.adapters.base import ToolCapability, ToolHealth

    class FakeAgentAdapter:
        def name(self):
            return "ccr-code"

        def capabilities(self):
            return [ToolCapability.CODE_SEARCH]

        async def health_check(self):
            return ToolHealth(
                True,
                "available",
                version="ccr code",
                last_check="ok",
            )

    client, _mock_pm = tools_client
    monkeypatch.setattr(tools, "get_all_adapters", lambda: [FakeAgentAdapter()])
    monkeypatch.setattr(
        tools,
        "external_agent_provider_capabilities",
        lambda name: {
            "supports_mcp": name == "ccr-code",
            "mcp_profiles": ["codehub-readonly"],
            "supports_artifact_export": True,
            "supports_json_output": True,
            "prompt_transport": "claude_print_arg",
        },
    )

    resp = await client.get("/api/tools/status")

    assert resp.status_code == 200
    body = resp.json()["ccr-code"]
    assert body["agent_provider"]["supports_mcp"] is True
    assert body["agent_provider"]["mcp_profiles"] == ["codehub-readonly"]
    assert body["agent_provider"]["supports_artifact_export"] is True
    assert body["agent_provider"]["supports_json_output"] is True


async def test_tools_status_loads_persisted_external_agent_provider_after_restart(
    tools_client,
    tmp_path,
    monkeypatch,
):
    """The status endpoint should match /procs for persisted custom providers."""
    import aiosqlite
    from app.config import settings
    from app.database import _MIGRATIONS, _SCHEMA
    from app.adapters.base import ToolCapability, ToolHealth

    db_path = tmp_path / "settings.db"
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                await db.execute(stmt)
            except aiosqlite.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            (
                "external_agent_custom_providers",
                '[{"id":"persisted-agent","command":"persisted-agent run --json"}]',
            ),
        )
        await db.commit()

    class FakeAgentAdapter:
        def __init__(self, provider: str):
            self._provider = provider

        def name(self):
            return self._provider

        def capabilities(self):
            return [ToolCapability.CODE_SEARCH]

        async def health_check(self):
            return ToolHealth(True, "available", version="persisted", last_check="ok")

    client, _mock_pm = tools_client
    monkeypatch.setattr(settings, "sqlite_db", str(db_path))
    monkeypatch.setattr(settings, "external_agent_custom_providers", [])
    monkeypatch.setattr(tools, "get_all_adapters", lambda: [])
    monkeypatch.setattr(tools, "ExternalAgentAdapter", FakeAgentAdapter)

    resp = await client.get("/api/tools/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["persisted-agent"]["healthy"] is True
    assert body["persisted-agent"]["agent_provider"]["provider"] == "persisted-agent"


async def test_tools_status_exposes_fast_context_diagnostics(tools_client, monkeypatch):
    from app.adapters.context_discovery import FastContextAdapter
    from app.config import settings

    client, _mock_pm = tools_client
    monkeypatch.setattr(settings, "fast_context_enabled", True)
    monkeypatch.setattr(settings, "fast_context_backend_bridge_enabled", False)
    monkeypatch.setattr(tools, "get_all_adapters", lambda: [FastContextAdapter()])

    resp = await client.get("/api/tools/status")

    assert resp.status_code == 200
    body = resp.json()["fast-context"]
    assert body["healthy"] is False
    assert body["container_status"] == "unavailable"
    assert "backend bridge is not configured" in body["message"]
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


async def test_external_agent_health_marks_default_ccr_config_hint_misconfigured(monkeypatch):
    """Missing CCR config should be visible before a full analysis is launched."""
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

    assert health.is_healthy is False
    assert health.container_status == "misconfigured"
    assert "CCR_CONFIG_PATH" in health.last_check


async def test_external_agent_health_marks_missing_profile_ccr_config_hint_misconfigured(monkeypatch):
    """A profile launch without a verified profile config should be treated as misconfigured."""
    from app.adapters.external_agent import ExternalAgentAdapter

    monkeypatch.setattr(
        "app.adapters.external_agent.check_provider_health",
        lambda *_args, **_kwargs: {
            "provider": "claude-code",
            "status": "available",
            "path": "PowerShell command: ccr",
            "launch_kind": "powershell-profile",
            "attempts": [
                {
                    "command": "ccr code",
                    "status": "available",
                    "launch_kind": "powershell-profile",
                    "config_hint": (
                        "CCR_CONFIG_PATH is not set and default config not found: "
                        "C:/Users/me/.claude-code-router/config-router.json"
                    ),
                }
            ],
        },
    )

    health = await ExternalAgentAdapter("claude-code", "claude_code_command").health_check()

    assert health.is_healthy is False
    assert health.container_status == "misconfigured"
    assert "powershell-profile" in health.last_check


async def test_external_agent_health_accepts_profile_ccr_config(monkeypatch):
    """A verified CCR config from PowerShell profile should remain healthy."""
    from app.adapters.external_agent import ExternalAgentAdapter

    monkeypatch.setattr(
        "app.adapters.external_agent.check_provider_health",
        lambda *_args, **_kwargs: {
            "provider": "claude-code",
            "status": "available",
            "path": "C:/Users/me/AppData/Roaming/npm/ccr.cmd",
            "launch_kind": "powershell-profile",
            "attempts": [
                {
                    "command": "ccr code",
                    "status": "available",
                    "launch_kind": "powershell-profile",
                    "profile_config_path": "C:/Users/me/.ccr/config.json",
                    "config_hint": (
                        "CCR_CONFIG_PATH is available from PowerShell profile: "
                        "C:/Users/me/.ccr/config.json"
                    ),
                }
            ],
        },
    )

    health = await ExternalAgentAdapter("claude-code", "claude_code_command").health_check()

    assert health.is_healthy is True
    assert health.container_status == "available"
    assert "powershell-profile" in health.last_check


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


async def test_external_agent_startup_probe_supports_runtime_custom_provider(
    tools_client,
    monkeypatch,
):
    """Workbench-configured Agent CLIs should be probeable without app restart."""
    from app.config import settings

    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "corp-agent", "command": "corp-agent run --json"}
    ])
    client, _mock_pm = tools_client

    def missing_adapter(_name):
        raise KeyError("not registered at import time")

    monkeypatch.setattr(tools, "get_adapter", missing_adapter)

    async def fake_startup_probe(provider, repo_path=None):
        assert provider == "corp-agent"
        return {
            "provider": provider,
            "healthy": False,
            "status": "unavailable",
            "message": f"runtime custom probe at {repo_path}",
        }

    monkeypatch.setattr(
        "app.adapters.external_agent.probe_external_agent_startup",
        fake_startup_probe,
    )

    resp = await client.post(
        "/api/tools/corp-agent/startup-probe",
        params={"repo_path": "E:/repo"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "corp-agent"
    assert body["status"] == "unavailable"
    assert body["message"] == "runtime custom probe at E:/repo"


async def test_external_agent_process_env_includes_custom_provider_env_hints(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.external_agent_discovery import _agent_process_env

    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {
            "id": "corp-agent",
            "command": "corp-agent run --json",
            "env_hints": {
                "CORP_AGENT_PROFILE": "innernet",
                "CORP_AGENT_TOKEN": "token=raw-secret-value",
            },
        }
    ])

    env = _agent_process_env("corp-agent", tmp_path)

    assert env["CODETALK_AGENT_READONLY"] == "1"
    assert env["CODETALK_REPO_PATH"] == str(tmp_path.resolve())
    assert env["CORP_AGENT_PROFILE"] == "innernet"
    assert env["CORP_AGENT_TOKEN"] == "token=raw-secret-value"


async def test_gitnexus_startup_probe_reports_managed_process_diagnostics(monkeypatch):
    from app.config import settings

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
    assert body["diagnostics"]["configured_command"][0] == settings.gitnexus_bin
    assert body["diagnostics"]["resolved_command"][1:] == [
        "serve",
        "--port",
        str(settings.gitnexus_port),
        "--host",
        "0.0.0.0",
    ]
    assert body["diagnostics"]["health_url"].endswith("/api/info")
    assert body["diagnostics"]["health_fallback_url"].endswith("/api/analyze")
    assert body["diagnostics"]["initial_health"]["last_error"] == "Health endpoint unreachable"
    assert body["diagnostics"]["post_start_health"]["last_error"] == "Health endpoint unreachable"
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


async def test_gitnexus_startup_probe_reports_repo_index_readiness(tmp_path, monkeypatch):
    mock_pm = MagicMock()
    mock_pm.start = AsyncMock(return_value=True)
    mock_pm.health_check = AsyncMock(return_value={
        "name": "gitnexus",
        "healthy": True,
        "status": "running",
    })
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    readiness = {
        "requested_repo_path": str(repo_path),
        "tool_repo_path": str(repo_path),
        "service_reachable": True,
        "repo_indexed": False,
        "indexed_repo_count": 2,
        "message": "GitNexus reachable but this repo is not indexed",
    }
    readiness_probe = AsyncMock(return_value=readiness)
    monkeypatch.setattr(tools, "_gitnexus_repo_readiness", readiness_probe, raising=False)

    app = _make_app(mock_pm)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/tools/gitnexus/startup-probe",
            params={"repo_path": str(repo_path)},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["diagnostics"]["repo_index"] == readiness
    readiness_probe.assert_awaited_once_with(str(repo_path))


async def test_gitnexus_repo_readiness_matches_indexed_parent_repo(tmp_path, monkeypatch):
    parent = tmp_path / "frontend" / "nof"
    child = parent / "nvmf_tcp"
    child.mkdir(parents=True)

    class FakeResponse:
        status_code = 200

        def json(self):
            return [{
                "name": "nof",
                "path": str(parent),
                "stats": {"files": 12, "nodes": 34, "edges": 56},
            }]

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(tools, "local_http_client", lambda *_args, **_kwargs: FakeClient())

    readiness = await tools._gitnexus_repo_readiness(str(child))

    assert readiness["service_reachable"] is True
    assert readiness["repo_indexed"] is True
    assert readiness["matched_repo_name"] == "nof"
    assert readiness["matched_repo_path"] == str(parent)
    assert readiness["node_count"] == 34


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


async def test_process_manager_finds_windows_npm_shim_when_service_path_misses_it(
    tmp_path, monkeypatch
):
    """A backend service PATH may miss user npm shims even when GitNexus is installed."""
    from app.services import process_manager

    npm_dir = tmp_path / "AppData" / "Roaming" / "npm"
    npm_dir.mkdir(parents=True)
    shim = npm_dir / "gitnexus.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")

    monkeypatch.setattr(process_manager.sys, "platform", "win32")
    monkeypatch.setattr(process_manager.shutil, "which", lambda _command: None)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))

    cmd = process_manager._resolve_spawn_command(["gitnexus", "serve", "--port", "7100"])

    assert cmd[0] == str(shim)
    assert cmd[1:] == ["serve", "--port", "7100"]


async def test_process_manager_wraps_windows_powershell_shim_before_spawn(
    tmp_path, monkeypatch
):
    """PowerShell-only shims must be launched through PowerShell, not CreateProcess."""
    from app.services import process_manager

    npm_dir = tmp_path / "AppData" / "Roaming" / "npm"
    npm_dir.mkdir(parents=True)
    shim = npm_dir / "gitnexus.ps1"
    shim.write_text("Write-Output gitnexus\n", encoding="utf-8")

    monkeypatch.setattr(process_manager.sys, "platform", "win32")
    monkeypatch.setattr(process_manager.shutil, "which", lambda _command: None)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("SystemRoot", "C:/Windows")

    cmd = process_manager._resolve_spawn_command(["gitnexus", "serve", "--port", "7100"])

    assert cmd[0].endswith("powershell.exe")
    assert cmd[1:5] == ["-NoLogo", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    assert cmd[5:7] == ["-File", str(shim)]
    assert cmd[7:] == ["serve", "--port", "7100"]


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
