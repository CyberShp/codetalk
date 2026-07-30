import json

def test_effective_network_mode_labels_are_compatibility_only():
    from app.config import Settings

    default = Settings(_env_file=None)
    assert default.network_policy_v2_enabled is True
    assert default.effective_network_mode == "intranet"
    assert Settings(_env_file=None, intranet_network_mode=True).effective_network_mode == "intranet"
    assert Settings(_env_file=None, intranet_network_mode=False).effective_network_mode == "developer"
    assert Settings(_env_file=None, network_mode="intranet").effective_network_mode == "intranet"


def test_network_policy_v2_flag_does_not_preserve_legacy_agent_egress_block(monkeypatch):
    from app.services.network_policy import resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "none", raising=False)
    assert resolve_agent_network_context(requires_network=True).allowed is True

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", False, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.intranet_network_mode", True)
    monkeypatch.setattr("app.services.network_policy.settings.intranet_agent_egress_enforced_by_host", False)
    legacy = resolve_agent_network_context(
        requires_network=True,
        environment={"HTTPS_PROXY": "http://corp-proxy.example:8080"},
    )

    assert legacy.allowed is True
    assert legacy.requires_os_network_isolation is False
    assert legacy.sanitized_environment["HTTPS_PROXY"] == "http://corp-proxy.example:8080"


def test_v2_intranet_allows_network_cli_without_codetalk_egress_boundary(monkeypatch):
    from app.services.network_policy import resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "none", raising=False)

    networked = resolve_agent_network_context(requires_network=True)
    offline = resolve_agent_network_context(requires_network=False)

    assert networked.allowed is True
    assert networked.boundary == "none"
    assert networked.requires_os_network_isolation is False
    assert offline.allowed is True
    assert offline.requires_os_network_isolation is False


def test_agent_environment_is_not_rewritten_into_codetalk_approved_proxy_boundary(monkeypatch):
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
    assert context.requires_os_network_isolation is False
    assert context.sanitized_environment["HTTPS_PROXY"] == "https://unknown:secret@evil.example"
    assert context.sanitized_environment["NO_PROXY"] == "evil.example"
    assert context.sanitized_environment["SSL_CERT_FILE"] == "/tmp/evil-ca.pem"
    assert context.sanitized_environment["PIP_INDEX_URL"] == "https://packages.example/simple"
    assert context.sanitized_environment["NPM_CONFIG_REGISTRY"] == "https://packages.example/npm"
    assert "trace.example" not in json.dumps(context.snapshot())
    serialized = json.dumps(context.snapshot())
    assert "secret" not in serialized


def test_agent_environment_preserves_ca_override_channels_as_environment_owned(monkeypatch):
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
    assert context.sanitized_environment["NODE_OPTIONS"] == "--use-openssl-ca"
    assert context.sanitized_environment["SSL_CERT_DIR"] == "/tmp/unapproved-ca-dir"
    assert context.sanitized_environment["GIT_SSL_CAINFO"] == "/tmp/unapproved-git-ca.pem"
    assert context.sanitized_environment["NPM_CONFIG_CAFILE"] == "/tmp/unapproved-npm-ca.pem"


def test_strict_compliance_mode_does_not_require_codetalk_os_network_isolation(monkeypatch):
    from app.services.network_policy import resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "strict_compliance", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "deployment_egress_policy", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.deployment_egress_policy_id", "egress-prod-1", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.strict_compliance_os_network_isolation_enabled", False, raising=False)

    strict = resolve_agent_network_context(requires_network=True)
    assert strict.allowed is True
    assert strict.requires_os_network_isolation is False

    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "developer", raising=False)
    developer = resolve_agent_network_context(
        requires_network=True,
        environment={"LANGSMITH_TRACING": "true", "CODEX_DISABLE_AUTO_UPDATE": "0"},
    )
    assert developer.allowed is True


def test_context_snapshot_never_includes_proxy_credentials_even_when_context_is_allowed(monkeypatch):
    from app.services.network_policy import resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "approved_proxy_gateway", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_url", "https://user:very-secret@gateway.corp.example:8443", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_config_id", "", raising=False)

    context = resolve_agent_network_context(requires_network=True)
    assert "very-secret" not in json.dumps(context.snapshot())
    assert context.require_allowed() is context


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


def test_deployment_egress_policy_setting_does_not_create_codetalk_boundary(monkeypatch):
    from app.services.network_policy import resolve_agent_network_context

    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True, raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.egress_boundary", "deployment_egress_policy", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.deployment_egress_policy_id", "egress-prod-1", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_url", "https://gateway.corp.example:8443", raising=False)
    monkeypatch.setattr("app.services.network_policy.settings.approved_proxy_config_id", "proxy-prod-1", raising=False)

    context = resolve_agent_network_context(requires_network=True)

    assert context.allowed is True
    assert context.boundary == "none"
    assert context.requires_os_network_isolation is False
    assert "HTTPS_PROXY" not in context.sanitized_environment
    assert "HTTP_PROXY" not in context.sanitized_environment
    assert "ALL_PROXY" not in context.sanitized_environment
