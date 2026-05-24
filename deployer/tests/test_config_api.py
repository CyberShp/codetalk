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
    await client.post("/api/config", json={"portFrontend": 3005, "portBackend": 8100})
    await client.post("/api/config", json={"portBackend": 9000})
    resp = await client.get("/api/config")
    body = resp.json()
    assert body.get("portFrontend") == 3005
    assert body.get("portBackend") == 9000


async def test_post_config_returns_ok_true(client):
    resp = await client.post("/api/config", json={"mode": "native"})
    assert resp.json().get("ok") is True


async def test_config_llm_provider_switch_redistributes_key(client):
    # Save openai config
    await client.post("/api/config", json={
        "llmProvider": "openai",
        "apiKey": "sk-openai-key",
    })
    # Switch to anthropic
    await client.post("/api/config", json={
        "llmProvider": "anthropic",
        "apiKey": "ant-key",
    })
    get_resp = await client.get("/api/config")
    data = get_resp.json()
    assert data.get("llmProvider") == "anthropic"
    assert data.get("apiKey") == "ant-key"


async def test_get_config_mapped_keys_are_camelcase(client):
    resp = await client.get("/api/config")
    body = resp.json()
    # Keys that ARE in the mapping must come back camelCase
    assert "backend_port" not in body
    assert "frontend_port" not in body
    assert "llm_provider" not in body
    assert "portBackend" in body or "portFrontend" in body or "mode" in body
