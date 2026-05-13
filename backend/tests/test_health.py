"""Tests for the /health endpoint."""


async def test_health_returns_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_is_fast(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 1.0
