import pytest


def test_intranet_policy_allows_loopback_and_explicitly_approved_hosts_even_when_publicly_addressed():
    from app.services.network_policy import IntranetNetworkPolicy

    policy = IntranetNetworkPolicy(
        policy_id="corp-approved-v1",
        allowed_hosts={"models.corp.example"},
        resolver=lambda host, _port: {
            "models.corp.example": ["203.0.113.18"],
        }.get(host, []),
    )

    assert policy.evaluate_url("http://127.0.0.1:7100/api/repos").allowed
    assert policy.evaluate_url("http://[::1]:3004/health").allowed
    assert policy.evaluate_url("https://models.corp.example/v1/chat").allowed
    assert not policy.evaluate_url("http://10.42.7.18:7100/api/repos").allowed


def test_intranet_policy_allows_only_explicit_direct_ip_cidrs():
    from app.services.network_policy import IntranetNetworkPolicy

    policy = IntranetNetworkPolicy(
        policy_id="corp-approved-v1",
        allowed_cidrs={"10.42.0.0/16"},
    )

    assert policy.evaluate_url("http://10.42.7.18:7100/api/repos").allowed
    assert not policy.evaluate_url("http://10.43.7.18:7100/api/repos").allowed


def test_intranet_policy_rejects_public_url_before_a_connection_is_attempted():
    from app.services.network_policy import IntranetNetworkPolicy, NetworkEgressBlocked

    resolved = False

    def resolver(_host: str, _port: int):
        nonlocal resolved
        resolved = True
        return ["93.184.216.34"]

    policy = IntranetNetworkPolicy(policy_id="corp-approved-v1", resolver=resolver)
    decision = policy.evaluate_url("https://example.com/secret")

    assert decision.allowed is False
    assert decision.reason == "host_not_allowlisted"
    assert resolved is False
    with pytest.raises(NetworkEgressBlocked, match="运行时出站策略拒绝"):
        policy.require_url("https://example.com/secret")


def test_allowlisted_hostname_can_resolve_to_a_non_rfc1918_intranet_address():
    from app.services.network_policy import IntranetNetworkPolicy

    policy = IntranetNetworkPolicy(
        policy_id="corp-approved-v1",
        allowed_hosts={"models.corp.example"},
        resolver=lambda _host, _port: ["8.8.8.8"],
    )

    decision = policy.evaluate_url("https://models.corp.example/v1")

    assert decision.allowed is True
    assert decision.reason == "approved_hostname"


def test_explicitly_approved_model_host_is_allowed_but_autonomous_services_are_rejected():
    from app.services.network_policy import IntranetNetworkPolicy

    policy = IntranetNetworkPolicy(
        policy_id="corp-approved-v1",
        allowed_hosts={"api.openai.com", "trace.langchain.com", "api.anthropic.com"},
        resolver=lambda _host, _port: ["203.0.113.18"],
    )

    approved = policy.evaluate_url("https://api.openai.com/v1/chat/completions")
    assert approved.allowed is True
    assert approved.reason == "approved_hostname"

    assert policy.evaluate_url("https://trace.langchain.com/api").reason == "autonomous_service_forbidden"
    assert policy.evaluate_url("https://github.com/vendor/update").reason == "autonomous_service_forbidden"


def test_model_request_paths_are_limited_to_adapter_api_routes():
    from app.services.network_policy import IntranetNetworkPolicy

    policy = IntranetNetworkPolicy(
        policy_id="corp-approved-v1",
        allowed_hosts={"api.openai.com"},
        resolver=lambda _host, _port: ["203.0.113.18"],
    )

    assert policy.evaluate_model_request_url(
        "https://api.openai.com/v1/chat/completions"
    ).allowed
    assert policy.evaluate_model_request_url("https://api.openai.com/v1/embeddings").allowed
    denied = policy.evaluate_model_request_url("https://api.openai.com/v1/models")
    assert denied.allowed is False
    assert denied.reason == "model_endpoint_path_forbidden"


def test_policy_snapshot_is_json_safe_and_does_not_include_runtime_resolver():
    from app.services.network_policy import IntranetNetworkPolicy

    policy = IntranetNetworkPolicy(
        policy_id="corp-approved-v1",
        allowed_hosts={"models.corp.example"},
        allowed_cidrs={"10.42.0.0/16"},
        resolver=lambda _host, _port: ["10.42.7.18"],
    )

    assert policy.snapshot() == {
        "network_mode": "intranet_controlled_egress",
        "allowed_endpoint_policy_id": "corp-approved-v1",
        "allowed_hosts": ["models.corp.example"],
        "allowed_cidrs": ["10.42.0.0/16"],
        "telemetry": "disabled",
        "remote_tracing": "disabled",
        "hosted_mcp": "forbidden",
        "external_model_api": "approved_only",
    }


def test_intranet_agent_environment_removes_proxy_telemetry_and_update_channels():
    from app.services.network_policy import scrub_intranet_agent_environment

    env = scrub_intranet_agent_environment({
        "PATH": "/usr/bin",
        "HTTPS_PROXY": "http://public-proxy.example:8080",
        "ALL_PROXY": "socks5://public-proxy.example:1080",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://trace.example/v1",
        "LANGSMITH_TRACING": "true",
        "CODEX_DISABLE_AUTO_UPDATE": "0",
    })

    assert env["PATH"] == "/usr/bin"
    assert "HTTPS_PROXY" not in env
    assert "ALL_PROXY" not in env
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env
    assert env["LANGSMITH_TRACING"] == "false"
    assert env["CODEX_DISABLE_AUTO_UPDATE"] == "1"
    assert env["DO_NOT_TRACK"] == "1"
    assert env["OPENAI_AGENTS_DISABLE_TRACING"] == "1"
    assert env["OPENAI_AGENTS_DONT_LOG_MODEL_DATA"] == "1"
    assert env["LANGCHAIN_TRACING_V2"] == "false"
    assert env["OTEL_SDK_DISABLED"] == "true"


def test_runtime_policy_blocks_official_model_endpoint_before_client_connection(monkeypatch):
    from app.services.network_policy import require_runtime_url

    monkeypatch.setattr("app.services.network_policy.settings.intranet_network_mode", True)
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_hosts", [])
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_cidrs", [])

    with pytest.raises(Exception, match="运行时出站策略拒绝"):
        require_runtime_url("https://api.openai.com/v1/chat/completions")


def test_configured_model_inference_requires_deployment_approval_not_ip_class(monkeypatch):
    from app.services.network_policy import (
        NetworkEgressBlocked,
        require_configured_model_request_url,
    )

    monkeypatch.setattr("app.services.network_policy.settings.intranet_network_mode", True)
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_hosts", [])

    with pytest.raises(NetworkEgressBlocked, match="host_not_allowlisted"):
        require_configured_model_request_url(
            "https://api.deepseek.com/v1/chat/completions"
        )

    monkeypatch.setattr(
        "app.services.network_policy.settings.intranet_allowed_hosts",
        ["api.deepseek.com"],
    )
    decision = require_configured_model_request_url("https://api.deepseek.com/v1/chat/completions")
    assert decision.allowed is True
    assert decision.reason == "configured_and_approved_model_inference"

    with pytest.raises(NetworkEgressBlocked, match="model_endpoint_path_forbidden"):
        require_configured_model_request_url("https://api.deepseek.com/v1/models")
    with pytest.raises(NetworkEgressBlocked, match="autonomous_service_forbidden"):
        require_configured_model_request_url("https://github.com/v1/chat/completions")


def test_intranet_agent_network_fails_closed_until_deployment_policy_is_certified(monkeypatch):
    from app.services.network_policy import agent_network_is_permitted

    monkeypatch.setattr("app.services.network_policy.settings.intranet_network_mode", True)
    monkeypatch.setattr(
        "app.services.network_policy.settings.intranet_agent_egress_enforced_by_host",
        False,
        raising=False,
    )

    assert agent_network_is_permitted() is False

    monkeypatch.setattr(
        "app.services.network_policy.settings.intranet_agent_egress_enforced_by_host",
        True,
    )
    assert agent_network_is_permitted() is True
