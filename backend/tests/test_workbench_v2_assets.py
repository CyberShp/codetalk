from dataclasses import asdict

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def assets_client(tmp_path, monkeypatch):
    from app.api import workbench_v2_assets
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)
    app = FastAPI()
    app.include_router(workbench_v2_assets.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, workbench_v2_assets


async def test_semantic_asset_api_manages_cases_and_commits_preview(assets_client):
    client, module = assets_client
    store = module.semantic_store()
    semantic_id = store.upsert_case({
        "case_id": "TC_NVME_TLS_001",
        "feature": "NVMe TCP TLS",
        "module": "nvmf/tcp/tls",
        "scenario": "invalid certificate rejects connection",
        "expected": ["connection is rejected"],
        "test_level": "black_box",
        "interface": "NVMe/TCP",
        "tags": ["security"],
        "source_ref": "task_run:run_semantic_1:black_box_cases",
    })

    response = await client.get("/api/workbench/semantic-cases", params={"q": "certificate"})
    assert response.status_code == 200
    assert response.json()["items"][0]["semantic_id"] == semantic_id

    detail = await client.get(f"/api/workbench/semantic-cases/{semantic_id}")
    assert detail.status_code == 200
    assert detail.json()["counts"] == {"preconditions": 0, "actions": 0, "expected": 1}
    assert detail.json()["references"] == [{
        "type": "task_run",
        "task_run_id": "run_semantic_1",
        "output_id": "black_box_cases",
    }]

    updated = await client.patch(
        f"/api/workbench/semantic-cases/{semantic_id}",
        json={"scenario": "expired certificate rejects connection"},
    )
    assert updated.status_code == 200
    assert updated.json()["scenario"].startswith("expired")
    assert (await client.post(f"/api/workbench/semantic-cases/{semantic_id}/deprecate")).json()["status"] == "deprecated"
    assert (await client.post(f"/api/workbench/semantic-cases/{semantic_id}/restore")).json()["status"] == "active"

    preview = await client.post(
        "/api/workbench/semantic-cases/import/preview",
        files={"file": ("cases.csv", b"case_id,scenario,expected\nTC_NEW,new case,visible result\n", "text/csv")},
        data={"options_json": '{"mapping":{}}'},
    )
    assert preview.status_code == 200
    preview_body = preview.json()
    assert preview_body["valid_count"] == 1
    assert store.list_cases(page=1, page_size=20)["total"] == 1

    committed = await client.post(
        "/api/workbench/semantic-cases/import/commit",
        json={"preview_id": preview_body["preview_id"], "conflict_strategy": "skip"},
    )
    assert committed.status_code == 201
    assert committed.json()["imported_count"] == 1
    assert committed.json()["failure_download_url"].endswith("/failures")
    failures = await client.get(committed.json()["failure_download_url"])
    assert failures.status_code == 200
    assert failures.headers["content-type"].startswith("application/x-ndjson")


async def test_evidence_asset_api_uses_existing_memory_and_source_slices(assets_client):
    client, module = assets_client
    store = module.evidence_store()
    evidence_id = store.upsert_evidence_item(
        run_id="run_1",
        workspace_id="ws_spdk",
        kind="source_file",
        subject_key="lib/nvmf/tcp.c",
        status="validated",
        source="gitnexus",
        path="lib/nvmf/tcp.c",
        symbol="nvmf_tcp_create",
        reason="TLS transport creation",
        confidence=0.95,
        text="certificate and TLS transport evidence",
        provenance={"task_id": "task_1"},
    )
    store.add_source_slice(
        evidence_id=evidence_id,
        file_path="lib/nvmf/tcp.c",
        start_line=10,
        end_line=14,
        excerpt="static int nvmf_tcp_create(void)",
        sha256="abc",
    )

    listed = await client.get(
        "/api/workbench/evidence",
        params={"q": "certificate", "workspace_id": "ws_spdk", "kind": "source_file"},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["evidence_id"] == evidence_id

    detail = await client.get(f"/api/workbench/evidence/{evidence_id}")
    assert detail.status_code == 200
    assert detail.json()["source_slices"][0]["file_path"] == "lib/nvmf/tcp.c"
    assert detail.json()["provenance"]["task_id"] == "task_1"

    facets = await client.get("/api/workbench/evidence/facets")
    assert facets.status_code == 200
    assert facets.json()["kinds"] == [{"value": "source_file", "count": 1}]


async def test_asset_lists_default_to_25_and_reject_page_sizes_over_100(assets_client):
    client, module = assets_client
    store = module.semantic_store()
    for index in range(30):
        store.upsert_case({
            "case_id": f"TC_PAGE_{index:03d}",
            "scenario": f"pagination scenario {index}",
            "expected": ["visible result"],
        })

    semantic = await client.get("/api/workbench/semantic-cases")
    evidence = await client.get("/api/workbench/evidence")

    assert semantic.status_code == 200
    assert semantic.json()["page_size"] == 25
    assert len(semantic.json()["items"]) == 25
    assert evidence.status_code == 200
    assert evidence.json()["page_size"] == 25
    assert (await client.get(
        "/api/workbench/semantic-cases", params={"page_size": 101}
    )).status_code == 422
    assert (await client.get(
        "/api/workbench/evidence", params={"page_size": 101}
    )).status_code == 422
