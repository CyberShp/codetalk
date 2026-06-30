"""Tests for /api/settings/llm and /api/settings/general endpoints."""

from unittest.mock import AsyncMock, patch


_LLM = {
    "name": "test-claude",
    "api_type": "anthropic",
    "base_url": "https://api.anthropic.com",
    "api_key": "sk-ant-test",
    "model": "claude-3-5-sonnet-20241022",
}


# ---------------------------------------------------------------------------
# LLM config CRUD
# ---------------------------------------------------------------------------


async def test_list_llm_configs_empty(client):
    response = await client.get("/api/settings/llm")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_llm_config(client):
    response = await client.post("/api/settings/llm", json=_LLM)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "test-claude"
    assert data["api_type"] == "anthropic"
    assert data["model"] == "claude-3-5-sonnet-20241022"
    assert "id" in data
    assert "created_at" in data


async def test_create_llm_config_does_not_expose_api_key(client):
    response = await client.post("/api/settings/llm", json=_LLM)
    assert response.status_code == 201
    assert "api_key" not in response.json()


async def test_create_llm_config_invalid_api_type(client):
    bad = {**_LLM, "api_type": "unsupported_provider"}
    response = await client.post("/api/settings/llm", json=bad)
    assert response.status_code == 422


async def test_create_llm_config_openai_compat(client):
    payload = {**_LLM, "name": "openai-test", "api_type": "openai_compat"}
    response = await client.post("/api/settings/llm", json=payload)
    assert response.status_code == 201
    assert response.json()["api_type"] == "openai_compat"


async def test_create_llm_config_with_optional_fields(client):
    payload = {
        **_LLM,
        "max_tokens": 8192,
        "temperature": 0.7,
        "config_json": '{"top_p": 0.9}',
        "is_chat_model": True,
        "is_embedding_model": True,
    }
    response = await client.post("/api/settings/llm", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["max_tokens"] == 8192
    assert data["temperature"] == 0.7
    assert data["config_json"] == '{"top_p": 0.9}'
    assert data["is_chat_model"] is True
    assert data["is_embedding_model"] is True


async def test_list_llm_configs_after_create(client):
    await client.post("/api/settings/llm", json=_LLM)
    await client.post(
        "/api/settings/llm",
        json={**_LLM, "name": "test-openai", "api_type": "openai_compat"},
    )

    response = await client.get("/api/settings/llm")
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_update_llm_config(client):
    created = await client.post("/api/settings/llm", json=_LLM)
    cfg_id = created.json()["id"]

    response = await client.put(
        f"/api/settings/llm/{cfg_id}",
        json={"model": "claude-opus-4-7", "max_tokens": 8192},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["model"] == "claude-opus-4-7"
    assert data["max_tokens"] == 8192
    assert data["name"] == "test-claude"


async def test_update_llm_config_boolean_fields(client):
    created = await client.post("/api/settings/llm", json=_LLM)
    cfg_id = created.json()["id"]

    response = await client.put(
        f"/api/settings/llm/{cfg_id}",
        json={"is_chat_model": False, "is_embedding_model": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["is_chat_model"] is False
    assert data["is_embedding_model"] is True


async def test_update_llm_config_no_changes(client):
    created = await client.post("/api/settings/llm", json=_LLM)
    cfg_id = created.json()["id"]

    response = await client.put(f"/api/settings/llm/{cfg_id}", json={})
    assert response.status_code == 200
    assert response.json()["name"] == "test-claude"


async def test_update_llm_config_not_found(client):
    response = await client.put(
        "/api/settings/llm/nonexistent-id",
        json={"model": "gpt-5"},
    )
    assert response.status_code == 404


async def test_delete_llm_config(client):
    created = await client.post("/api/settings/llm", json=_LLM)
    cfg_id = created.json()["id"]

    delete_resp = await client.delete(f"/api/settings/llm/{cfg_id}")
    assert delete_resp.status_code == 204

    list_resp = await client.get("/api/settings/llm")
    assert list_resp.json() == []


async def test_delete_llm_config_clears_active_references(client, db):
    created = await client.post("/api/settings/llm", json=_LLM)
    cfg_id = created.json()["id"]

    await client.put(
        "/api/settings/general",
        json={
            "proxy_mode": "none",
            "proxy_url": "",
            "ssl_cert_path": "",
            "active_chat_model_id": cfg_id,
            "active_embedding_model_id": cfg_id,
        },
    )

    delete_resp = await client.delete(f"/api/settings/llm/{cfg_id}")
    assert delete_resp.status_code == 204

    general = await client.get("/api/settings/general")
    data = general.json()
    assert data["active_chat_model_id"] == ""
    assert data["active_embedding_model_id"] == ""


async def test_delete_llm_config_not_found(client):
    response = await client.delete("/api/settings/llm/nonexistent-id")
    assert response.status_code == 404


async def test_agent_provider_settings_roundtrip_updates_runtime_provider_matrix(client):
    payload = {
        "claude_code_command": "ccr code",
        "claude_code_config_path": "C:/innernet/ccr/config-router.json",
        "claude_code_fallback_commands": ["claude"],
        "claude_code_mcp_profiles": ["codehub-readonly"],
        "opencode_command": "",
        "opencode_fallback_commands": [],
        "opencode_mcp_profiles": [],
        "external_agent_custom_providers": [
            {
                "id": "corp-agent",
                "command": "corp-agent run --json",
                "prompt_transport": "stdin",
                "env_hints": {
                    "CORP_AGENT_PROFILE": "innernet",
                    "CORP_AGENT_TOKEN": "token=secret-value",
                },
                "supports_mcp": True,
                "mcp_profiles": ["codehub-mcp"],
            }
        ],
    }

    update_resp = await client.put("/api/settings/agent-providers", json=payload)

    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["claude_code_command"] == "ccr code"
    assert body["claude_code_config_path"] == "C:/innernet/ccr/config-router.json"
    assert body["claude_code_fallback_commands"] == ["claude"]
    assert body["external_agent_custom_providers"][0]["id"] == "corp-agent"
    assert body["external_agent_custom_providers"][0]["env_hints"] == {
        "CORP_AGENT_PROFILE": "innernet",
        "CORP_AGENT_TOKEN": "token=secret-value",
    }

    loaded_resp = await client.get("/api/settings/agent-providers")
    assert loaded_resp.status_code == 200
    assert loaded_resp.json() == body

    from app.services.external_agent_discovery import (
        external_agent_provider_capabilities,
        external_agent_provider_spec,
        split_agent_command,
    )

    claude_spec = external_agent_provider_spec("claude-code")
    corp_spec = external_agent_provider_spec("corp-agent")
    assert claude_spec is not None
    assert corp_spec is not None
    assert claude_spec.command == "ccr code"
    assert claude_spec.mcp_profiles == ["codehub-readonly"]
    assert split_agent_command(corp_spec.command) == ["corp-agent", "run", "--json"]
    assert corp_spec.env_hints["CORP_AGENT_PROFILE"] == "innernet"
    assert corp_spec.env_hints["CORP_AGENT_TOKEN"] == "token=secret-value"
    assert external_agent_provider_capabilities("corp-agent")["supports_mcp"] is True
    assert external_agent_provider_capabilities("corp-agent")["env_hint_keys"] == [
        "CORP_AGENT_PROFILE",
        "CORP_AGENT_TOKEN",
    ]


async def test_agent_provider_settings_rejects_invalid_custom_provider_json(client):
    response = await client.put(
        "/api/settings/agent-providers",
        json={
            "claude_code_command": "ccr code",
            "external_agent_custom_providers": [
                {"id": "missing-command", "prompt_transport": "stdin"}
            ],
        },
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# LLM test connection
# ---------------------------------------------------------------------------


async def test_llm_connection_anthropic_success(client):
    mock_client = AsyncMock()
    mock_client.health_check = AsyncMock(return_value=(True, "connected"))
    mock_client.close = AsyncMock()

    with patch(
        "app.llm.anthropic.AnthropicClient", return_value=mock_client
    ):
        response = await client.post("/api/settings/llm/test", json=_LLM)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["message"] == "connected"


async def test_llm_connection_openai_success(client):
    mock_client = AsyncMock()
    mock_client.health_check = AsyncMock(return_value=(True, "ok"))
    mock_client.close = AsyncMock()

    payload = {**_LLM, "api_type": "openai_compat"}
    with patch(
        "app.llm.openai_compat.OpenAICompatClient", return_value=mock_client
    ):
        response = await client.post("/api/settings/llm/test", json=payload)

    assert response.status_code == 200
    assert response.json()["success"] is True


async def test_llm_connection_failure(client):
    with patch(
        "app.llm.anthropic.AnthropicClient",
        side_effect=ConnectionError("refused"),
    ):
        response = await client.post("/api/settings/llm/test", json=_LLM)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "refused" in data["message"]


async def test_llm_connection_failure_redacts_api_key_from_message(client):
    secret = "sk-settings-secret-123"
    payload = {**_LLM, "api_key": secret}
    with patch(
        "app.llm.anthropic.AnthropicClient",
        side_effect=ConnectionError(
            f"request failed Authorization: Bearer {secret}; api_key={secret}"
        ),
    ):
        response = await client.post("/api/settings/llm/test", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "request failed" in data["message"]
    assert secret not in data["message"]
    assert "<redacted>" in data["message"]


async def test_llm_connection_unknown_api_type(client):
    payload = {**_LLM, "api_type": "unknown_type"}
    response = await client.post("/api/settings/llm/test", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "未知" in response.json()["message"]


async def test_llm_connection_uses_proxy_settings(client, db):
    await client.put(
        "/api/settings/general",
        json={
            "proxy_mode": "custom",
            "proxy_url": "http://proxy:8080",
            "ssl_cert_path": "/path/to/cert.pem",
            "active_chat_model_id": "",
            "active_embedding_model_id": "",
        },
    )

    mock_client = AsyncMock()
    mock_client.health_check = AsyncMock(return_value=(True, "ok"))
    mock_client.close = AsyncMock()

    with patch(
        "app.llm.anthropic.AnthropicClient", return_value=mock_client
    ) as mock_cls:
        response = await client.post("/api/settings/llm/test", json=_LLM)

    assert response.status_code == 200
    call_kwargs = mock_cls.call_args[1]
    assert call_kwargs["proxy_url"] == "http://proxy:8080"
    assert call_kwargs["ssl_cert_path"] == "/path/to/cert.pem"


# ---------------------------------------------------------------------------
# General settings
# ---------------------------------------------------------------------------


async def test_get_general_settings_defaults(client):
    response = await client.get("/api/settings/general")
    assert response.status_code == 200
    data = response.json()
    assert data["proxy_mode"] == "none"
    assert data["proxy_url"] == ""
    assert data["ssl_cert_path"] == ""
    assert data["active_chat_model_id"] == ""
    assert data["active_embedding_model_id"] == ""


async def test_update_general_settings(client):
    payload = {
        "proxy_mode": "custom",
        "proxy_url": "http://proxy.example.com:8080",
        "ssl_cert_path": "",
        "active_chat_model_id": "abc-123",
        "active_embedding_model_id": "",
    }
    put_resp = await client.put("/api/settings/general", json=payload)
    assert put_resp.status_code == 200

    get_resp = await client.get("/api/settings/general")
    data = get_resp.json()
    assert data["proxy_mode"] == "custom"
    assert data["proxy_url"] == "http://proxy.example.com:8080"
    assert data["active_chat_model_id"] == "abc-123"


async def test_update_general_settings_idempotent(client):
    payload = {
        "proxy_mode": "system",
        "proxy_url": "",
        "ssl_cert_path": "",
        "active_chat_model_id": "",
        "active_embedding_model_id": "",
    }
    await client.put("/api/settings/general", json=payload)
    await client.put("/api/settings/general", json=payload)

    get_resp = await client.get("/api/settings/general")
    assert get_resp.status_code == 200
    assert get_resp.json()["proxy_mode"] == "system"


async def test_update_general_settings_all_proxy_modes(client):
    for mode in ("none", "system", "custom"):
        payload = {
            "proxy_mode": mode,
            "proxy_url": "",
            "ssl_cert_path": "",
            "active_chat_model_id": "",
            "active_embedding_model_id": "",
        }
        resp = await client.put("/api/settings/general", json=payload)
        assert resp.status_code == 200
        assert resp.json()["proxy_mode"] == mode


# ---------------------------------------------------------------------------
# Active model settings
# ---------------------------------------------------------------------------

_EMBED_LLM = {
    "name": "internal-embed",
    "api_type": "openai_compat",
    "base_url": "http://internal.ai/v1",
    "api_key": "sk-internal",
    "model": "qwen-embed-v3",
    "is_chat_model": False,
    "is_embedding_model": True,
}

_CHAT_LLM = {
    "name": "internal-chat",
    "api_type": "openai_compat",
    "base_url": "http://internal.ai/v1",
    "api_key": "sk-internal",
    "model": "qw2.5",
    "is_chat_model": True,
    "is_embedding_model": False,
}


async def test_update_general_settings_persists_active_embedding_model(client):
    embed_resp = await client.post("/api/settings/llm", json=_EMBED_LLM)
    embed_id = embed_resp.json()["id"]

    response = await client.put(
        "/api/settings/general",
        json={
            "proxy_mode": "none",
            "proxy_url": "",
            "ssl_cert_path": "",
            "active_chat_model_id": "",
            "active_embedding_model_id": embed_id,
        },
    )

    assert response.status_code == 200
    assert response.json()["active_embedding_model_id"] == embed_id
    persisted = await client.get("/api/settings/general")
    assert persisted.json()["active_embedding_model_id"] == embed_id


async def test_update_general_settings_persists_active_chat_model(client):
    chat_resp = await client.post("/api/settings/llm", json=_CHAT_LLM)
    chat_id = chat_resp.json()["id"]

    response = await client.put(
        "/api/settings/general",
        json={
            "proxy_mode": "none",
            "proxy_url": "",
            "ssl_cert_path": "",
            "active_chat_model_id": chat_id,
            "active_embedding_model_id": "",
        },
    )

    assert response.status_code == 200
    assert response.json()["active_chat_model_id"] == chat_id
    persisted = await client.get("/api/settings/general")
    assert persisted.json()["active_chat_model_id"] == chat_id


async def test_update_general_settings_allows_missing_active_model_reference(client):
    resp = await client.put(
        "/api/settings/general",
        json={
            "proxy_mode": "none",
            "proxy_url": "",
            "ssl_cert_path": "",
            "active_chat_model_id": "nonexistent-id",
            "active_embedding_model_id": "",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["active_chat_model_id"] == "nonexistent-id"


_EMBED_LLM_SEPARATE = {
    "name": "embed-separate",
    "api_type": "openai_compat",
    "base_url": "http://embed.internal/v1",
    "api_key": "sk-embed-key",
    "model": "bge-large-v3",
    "is_chat_model": False,
    "is_embedding_model": True,
}


async def test_update_general_settings_persists_separate_embedding_model(client):
    embed_resp = await client.post("/api/settings/llm", json=_EMBED_LLM_SEPARATE)
    embed_id = embed_resp.json()["id"]

    response = await client.put(
        "/api/settings/general",
        json={
            "proxy_mode": "none",
            "proxy_url": "",
            "ssl_cert_path": "",
            "active_chat_model_id": "",
            "active_embedding_model_id": embed_id,
        },
    )

    assert response.status_code == 200
    assert response.json()["active_embedding_model_id"] == embed_id


async def test_update_general_settings_keeps_embedding_config_data_isolated(client):
    payload = {
        **_EMBED_LLM_SEPARATE,
        "base_url": "http://embed.internal",
        "model": "bge-m3",
    }
    embed_resp = await client.post("/api/settings/llm", json=payload)
    embed_id = embed_resp.json()["id"]

    await client.put(
        "/api/settings/general",
        json={
            "proxy_mode": "none",
            "proxy_url": "",
            "ssl_cert_path": "",
            "active_chat_model_id": "",
            "active_embedding_model_id": embed_id,
        },
    )

    configs = await client.get("/api/settings/llm")
    [stored] = configs.json()
    assert stored["id"] == embed_id
    assert stored["base_url"] == "http://embed.internal"
    assert stored["model"] == "bge-m3"
    assert "api_key" not in stored


async def test_update_general_settings_clears_active_chat_model(client):
    chat_resp = await client.post("/api/settings/llm", json=_CHAT_LLM)
    chat_id = chat_resp.json()["id"]

    await client.put(
        "/api/settings/general",
        json={"proxy_mode": "none", "proxy_url": "", "ssl_cert_path": "",
              "active_chat_model_id": chat_id, "active_embedding_model_id": ""},
    )
    assert (await client.get("/api/settings/general")).json()["active_chat_model_id"] == chat_id

    response = await client.put(
        "/api/settings/general",
        json={"proxy_mode": "none", "proxy_url": "", "ssl_cert_path": "",
              "active_chat_model_id": "", "active_embedding_model_id": ""},
    )
    assert response.status_code == 200
    assert response.json()["active_chat_model_id"] == ""


async def test_update_general_settings_clears_active_embedding_model(client):
    embed_resp = await client.post("/api/settings/llm", json=_EMBED_LLM_SEPARATE)
    embed_id = embed_resp.json()["id"]

    await client.put(
        "/api/settings/general",
        json={"proxy_mode": "none", "proxy_url": "", "ssl_cert_path": "",
              "active_chat_model_id": "", "active_embedding_model_id": embed_id},
    )
    assert (await client.get("/api/settings/general")).json()["active_embedding_model_id"] == embed_id

    response = await client.put(
        "/api/settings/general",
        json={"proxy_mode": "none", "proxy_url": "", "ssl_cert_path": "",
              "active_chat_model_id": "", "active_embedding_model_id": ""},
    )
    assert response.status_code == 200
    assert response.json()["active_embedding_model_id"] == ""


async def test_settings_config_has_no_deepwiki_runtime_side_effects():
    from app.config import settings as app_settings

    assert not hasattr(app_settings, "deepwiki_path")
    assert not hasattr(app_settings, "deepwiki_api_port")
    assert not hasattr(app_settings, "deepwiki_ui_port")


async def test_settings_backend_port_defaults_to_public_local_api_port(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("CODETALK_BACKEND_PORT", raising=False)

    assert Settings().backend_port == 3004


async def test_settings_backend_port_can_be_overridden_for_isolated_runs(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("CODETALK_BACKEND_PORT", "39104")

    assert Settings().backend_port == 39104


# ---------------------------------------------------------------------------
# Active model references on LLM config mutation
# ---------------------------------------------------------------------------


async def test_update_active_chat_config_keeps_active_reference(client):
    chat_resp = await client.post("/api/settings/llm", json=_CHAT_LLM)
    chat_id = chat_resp.json()["id"]

    await client.put(
        "/api/settings/general",
        json={"proxy_mode": "none", "proxy_url": "", "ssl_cert_path": "",
              "active_chat_model_id": chat_id, "active_embedding_model_id": ""},
    )

    update_resp = await client.put(f"/api/settings/llm/{chat_id}", json={"model": "qw3-turbo"})
    assert update_resp.status_code == 200
    assert update_resp.json()["model"] == "qw3-turbo"

    general = await client.get("/api/settings/general")
    assert general.json()["active_chat_model_id"] == chat_id


async def test_update_active_embedding_config_keeps_active_reference(client):
    embed_resp = await client.post("/api/settings/llm", json=_EMBED_LLM_SEPARATE)
    embed_id = embed_resp.json()["id"]

    await client.put(
        "/api/settings/general",
        json={"proxy_mode": "none", "proxy_url": "", "ssl_cert_path": "",
              "active_chat_model_id": "", "active_embedding_model_id": embed_id},
    )

    update_resp = await client.put(f"/api/settings/llm/{embed_id}", json={"model": "bge-m3"})
    assert update_resp.status_code == 200
    assert update_resp.json()["model"] == "bge-m3"

    general = await client.get("/api/settings/general")
    assert general.json()["active_embedding_model_id"] == embed_id


async def test_delete_active_config_clears_active_model_reference(client):
    chat_resp = await client.post("/api/settings/llm", json=_CHAT_LLM)
    chat_id = chat_resp.json()["id"]

    await client.put(
        "/api/settings/general",
        json={"proxy_mode": "none", "proxy_url": "", "ssl_cert_path": "",
              "active_chat_model_id": chat_id, "active_embedding_model_id": ""},
    )
    assert (await client.get("/api/settings/general")).json()["active_chat_model_id"] == chat_id

    delete_resp = await client.delete(f"/api/settings/llm/{chat_id}")
    assert delete_resp.status_code == 204

    general = await client.get("/api/settings/general")
    assert general.json()["active_chat_model_id"] == ""
