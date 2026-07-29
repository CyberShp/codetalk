"""Phase 7 settings migration contract tests."""

import json

import pytest


@pytest.mark.asyncio
async def test_legacy_network_mode_snapshot_exposes_read_only_migration_preview_without_writes(
    client, monkeypatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "network_mode", None)
    monkeypatch.setattr(settings, "intranet_network_mode", True)
    monkeypatch.setattr(settings, "approved_proxy_url", "https://admin:secret@gateway.corp.example")
    monkeypatch.setattr(settings, "approved_ca_bundle_path", "/etc/codetalk/corp-ca.pem")

    before = await client.get("/api/settings/general")
    response = await client.get("/api/settings/network-policy")
    after = await client.get("/api/settings/general")

    assert response.status_code == 200
    body = response.json()
    assert body["migration_preview"] == {
        "contract_version": 1,
        "source": "legacy_intranet_network_mode",
        "effective_mode": "intranet",
        "read_only": True,
        "automatic_write": False,
        "admin_confirmation_required": True,
        "admin_guidance": (
            "当前有效网络模式仍来自旧版 intranet_network_mode 配置。"
            "请由管理员确认并显式配置 network_mode；预览不会自动写入部署设置。"
        ),
    }
    assert before.json() == after.json()

    serialized = json.dumps(body)
    assert "admin:secret" not in serialized
    assert "gateway.corp.example" not in serialized
    assert "/etc/codetalk/corp-ca.pem" not in serialized


@pytest.mark.asyncio
async def test_explicit_network_mode_snapshot_marks_migration_confirmed(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "network_mode", "strict_compliance")
    monkeypatch.setattr(settings, "intranet_network_mode", True)

    response = await client.get("/api/settings/network-policy")

    assert response.status_code == 200
    assert response.json()["migration_preview"] == {
        "contract_version": 1,
        "source": "network_mode",
        "effective_mode": "strict_compliance",
        "read_only": True,
        "automatic_write": False,
        "admin_confirmation_required": False,
        "admin_guidance": None,
    }
