from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _payload(name: str) -> dict:
    return {
        "name": name,
        "artifacts": [
            {
                "id": "test_design",
                "filename": "test_design.md",
                "format": "markdown",
                "required": True,
            }
        ],
    }


@pytest.fixture
async def profile_client(tmp_path):
    from app.api import artifact_profiles as profile_api
    from app.services.artifact_profiles import ArtifactProfileStore

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    store = ArtifactProfileStore(tmp_path / "profiles.db")
    app = FastAPI(lifespan=lifespan)
    app.include_router(profile_api.router)
    app.dependency_overrides[profile_api.get_artifact_profile_store] = lambda: store
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_artifact_profile_api_crud_versions_and_restore(profile_client):
    created_response = await profile_client.post(
        "/api/workbench/artifact-profiles", json=_payload("Local default")
    )
    assert created_response.status_code == 201
    created = created_response.json()

    updated_response = await profile_client.put(
        f"/api/workbench/artifact-profiles/{created['id']}",
        json={"expected_version": 1, "profile": _payload("Local concise")},
    )
    assert updated_response.status_code == 200
    assert updated_response.json()["version"] == 2

    versions = (
        await profile_client.get(
            f"/api/workbench/artifact-profiles/{created['id']}/versions"
        )
    ).json()
    assert [item["version"] for item in versions] == [2, 1]

    restored = await profile_client.post(
        f"/api/workbench/artifact-profiles/{created['id']}/restore/1"
    )
    assert restored.status_code == 200
    assert restored.json()["name"] == "Local default"
    assert restored.json()["version"] == 3


async def test_artifact_profile_api_resolves_workspace_and_run_selection(profile_client):
    selected = (
        await profile_client.post(
            "/api/workbench/artifact-profiles", json=_payload("Selected")
        )
    ).json()
    workspace = (
        await profile_client.post(
            "/api/workbench/artifact-profiles", json=_payload("Workspace")
        )
    ).json()
    response = await profile_client.put(
        "/api/workbench/artifact-profiles/bindings/workspaces/ws-1",
        json={"profile_id": workspace["id"]},
    )
    assert response.status_code == 204

    resolved = await profile_client.post(
        "/api/workbench/artifact-profiles/resolve",
        json={"workspace_id": "ws-1", "feature_tags": ["iscsi"]},
    )
    assert resolved.json()["profile"]["name"] == "Workspace"

    override = await profile_client.post(
        "/api/workbench/artifact-profiles/resolve",
        json={"selected_profile_id": selected["id"], "workspace_id": "ws-1"},
    )
    assert override.json()["source"] == "run_selection"


async def test_artifact_profile_api_returns_actionable_validation_and_conflict_errors(
    profile_client,
):
    invalid = await profile_client.post(
        "/api/workbench/artifact-profiles",
        json={
            **_payload("Unsafe"),
            "safety": {"allow_unverified_evidence": True},
        },
    )
    assert invalid.status_code == 422
    assert "global safety" in invalid.json()["detail"]

    created = (
        await profile_client.post(
            "/api/workbench/artifact-profiles", json=_payload("Version one")
        )
    ).json()
    await profile_client.put(
        f"/api/workbench/artifact-profiles/{created['id']}",
        json={"expected_version": 1, "profile": _payload("Version two")},
    )
    stale = await profile_client.put(
        f"/api/workbench/artifact-profiles/{created['id']}",
        json={"expected_version": 1, "profile": _payload("Stale")},
    )
    assert stale.status_code == 409
    assert "current version is 2" in stale.json()["detail"]
