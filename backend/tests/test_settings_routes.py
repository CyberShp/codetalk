"""Route-level contracts for the current settings API."""

from unittest.mock import AsyncMock, patch


_CHAT_LLM = {
    "name": "route-chat",
    "api_type": "openai_compat",
    "base_url": "https://llm.example/v1",
    "api_key": "sk-route-test",
    "model": "route-chat-model",
    "is_chat_model": True,
    "is_embedding_model": False,
}


async def test_settings_llm_route_contract_hides_api_key(client):
    created = await client.post("/api/settings/llm", json=_CHAT_LLM)

    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "route-chat"
    assert body["api_type"] == "openai_compat"
    assert body["base_url"] == "https://llm.example/v1"
    assert body["model"] == "route-chat-model"
    assert body["is_chat_model"] is True
    assert body["is_embedding_model"] is False
    assert "api_key" not in body

    listed = await client.get("/api/settings/llm")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert "api_key" not in listed.json()[0]


async def test_settings_general_route_tracks_active_models(client):
    created = await client.post("/api/settings/llm", json=_CHAT_LLM)
    cfg_id = created.json()["id"]

    saved = await client.put(
        "/api/settings/general",
        json={
            "proxy_mode": "custom",
            "proxy_url": "http://proxy.example:8080",
            "ssl_cert_path": "/tmp/cert.pem",
            "active_chat_model_id": cfg_id,
            "active_embedding_model_id": "",
        },
    )

    assert saved.status_code == 200
    assert saved.json()["active_chat_model_id"] == cfg_id

    loaded = await client.get("/api/settings/general")
    assert loaded.status_code == 200
    assert loaded.json() == saved.json()


async def test_settings_delete_active_llm_route_clears_reference(client):
    created = await client.post("/api/settings/llm", json=_CHAT_LLM)
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

    deleted = await client.delete(f"/api/settings/llm/{cfg_id}")

    assert deleted.status_code == 204
    loaded = await client.get("/api/settings/general")
    assert loaded.json()["active_chat_model_id"] == ""
    assert loaded.json()["active_embedding_model_id"] == ""


async def test_settings_llm_test_route_uses_current_client_contract(client):
    mock_client = AsyncMock()
    mock_client.health_check = AsyncMock(return_value=(True, "route connected"))
    mock_client.close = AsyncMock()

    with patch("app.llm.openai_compat.OpenAICompatClient", return_value=mock_client) as mock_cls:
        response = await client.post("/api/settings/llm/test", json=_CHAT_LLM)

    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "route connected"}
    call_kwargs = mock_cls.call_args.kwargs
    assert call_kwargs["base_url"] == "https://llm.example/v1"
    assert call_kwargs["api_key"] == "sk-route-test"
    assert call_kwargs["model"] == "route-chat-model"
