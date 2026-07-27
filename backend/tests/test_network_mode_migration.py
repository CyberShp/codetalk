import json

import pytest


def test_effective_network_mode_defaults_to_intranet_and_migrates_legacy_boolean():
    from app.config import Settings

    default = Settings(_env_file=None)
    assert default.network_policy_v2_enabled is True
    assert default.effective_network_mode == "intranet"
    assert Settings(_env_file=None, intranet_network_mode=True).effective_network_mode == "intranet"
    assert Settings(_env_file=None, intranet_network_mode=False).effective_network_mode == "developer"
    assert Settings(_env_file=None, network_mode="strict_compliance").effective_network_mode == "strict_compliance"


def test_network_policy_v2_flag_defaults_on_and_legacy_flag_keeps_legacy_decision(monkeypatch):
    from app.services.network_policy import resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "approved_proxy_gateway", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_url", "https://gateway.corp.example:8443", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_config_id", "proxy-prod-1", raising=False)
    assert resolve_agent_network_context(requires_network=True).allowed is True

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", False, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.intranet_network_mode", True)
    monkeypatch.setattr("app.services.network_policy.settings.intranet_agent_egress_enforced_by_host", False)
    legacy = resolve_agent_network_context(requires_network=True)

    assert legacy.allowed is False
    assert legacy.reason == "legacy_intranet_egress_not_certified"
    assert "HTTPS_PROXY" not in legacy.sanitized_environment


def test_v2_intranet_blocks_network_cli_without_boundary_but_allows_offline_agent(monkeypatch):
    from app.services.network_policy import resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "none", raising=False)

    networked = resolve_agent_network_context(requires_network=True)
    offline = resolve_agent_network_context(requires_network=False)

    assert networked.allowed is False
    assert networked.boundary == "none"
    assert "批准代理网关" in networked.remediation
    assert offline.allowed is True
    assert offline.reason == "offline_agent_allowed"


def test_v2_intranet_injects_only_deployment_approved_proxy_ca_and_never_inherits_unknown_values(monkeypatch):
    from app.services.network_policy import resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "approved_proxy_gateway", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_url", "https://svc:secret@gateway.corp.example:8443", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_no_proxy", "localhost,.corp.example", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_ca_bundle_path", "/etc/codetalk/corp-ca.pem", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_config_id", "proxy-prod-1", raising=False)

    context = resolve_agent_network_context(
        requires_network=True,
        environment={
            "PATH": "/usr/bin",
            "HTTPS_PROXY": "https://unknown:secret@evil.example",
            "NO_PROXY": "evil.example",
            "SSL_CERT_FILE": "/tmp/evil-ca.pem",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://trace.example",
            "PIP_INDEX_URL": "https://packages.example/simple",
            "NPM_CONFIG_REGISTRY": "https://packages.example/npm",
        },
    )

    assert context.allowed is True
    assert context.sanitized_environment["HTTPS_PROXY"] == "https://svc:secret@gateway.corp.example:8443"
    assert context.sanitized_environment["HTTP_PROXY"] == "https://svc:secret@gateway.corp.example:8443"
    assert context.sanitized_environment["NO_PROXY"] == "localhost,.corp.example"
    assert context.sanitized_environment["SSL_CERT_FILE"] == "/etc/codetalk/corp-ca.pem"
    assert context.sanitized_environment["OTEL_SDK_DISABLED"] == "true"
    assert "PIP_INDEX_URL" not in context.sanitized_environment
    assert "NPM_CONFIG_REGISTRY" not in context.sanitized_environment
    assert context.sanitized_environment["PIP_NO_INDEX"] == "1"
    assert context.sanitized_environment["UV_OFFLINE"] == "1"
    assert context.sanitized_environment["NPM_CONFIG_UPDATE_NOTIFIER"] == "false"
    assert "trace.example" not in json.dumps(context.snapshot())
    serialized = json.dumps(context.snapshot())
    assert "secret" not in serialized
    assert "gateway.corp.example" not in serialized
    assert "proxy-prod-1" in serialized


def test_agent_environment_strips_all_unapproved_ca_override_channels(monkeypatch):
    from app.services.network_policy import resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "deployment_egress_policy", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.deployment_egress_policy_id", "egress-prod-1", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_ca_bundle_path", "", raising=False)

    context = resolve_agent_network_context(
        requires_network=True,
        environment={
            "PATH": "/usr/bin",
            "SSL_CERT_DIR": "/tmp/unapproved-ca-dir",
            "GIT_SSL_CAINFO": "/tmp/unapproved-git-ca.pem",
            "NPM_CONFIG_CAFILE": "/tmp/unapproved-npm-ca.pem",
            "NODE_OPTIONS": "--use-openssl-ca",
        },
    )

    assert context.allowed is True
    assert context.sanitized_environment["PATH"] == "/usr/bin"
    assert "NODE_OPTIONS" not in context.sanitized_environment
    assert "SSL_CERT_DIR" not in context.sanitized_environment
    assert "GIT_SSL_CAINFO" not in context.sanitized_environment
    assert "NPM_CONFIG_CAFILE" not in context.sanitized_environment


def test_strict_compliance_requires_os_isolation_and_developer_never_reenables_telemetry(monkeypatch):
    from app.services.network_policy import resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "strict_compliance", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "deployment_egress_policy", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.deployment_egress_policy_id", "egress-prod-1", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.strict_compliance_os_network_isolation_enabled", False, raising=False)

    strict = resolve_agent_network_context(requires_network=True)
    assert strict.allowed is False
    assert strict.reason == "strict_compliance_os_isolation_required"

    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "developer", raising=False)
    developer = resolve_agent_network_context(
        requires_network=True,
        environment={"LANGSMITH_TRACING": "true", "CODEX_DISABLE_AUTO_UPDATE": "0"},
    )
    assert developer.allowed is True
    assert developer.sanitized_environment["LANGSMITH_TRACING"] == "false"
    assert developer.sanitized_environment["CODEX_DISABLE_AUTO_UPDATE"] == "1"


def test_context_snapshot_and_blocked_exception_never_include_proxy_credentials(monkeypatch):
    from app.services.network_policy import NetworkEgressBlocked, resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "approved_proxy_gateway", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_url", "https://user:very-secret@gateway.corp.example:8443", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_config_id", "", raising=False)

    context = resolve_agent_network_context(requires_network=True)
    assert "very-secret" not in json.dumps(context.snapshot())
    with pytest.raises(NetworkEgressBlocked) as exc_info:
        context.require_allowed()
    assert "very-secret" not in str(exc_info.value)


def test_offline_agent_never_receives_approved_proxy_even_when_gateway_is_configured(monkeypatch):
    from app.services.network_policy import resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "approved_proxy_gateway", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_url", "https://gateway.corp.example:8443", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_config_id", "proxy-prod-1", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_ca_bundle_path", "/etc/codetalk/corp-ca.pem", raising=False)

    context = resolve_agent_network_context(requires_network=False)

    assert context.allowed is True
    assert "HTTPS_PROXY" not in context.sanitized_environment
    assert "HTTP_PROXY" not in context.sanitized_environment
    assert "ALL_PROXY" not in context.sanitized_environment
    assert "SSL_CERT_FILE" not in context.sanitized_environment


def test_deployment_egress_policy_does_not_silently_inject_proxy_gateway(monkeypatch):
    from app.services.network_policy import resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "deployment_egress_policy", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.deployment_egress_policy_id", "egress-prod-1", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_url", "https://gateway.corp.example:8443", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_config_id", "proxy-prod-1", raising=False)

    context = resolve_agent_network_context(requires_network=True)

    assert context.allowed is True
    assert context.boundary == "deployment_egress_policy"
    assert "HTTPS_PROXY" not in context.sanitized_environment
    assert "HTTP_PROXY" not in context.sanitized_environment
    assert "ALL_PROXY" not in context.sanitized_environment
