"""Tests for /api/settings/llm and /api/settings/general endpoints."""

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


async def test_list_llm_configs_after_create(client):
    await client.post("/api/settings/llm", json=_LLM)
    await client.post("/api/settings/llm", json={**_LLM, "name": "test-openai", "api_type": "openai_compat"})

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


async def test_delete_llm_config_not_found(client):
    response = await client.delete("/api/settings/llm/nonexistent-id")
    assert response.status_code == 404


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
