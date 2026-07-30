import json

import pytest


@pytest.mark.asyncio
async def test_network_policy_snapshot_reports_codetalk_passthrough_runtime(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "network_policy_v2_enabled", True)
    monkeypatch.setattr(settings, "network_mode", "intranet")
    monkeypatch.setattr(settings, "intranet_network_policy_id", "corp-policy-2026")
    monkeypatch.setattr(settings, "egress_boundary", "approved_proxy_gateway")
    monkeypatch.setattr(
        settings,
        "approved_proxy_url",
        "https://deploy-user:deploy-secret@gateway.corp.example:8443",
    )
    monkeypatch.setattr(settings, "approved_proxy_config_id", "proxy-prod-1")
    monkeypatch.setattr(settings, "approved_no_proxy", "localhost,.corp.example")
    monkeypatch.setattr(settings, "approved_ca_bundle_path", "/etc/codetalk/corp-ca.pem")
    monkeypatch.setattr(settings, "deployment_egress_policy_id", "egress-prod-1")

    # User-editable values and legacy deployment controls must not turn this
    # endpoint back into a network-boundary configuration surface.
    await client.put(
        "/api/settings/general",
        json={
            "proxy_mode": "custom",
            "proxy_url": "https://user:db-secret@untrusted.example:8080",
            "ssl_cert_path": "/tmp/user-ca.pem",
            "active_chat_model_id": "",
            "active_embedding_model_id": "",
            "behavior_claim_audit_model_id": "",
        },
    )

    response = await client.get("/api/settings/network-policy")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "mode": "intranet",
        "policy_id": "codetalk-passthrough",
        "boundary": "none",
        "approved_proxy_configured": False,
        "approved_proxy_config_id": None,
        "approved_no_proxy": False,
        "approved_ca_configured": False,
        "deployment_egress_policy_id": None,
        "telemetry": "managed_by_environment",
        "remote_tracing": "managed_by_environment",
        "hosted_mcp": "managed_by_environment",
        "cli_network_ready": True,
        "cli_block_reason": None,
        "cli_remediation": "CodeTalk 不拦截网络访问；连接结果由运行环境和公司内网决定。",
        "source": "codetalk_runtime",
        "migration_preview": {
            "contract_version": 1,
            "source": "codetalk_runtime",
            "effective_mode": "intranet",
            "read_only": True,
            "automatic_write": False,
            "admin_confirmation_required": False,
            "admin_guidance": None,
        },
    }
    serialized = json.dumps(body)
    for secret in ("deploy-secret", "db-secret", "gateway.corp.example", "/etc/codetalk/corp-ca.pem"):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_network_policy_snapshot_does_not_block_strict_mode_cli(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "network_policy_v2_enabled", True)
    monkeypatch.setattr(settings, "network_mode", "strict_compliance")
    monkeypatch.setattr(settings, "egress_boundary", "deployment_egress_policy")
    monkeypatch.setattr(settings, "deployment_egress_policy_id", "egress-prod-1")
    monkeypatch.setattr(settings, "strict_compliance_os_network_isolation_enabled", False)

    response = await client.get("/api/settings/network-policy")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "strict_compliance"
    assert body["cli_network_ready"] is True
    assert body["cli_block_reason"] is None
    assert "CodeTalk 不拦截网络访问" in body["cli_remediation"]


@pytest.mark.asyncio
async def test_llm_probe_failure_points_to_model_or_runtime_not_policy_gate(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "network_policy_v2_enabled", True)
    monkeypatch.setattr(settings, "network_mode", "intranet")
    monkeypatch.setattr(settings, "intranet_allowed_hosts", [])
    monkeypatch.setattr(settings, "intranet_allowed_cidrs", [])

    response = await client.post(
        "/api/settings/llm/test",
        json={
            "name": "unapproved",
            "api_type": "openai_compat",
            "base_url": "https://unapproved.example/v1",
            "api_key": "sk-super-secret",
            "model": "test-model",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "部署网络策略" not in body["message"]
    assert "管理员" not in body["message"]
    assert "未获管理员批准" not in body["message"]
    assert "host_not_allowlisted" not in body["message"]
    assert body["code"] == "model_connection_failed"
    assert "sk-super-secret" not in body["message"]
