"""E2E tests for /api/prompts endpoints."""

import uuid

from httpx import AsyncClient


async def test_list_prompts_has_default(e2e_client: AsyncClient):
    """After DB init, the system default template should exist."""
    resp = await e2e_client.get("/api/prompts")
    assert resp.status_code == 200
    templates = resp.json()
    assert len(templates) >= 1
    system_templates = [t for t in templates if t["is_system"]]
    assert len(system_templates) >= 1


async def test_create_prompt_template(e2e_client: AsyncClient):
    resp = await e2e_client.post(
        "/api/prompts",
        json={"name": "Custom Template", "content": "Analyze {{repo}} for security."},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Custom Template"
    assert body["is_system"] is False


async def test_get_prompt_template(e2e_client: AsyncClient):
    create_resp = await e2e_client.post(
        "/api/prompts",
        json={"name": "Get Test", "content": "Template content here."},
    )
    tpl_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/prompts/{tpl_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == tpl_id


async def test_update_prompt_template(e2e_client: AsyncClient):
    create_resp = await e2e_client.post(
        "/api/prompts",
        json={"name": "To Update", "content": "Original content."},
    )
    tpl_id = create_resp.json()["id"]

    resp = await e2e_client.put(
        f"/api/prompts/{tpl_id}",
        json={"name": "Updated Name", "content": "Updated content."},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"


async def test_delete_prompt_template(e2e_client: AsyncClient):
    create_resp = await e2e_client.post(
        "/api/prompts",
        json={"name": "To Delete", "content": "Will be deleted."},
    )
    tpl_id = create_resp.json()["id"]

    resp = await e2e_client.delete(f"/api/prompts/{tpl_id}")
    assert resp.status_code == 204

    get_resp = await e2e_client.get(f"/api/prompts/{tpl_id}")
    assert get_resp.status_code == 404


async def test_delete_system_template_forbidden(e2e_client: AsyncClient):
    """System templates should not be deletable."""
    resp = await e2e_client.delete("/api/prompts/system-default")
    assert resp.status_code == 403


async def test_update_system_template_forbidden(e2e_client: AsyncClient):
    """System templates should not be updatable."""
    resp = await e2e_client.put(
        "/api/prompts/system-default",
        json={"name": "Hacked"},
    )
    assert resp.status_code == 403


async def test_get_nonexistent_template(e2e_client: AsyncClient):
    resp = await e2e_client.get(f"/api/prompts/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_create_template_empty_name_returns_422(e2e_client: AsyncClient):
    """Creating a template with an empty name returns 422."""
    resp = await e2e_client.post(
        "/api/prompts",
        json={"name": "   ", "content": "Some content."},
    )
    assert resp.status_code == 422


async def test_create_template_empty_content_returns_422(e2e_client: AsyncClient):
    """Creating a template with empty content returns 422."""
    resp = await e2e_client.post(
        "/api/prompts",
        json={"name": "Valid Name", "content": "   "},
    )
    assert resp.status_code == 422


async def test_update_nonexistent_template_returns_404(e2e_client: AsyncClient):
    """Updating a non-existent template returns 404."""
    resp = await e2e_client.put(
        f"/api/prompts/{uuid.uuid4()}",
        json={"name": "New Name"},
    )
    assert resp.status_code == 404


async def test_update_template_empty_name_returns_422(e2e_client: AsyncClient):
    """Updating a template with an empty name returns 422."""
    create_resp = await e2e_client.post(
        "/api/prompts",
        json={"name": "Update Target", "content": "Content here."},
    )
    tpl_id = create_resp.json()["id"]

    resp = await e2e_client.put(
        f"/api/prompts/{tpl_id}",
        json={"name": "   "},
    )
    assert resp.status_code == 422


async def test_update_template_empty_content_returns_422(e2e_client: AsyncClient):
    """Updating a template with empty content returns 422."""
    create_resp = await e2e_client.post(
        "/api/prompts",
        json={"name": "Content Target", "content": "Content here."},
    )
    tpl_id = create_resp.json()["id"]

    resp = await e2e_client.put(
        f"/api/prompts/{tpl_id}",
        json={"content": "   "},
    )
    assert resp.status_code == 422


async def test_delete_nonexistent_template_returns_404(e2e_client: AsyncClient):
    """Deleting a non-existent template returns 404."""
    resp = await e2e_client.delete(f"/api/prompts/{uuid.uuid4()}")
    assert resp.status_code == 404
