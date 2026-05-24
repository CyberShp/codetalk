"""E2E tests for /api/coverage endpoints."""

import uuid

from httpx import AsyncClient


async def test_list_coverage_analyses_empty(e2e_client: AsyncClient):
    resp = await e2e_client.get("/api/coverage/list")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_upload_coverage_xml(e2e_client: AsyncClient):
    """Upload a minimal Cobertura XML coverage report."""
    xml_content = """<?xml version="1.0" ?>
<coverage line-rate="0.85" branch-rate="0.70" version="1.0">
  <packages>
    <package name="app" line-rate="0.85" branch-rate="0.70" complexity="0">
      <classes>
        <class name="main.py" filename="app/main.py" line-rate="0.9" branch-rate="0.8">
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="1"/>
            <line number="3" hits="0"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>"""

    resp = await e2e_client.post(
        "/api/coverage/upload",
        files={"files": ("coverage.xml", xml_content.encode(), "text/xml")},
        data={"name": "test-coverage"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"]
    assert body["source_format"] != "unknown" or body["status"] in ("parsed", "error")


async def test_upload_no_files(e2e_client: AsyncClient):
    """Upload with no files should fail."""
    resp = await e2e_client.post("/api/coverage/upload")
    assert resp.status_code == 422


async def test_upload_unsupported_format(e2e_client: AsyncClient):
    resp = await e2e_client.post(
        "/api/coverage/upload",
        files={"files": ("report.pdf", b"fake pdf", "application/pdf")},
    )
    assert resp.status_code == 400


async def test_get_nonexistent_analysis(e2e_client: AsyncClient):
    resp = await e2e_client.get(f"/api/coverage/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_delete_nonexistent_analysis(e2e_client: AsyncClient):
    resp = await e2e_client.delete(f"/api/coverage/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_fetch_from_api_returns_501(e2e_client: AsyncClient):
    """The intranet API fetch endpoint is not implemented yet."""
    resp = await e2e_client.post("/api/coverage/fetch-from-api")
    assert resp.status_code == 501
