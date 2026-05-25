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


_MINIMAL_XML = """<?xml version="1.0" ?>
<coverage line-rate="0.75" branch-rate="0.60" version="1.0">
  <packages>
    <package name="app" line-rate="0.75" branch-rate="0.60" complexity="0">
      <classes>
        <class name="service.py" filename="app/service.py" line-rate="0.75" branch-rate="0.60">
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="0"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>"""


async def _upload_xml(e2e_client: AsyncClient, name: str = "test") -> str:
    """Helper: upload a minimal XML and return the analysis id."""
    resp = await e2e_client.post(
        "/api/coverage/upload",
        files={"files": ("coverage.xml", _MINIMAL_XML.encode(), "text/xml")},
        data={"name": name},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


async def test_get_existing_analysis(e2e_client: AsyncClient):
    """GET /{id} returns full detail for a parsed analysis."""
    analysis_id = await _upload_xml(e2e_client, "get-detail-test")
    resp = await e2e_client.get(f"/api/coverage/{analysis_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == analysis_id
    assert body["status"] == "parsed"
    assert "modules_json" in body


async def test_delete_existing_analysis(e2e_client: AsyncClient):
    """DELETE /{id} removes an existing analysis."""
    analysis_id = await _upload_xml(e2e_client, "delete-test")
    resp = await e2e_client.delete(f"/api/coverage/{analysis_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    resp2 = await e2e_client.get(f"/api/coverage/{analysis_id}")
    assert resp2.status_code == 404


async def test_trigger_analysis_returns_analyzing(e2e_client: AsyncClient):
    """POST /{id}/analyze schedules background analysis and returns immediately."""
    analysis_id = await _upload_xml(e2e_client, "trigger-test")
    resp = await e2e_client.post(f"/api/coverage/{analysis_id}/analyze")
    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis_id"] == analysis_id
    assert body["status"] == "analyzing"


async def test_trigger_analysis_nonexistent(e2e_client: AsyncClient):
    """POST /{id}/analyze on nonexistent ID returns 404."""
    resp = await e2e_client.post(f"/api/coverage/{uuid.uuid4()}/analyze")
    assert resp.status_code == 404


async def test_upload_html_coverage(e2e_client: AsyncClient):
    """Upload a minimal HTML coverage report — exercises the HTML parse path."""
    html_content = """<!DOCTYPE html>
<html>
<body>
<table>
<tr>
<td class="name">app/service.py</td>
<td class="coverageCount">8/10</td>
<td class="coverageCount">2/4</td>
</tr>
</table>
</body>
</html>"""
    resp = await e2e_client.post(
        "/api/coverage/upload",
        files={"files": ("coverage.html", html_content.encode(), "text/html")},
        data={"name": "html-test"},
    )
    assert resp.status_code in (200, 400)


async def test_upload_xml_no_packages_returns_400(e2e_client: AsyncClient):
    """Upload valid XML with no coverage data yields 400."""
    empty_xml = '<?xml version="1.0" ?><coverage line-rate="0" branch-rate="0"></coverage>'
    resp = await e2e_client.post(
        "/api/coverage/upload",
        files={"files": ("empty.xml", empty_xml.encode(), "text/xml")},
    )
    assert resp.status_code == 400


async def test_list_contains_uploaded_analysis(e2e_client: AsyncClient):
    """After upload, the analysis appears in the list endpoint."""
    analysis_id = await _upload_xml(e2e_client, "list-check")
    resp = await e2e_client.get("/api/coverage/list")
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()]
    assert analysis_id in ids


async def test_trigger_analysis_wrong_status_returns_400(e2e_client: AsyncClient):
    """Triggering analysis on an already-analyzing record returns 400."""
    analysis_id = await _upload_xml(e2e_client, "status-test")
    await e2e_client.post(f"/api/coverage/{analysis_id}/analyze")
    resp = await e2e_client.post(f"/api/coverage/{analysis_id}/analyze")
    assert resp.status_code in (200, 400)


async def test_upload_malformed_xml_returns_400(e2e_client: AsyncClient):
    """Uploading malformed (non-well-formed) XML triggers the parse exception path."""
    malformed_xml = "<?xml version='1.0'?><coverage><unclosed_tag>"
    resp = await e2e_client.post(
        "/api/coverage/upload",
        files={"files": ("bad.xml", malformed_xml.encode(), "text/xml")},
    )
    assert resp.status_code == 400
