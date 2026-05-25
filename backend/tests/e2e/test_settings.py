"""E2E tests for /api/settings endpoints (LLM configs + general settings)."""

import os

import pytest
from httpx import AsyncClient

HAS_DEEPSEEK = bool(os.environ.get("DEEPSEEK_API_KEY", ""))


# -- Helpers --

def _llm_payload(**overrides) -> dict:
    base = {
        "name": "test-llm",
        "api_type": "openai_compat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-test-placeholder",
        "model": "deepseek-chat",
        "max_tokens": 4096,
        "temperature": 0.3,
    }
    base.update(overrides)
    return base


# -- LLM config CRUD --

async def test_list_llm_configs_empty(e2e_client: AsyncClient):
    resp = await e2e_client.get("/api/settings/llm")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_llm_config(e2e_client: AsyncClient):
    resp = await e2e_client.post("/api/settings/llm", json=_llm_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "test-llm"
    assert body["api_type"] == "openai_compat"
    assert body["model"] == "deepseek-chat"
    assert body["id"]
    assert body["created_at"]


async def test_get_llm_config_appears_in_list(e2e_client: AsyncClient):
    create_resp = await e2e_client.post("/api/settings/llm", json=_llm_payload())
    cfg_id = create_resp.json()["id"]

    resp = await e2e_client.get("/api/settings/llm")
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert cfg_id in ids


async def test_update_llm_config(e2e_client: AsyncClient):
    create_resp = await e2e_client.post("/api/settings/llm", json=_llm_payload())
    cfg_id = create_resp.json()["id"]

    resp = await e2e_client.put(
        f"/api/settings/llm/{cfg_id}",
        json={"name": "updated-name", "temperature": 0.7},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "updated-name"
    assert body["temperature"] == 0.7


async def test_update_llm_config_model_flags(e2e_client: AsyncClient):
    """Updating is_chat_model and is_embedding_model covers boolean-to-int conversion."""
    create_resp = await e2e_client.post("/api/settings/llm", json=_llm_payload())
    cfg_id = create_resp.json()["id"]

    resp = await e2e_client.put(
        f"/api/settings/llm/{cfg_id}",
        json={"is_chat_model": True, "is_embedding_model": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_chat_model"] is True
    assert body["is_embedding_model"] is False


async def test_delete_llm_config(e2e_client: AsyncClient):
    create_resp = await e2e_client.post("/api/settings/llm", json=_llm_payload())
    cfg_id = create_resp.json()["id"]

    resp = await e2e_client.delete(f"/api/settings/llm/{cfg_id}")
    assert resp.status_code == 204

    list_resp = await e2e_client.get("/api/settings/llm")
    ids = [c["id"] for c in list_resp.json()]
    assert cfg_id not in ids


async def test_delete_nonexistent_llm_config(e2e_client: AsyncClient):
    resp = await e2e_client.delete("/api/settings/llm/nonexistent-id")
    assert resp.status_code == 404


async def test_create_llm_invalid_api_type(e2e_client: AsyncClient):
    payload = _llm_payload(api_type="invalid_provider")
    resp = await e2e_client.post("/api/settings/llm", json=payload)
    assert resp.status_code == 422


async def test_update_nonexistent_llm_config(e2e_client: AsyncClient):
    resp = await e2e_client.put(
        "/api/settings/llm/nonexistent-id",
        json={"name": "nope"},
    )
    assert resp.status_code == 404


@pytest.mark.skipif(not HAS_DEEPSEEK, reason="DEEPSEEK_API_KEY not set")
async def test_llm_connectivity_test(e2e_client: AsyncClient):
    """Test real LLM connectivity with DeepSeek API."""
    payload = _llm_payload(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
    )
    resp = await e2e_client.post("/api/settings/llm/test", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert "success" in body


# -- General settings --

async def test_get_general_settings(e2e_client: AsyncClient):
    resp = await e2e_client.get("/api/settings/general")
    assert resp.status_code == 200
    body = resp.json()
    assert "proxy_mode" in body
    assert "active_chat_model_id" in body


async def test_update_general_settings(e2e_client: AsyncClient):
    resp = await e2e_client.put(
        "/api/settings/general",
        json={
            "proxy_mode": "none",
            "proxy_url": "",
            "ssl_cert_path": "",
            "active_chat_model_id": "",
            "active_embedding_model_id": "",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["proxy_mode"] == "none"
