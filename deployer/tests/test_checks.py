"""E2E tests for GET /api/checks — runs real environment checks."""


async def test_checks_returns_200(client):
    resp = await client.get("/api/checks")
    assert resp.status_code == 200


async def test_checks_response_is_dict_with_checks_key(client):
    resp = await client.get("/api/checks")
    body = resp.json()
    assert "checks" in body
    assert isinstance(body["checks"], list)


async def test_checks_each_item_has_required_fields(client):
    resp = await client.get("/api/checks")
    for item in resp.json()["checks"]:
        assert "name" in item, f"missing 'name' in {item}"
        assert "status" in item, f"missing 'status' in {item}"
        assert "message" in item, f"missing 'message' in {item}"


async def test_checks_status_values_are_valid(client):
    resp = await client.get("/api/checks")
    valid_statuses = {"pass", "fail", "warn", "skip"}
    for item in resp.json()["checks"]:
        assert item["status"] in valid_statuses, f"unexpected status: {item['status']}"


async def test_checks_python_item_present_native(client):
    resp = await client.get("/api/checks?mode=native")
    names = [item["name"].lower() for item in resp.json()["checks"]]
    assert any("python" in n for n in names)


async def test_checks_git_item_present_native(client):
    resp = await client.get("/api/checks?mode=native")
    names = [item["name"].lower() for item in resp.json()["checks"]]
    assert any("git" in n for n in names)


async def test_checks_idempotent(client):
    r1 = await client.get("/api/checks")
    r2 = await client.get("/api/checks")
    assert r1.status_code == r2.status_code == 200
    c1 = {i["name"] for i in r1.json()["checks"]}
    c2 = {i["name"] for i in r2.json()["checks"]}
    assert c1 == c2


async def test_checks_mode_param_accepted(client):
    resp = await client.get("/api/checks?mode=native")
    assert resp.status_code == 200
    assert "checks" in resp.json()
