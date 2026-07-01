"""E2E tests for config API endpoints (/api/config GET/POST)."""


async def test_get_config_returns_200(client):
    resp = await client.get("/api/config")
    assert resp.status_code == 200


async def test_get_config_contains_required_fields(client):
    resp = await client.get("/api/config")
    body = resp.json()
    assert "mode" in body
    assert "portFrontend" in body or "portBackend" in body


async def test_post_config_and_get_roundtrip(client):
    payload = {"mode": "native", "portBackend": 9876}
    post_resp = await client.post("/api/config", json=payload)
    assert post_resp.status_code == 200
    get_resp = await client.get("/api/config")
    body = get_resp.json()
    assert body.get("portBackend") == 9876


async def test_post_config_camelcase_normalized(client):
    await client.post("/api/config", json={"llmProvider": "anthropic", "apiKey": "ant-test"})
    resp = await client.get("/api/config")
    body = resp.json()
    assert body.get("llmProvider") == "anthropic"


async def test_post_config_unknown_field_tolerated(client):
    resp = await client.post("/api/config", json={"unknownField": "hello"})
    assert resp.status_code == 200


async def test_post_config_empty_body_tolerated(client):
    resp = await client.post("/api/config", json={})
    assert resp.status_code == 200


async def test_post_config_partial_merge_preserves_existing(client):
    await client.post("/api/config", json={"portFrontend": 3003, "portBackend": 3004})
    await client.post("/api/config", json={"portBackend": 9000})
    resp = await client.get("/api/config")
    body = resp.json()
    assert body.get("portFrontend") == 3003
    assert body.get("portBackend") == 9000


async def test_post_config_returns_ok_true(client):
    resp = await client.post("/api/config", json={"mode": "native"})
    assert resp.json().get("ok") is True


async def test_config_llm_provider_switch_redistributes_key(client):
    # Save openai config
    openai_key = "sk-openai-key"
    await client.post("/api/config", json={
        "llmProvider": "openai",
        "apiKey": openai_key,
    })
    # Switch to anthropic
    anthropic_key = "ant-key"
    await client.post("/api/config", json={
        "llmProvider": "anthropic",
        "apiKey": anthropic_key,
    })
    get_resp = await client.get("/api/config")
    data = get_resp.json()
    assert data.get("llmProvider") == "anthropic"
    assert data.get("apiKeyConfigured") is True
    assert data.get("apiKeyPreview") == "ant-••••••••"
    assert data.get("apiKey") is None
    assert openai_key not in get_resp.text
    assert anthropic_key not in get_resp.text


async def test_get_config_never_returns_full_provider_key(client):
    secret = "sk-deployer-config-leak-test-1234567890"
    await client.post("/api/config", json={
        "llmProvider": "openai",
        "apiKey": secret,
    })

    resp = await client.get("/api/config")
    body = resp.json()

    assert body.get("apiKeyConfigured") is True
    assert body.get("apiKeyPreview") == "sk-d••••••••"
    assert "apiKey" not in body
    assert secret not in resp.text
    assert "sk-deployer-config-leak-test" not in resp.text
    assert "openai_api_key" not in body
    assert "anthropic_api_key" not in body
    assert "google_api_key" not in body


async def test_get_config_safe_metadata_does_not_pollute_saved_config(client, isolated_config):
    secret = "sk-roundtrip-still-private-1234567890"
    await client.post("/api/config", json={
        "llmProvider": "openai",
        "apiKey": secret,
    })
    get_resp = await client.get("/api/config")
    safe_payload = get_resp.json()

    post_resp = await client.post("/api/config", json=safe_payload)
    assert post_resp.status_code == 200

    raw = isolated_config.read_text(encoding="utf-8")
    assert secret in raw
    assert "api_key_configured" not in raw
    assert "api_key_preview" not in raw

    final_resp = await client.get("/api/config")
    assert secret not in final_resp.text
    assert final_resp.json().get("apiKeyConfigured") is True


async def test_get_config_mapped_keys_are_camelcase(client):
    resp = await client.get("/api/config")
    body = resp.json()
    # Keys that ARE in the mapping must come back camelCase
    assert "backend_port" not in body
    assert "frontend_port" not in body
    assert "llm_provider" not in body
    assert "portBackend" in body or "portFrontend" in body or "mode" in body
