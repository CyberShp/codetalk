"""Phase 7 confirmation contract for legacy workflow copies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


_FIXTURE_DIR = Path(__file__).with_name("fixtures") / "harness_workflow_refactor"


def _canvas_app() -> FastAPI:
    from app.api import agent_workbench, workbench_v2_workflows

    app = FastAPI()
    app.include_router(agent_workbench.router)
    app.include_router(workbench_v2_workflows.router)
    return app


@pytest.mark.asyncio
async def test_copy_to_v3_requires_confirmed_current_preview_token(tmp_path, monkeypatch) -> None:
    """A V3 copy must be tied to the reviewed, versioned preview response."""
    from app.config import settings

    fixture = json.loads(
        (_FIXTURE_DIR / "v2-draft-canvas-compatibility.json").read_text(encoding="utf-8")
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    workflow_id = fixture["workflow_header"]["workflow_id"]

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows",
            json={
                "id": workflow_id,
                "name": fixture["workflow_header"]["name"],
                "description": fixture["workflow_header"]["description"],
                "authoring_graph": fixture["workflow_version"]["authoring_graph"],
            },
        )
        assert created.status_code == 201
        version_id = created.json()["current_draft_version_id"]
        copy_url = f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/copy-to-v3"
        preview = await client.get(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/migration-preview"
        )
        assert preview.status_code == 200
        preview_payload = preview.json()
        assert preview_payload["requires_confirmation"] is True

        missing_confirmation = await client.post(copy_url, json={})
        assert missing_confirmation.status_code == 422
        assert missing_confirmation.json()["detail"]["code"] == "copy_to_v3_preview_confirmation_required"
        assert "confirmation_token" in preview_payload
        false_confirmation = await client.post(
            copy_url,
            json={
                "migration_contract_version": preview_payload["migration_contract_version"],
                "preview_confirmed": False,
                "confirmation_token": preview_payload["confirmation_token"],
            },
        )
        unknown_token = await client.post(
            copy_url,
            json={
                "migration_contract_version": preview_payload["migration_contract_version"],
                "preview_confirmed": True,
                "confirmation_token": "not-issued-by-the-preview",
            },
        )
        unknown_contract = await client.post(
            copy_url,
            json={
                "migration_contract_version": preview_payload["migration_contract_version"] + 1,
                "preview_confirmed": True,
                "confirmation_token": preview_payload["confirmation_token"],
            },
        )
        confirmed_copy = await client.post(
            copy_url,
            json={
                "migration_contract_version": preview_payload["migration_contract_version"],
                "preview_confirmed": True,
                "confirmation_token": preview_payload["confirmation_token"],
            },
        )

    assert false_confirmation.status_code == 422
    assert false_confirmation.json()["detail"]["code"] == "copy_to_v3_preview_confirmation_required"
    assert unknown_token.status_code == 422
    assert unknown_token.json()["detail"]["code"] == "copy_to_v3_preview_confirmation_invalid"
    assert unknown_contract.status_code == 422
    assert unknown_contract.json()["detail"]["code"] == "copy_to_v3_migration_contract_unknown"
    assert confirmed_copy.status_code == 201
    assert confirmed_copy.json()["migration_preview"]["confirmation_token"] == preview_payload["confirmation_token"]
