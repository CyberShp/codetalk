import json

import pytest


@pytest.mark.asyncio
async def test_network_policy_snapshot_comes_only_from_deployment_settings(client, monkeypatch):
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

    # User-editable values must neither be reflected in the snapshot nor gain
    # authority over the deployment network boundary.
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
        "policy_id": "corp-policy-2026",
        "boundary": "approved_proxy_gateway",
        "approved_proxy_configured": True,
        "approved_proxy_config_id": "proxy-prod-1",
        "approved_no_proxy": True,
        "approved_ca_configured": True,
        "deployment_egress_policy_id": "egress-prod-1",
        "telemetry": "disabled",
        "remote_tracing": "disabled",
        "hosted_mcp": "forbidden",
        "cli_network_ready": True,
        "cli_block_reason": None,
        "cli_remediation": None,
        "source": "deployment",
    }
    serialized = json.dumps(body)
    for secret in ("deploy-secret", "db-secret", "gateway.corp.example", "/etc/codetalk/corp-ca.pem"):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_network_policy_snapshot_reports_strict_mode_cli_block(client, monkeypatch):
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
    assert body["cli_network_ready"] is False
    assert body["cli_block_reason"] == "strict_compliance_os_isolation_required"
    assert "OS 网络隔离" in body["cli_remediation"]


@pytest.mark.asyncio
async def test_llm_probe_returns_redacted_actionable_policy_error(client, monkeypatch):
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
    assert "部署网络策略" in body["message"]
    assert "管理员" in body["message"]
    assert "模型地址未获管理员批准" in body["message"]
    assert "host_not_allowlisted" not in body["message"]
    assert body["code"] == "network_policy_blocked"
    assert "sk-super-secret" not in body["message"]
