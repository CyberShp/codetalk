import pytest


def test_intranet_policy_passes_through_valid_runtime_urls_without_allowlist_or_dns():
    from app.services.network_policy import IntranetNetworkPolicy

    resolved = False

    def resolver(_host: str, _port: int):
        nonlocal resolved
        resolved = True
        return ["203.0.113.18"]

    policy = IntranetNetworkPolicy(policy_id="compat-policy", resolver=resolver)

    for url in (
        "http://127.0.0.1:7100/api/repos",
        "http://10.43.7.18:7100/api/repos",
        "https://example.com/secret",
        "https://github.com/vendor/update",
        "wss://trace.langchain.com/socket",
    ):
        decision = policy.evaluate_url(url)
        assert decision.allowed is True
        assert decision.reason == "codetalk_network_passthrough"

    assert resolved is False
    assert policy.evaluate_url("file:///tmp/not-network").allowed is False


def test_model_request_urls_are_not_narrowed_by_codetalk_path_or_host_policy():
    from app.services.network_policy import IntranetNetworkPolicy

    policy = IntranetNetworkPolicy(policy_id="compat-policy")

    for url in (
        "https://api.openai.com/v1/chat/completions",
        "https://api.openai.com/v1/embeddings",
        "https://api.openai.com/v1/models",
        "https://github.com/v1/chat/completions",
    ):
        decision = policy.evaluate_model_request_url(url)
        assert decision.allowed is True
        assert decision.reason == "codetalk_network_passthrough"


def test_policy_snapshot_describes_environment_owned_network_controls():
    from app.services.network_policy import IntranetNetworkPolicy

    policy = IntranetNetworkPolicy(
        policy_id="compat-policy",
        allowed_hosts={"models.corp.example"},
        allowed_cidrs={"10.42.0.0/16"},
        resolver=lambda _host, _port: ["10.42.7.18"],
    )

    assert policy.snapshot() == {
        "network_mode": "codetalk_passthrough",
        "allowed_endpoint_policy_id": "compat-policy",
        "allowed_hosts": ["models.corp.example"],
        "allowed_cidrs": ["10.42.0.0/16"],
        "telemetry": "managed_by_environment",
        "remote_tracing": "managed_by_environment",
        "hosted_mcp": "managed_by_environment",
        "external_model_api": "configured_provider",
    }


def test_agent_environment_is_preserved_for_company_managed_runtime():
    from app.services.network_policy import scrub_intranet_agent_environment

    env = scrub_intranet_agent_environment({
        "PATH": "/usr/bin",
        "HTTPS_PROXY": "http://corp-proxy.example:8080",
        "ALL_PROXY": "socks5://corp-proxy.example:1080",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://trace.example/v1",
        "LANGSMITH_TRACING": "true",
        "CODEX_DISABLE_AUTO_UPDATE": "0",
    })

    assert env == {
        "PATH": "/usr/bin",
        "HTTPS_PROXY": "http://corp-proxy.example:8080",
        "ALL_PROXY": "socks5://corp-proxy.example:1080",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://trace.example/v1",
        "LANGSMITH_TRACING": "true",
        "CODEX_DISABLE_AUTO_UPDATE": "0",
    }


def test_runtime_url_gates_are_non_blocking_compatibility_checks(monkeypatch):
    from app.services.network_policy import (
        agent_network_is_permitted,
        require_configured_model_request_url,
        require_runtime_url,
    )

    monkeypatch.setattr("app.services.network_policy.settings.intranet_network_mode", True)
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_hosts", [])
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_cidrs", [])

    for url in (
        "https://api.openai.com/v1/chat/completions",
        "https://unapproved-tools.example/api",
        "https://github.com/vendor/update",
    ):
        decision = require_runtime_url(url)
        assert decision.allowed is True
        assert decision.reason == "codetalk_network_passthrough"

    for url in (
        "https://api.deepseek.com/v1/chat/completions",
        "https://api.deepseek.com/v1/models",
        "https://github.com/v1/chat/completions",
    ):
        assert require_configured_model_request_url(url).allowed is True

    assert agent_network_is_permitted() is True


def test_tool_client_creates_clients_for_non_loopback_urls(monkeypatch):
    from app.utils.local_client import local_http_client

    monkeypatch.setattr("app.services.network_policy.settings.intranet_network_mode", True)

    client = local_http_client("https://unapproved-tools.example/api")
    assert client.base_url.host == "unapproved-tools.example"


@pytest.mark.asyncio
async def test_process_health_probe_attempts_configured_tool_url(monkeypatch):
    from app.services.process_manager import ProcessManager

    monkeypatch.setattr("app.services.network_policy.settings.intranet_network_mode", True)
    manager = ProcessManager()
    managed = manager._processes["gitnexus"]
    managed._config["health_url"] = "https://unapproved-tools.example/health"

    class RecordingClient:
        is_closed = False
        called = False

        async def get(self, *_args, **_kwargs):
            self.called = True
            raise AssertionError("health request attempted")

        async def post(self, *_args, **_kwargs):
            raise AssertionError("health request attempted")

    client = RecordingClient()
    manager._http_client = client
    result = await manager.health_check("gitnexus")

    assert client.called is True
    assert result["healthy"] is False
    assert "health request attempted" in str(result["last_error"])
