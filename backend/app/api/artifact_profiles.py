"""Local artifact profile management for Workbench deliverables."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.artifact_profiles import (
    ArtifactProfileConflictError,
    ArtifactProfileNotFoundError,
    ArtifactProfileStore,
    ArtifactProfileValidationError,
)


router = APIRouter(
    prefix="/api/workbench/artifact-profiles",
    tags=["Workbench artifact profiles"],
)


class ArtifactProfilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str
    description: str = ""
    scope: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]]
    safety: dict[str, Any] | None = None


class ArtifactProfileUpdate(BaseModel):
    expected_version: int | None = None
    profile: ArtifactProfilePayload


class ProfileBinding(BaseModel):
    profile_id: str


class ProfileResolutionRequest(BaseModel):
    selected_profile_id: str = ""
    workspace_id: str = ""
    feature_tags: list[str] = Field(default_factory=list)
    builtin_profile: dict[str, Any] | None = None


def get_artifact_profile_store() -> ArtifactProfileStore:
    return ArtifactProfileStore(settings.data_path / "workbench" / "artifact_profiles.db")


@router.get("")
def list_artifact_profiles(
    store: ArtifactProfileStore = Depends(get_artifact_profile_store),
) -> list[dict[str, Any]]:
    return store.list_profiles()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_artifact_profile(
    payload: ArtifactProfilePayload,
    store: ArtifactProfileStore = Depends(get_artifact_profile_store),
) -> dict[str, Any]:
    try:
        return store.create_profile(payload.model_dump(exclude_none=True))
    except ArtifactProfileValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ArtifactProfileConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/resolve")
def resolve_artifact_profile(
    request: ProfileResolutionRequest,
    store: ArtifactProfileStore = Depends(get_artifact_profile_store),
) -> dict[str, Any]:
    try:
        return store.resolve_profile(**request.model_dump())
    except ArtifactProfileValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ArtifactProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_not_found_detail(exc)) from exc


@router.put("/bindings/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def bind_workspace_profile(
    workspace_id: str,
    binding: ProfileBinding,
    store: ArtifactProfileStore = Depends(get_artifact_profile_store),
) -> Response:
    try:
        store.bind_workspace(workspace_id, binding.profile_id)
    except ArtifactProfileValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ArtifactProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_not_found_detail(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/bindings/feature-tags/{feature_tag}", status_code=status.HTTP_204_NO_CONTENT)
def bind_feature_profile(
    feature_tag: str,
    binding: ProfileBinding,
    store: ArtifactProfileStore = Depends(get_artifact_profile_store),
) -> Response:
    try:
        store.bind_feature_tag(feature_tag, binding.profile_id)
    except ArtifactProfileValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ArtifactProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_not_found_detail(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/default", status_code=status.HTTP_204_NO_CONTENT)
def set_default_profile(
    binding: ProfileBinding,
    store: ArtifactProfileStore = Depends(get_artifact_profile_store),
) -> Response:
    try:
        store.set_user_default(binding.profile_id)
    except ArtifactProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_not_found_detail(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/default", status_code=status.HTTP_204_NO_CONTENT)
def clear_default_profile(
    store: ArtifactProfileStore = Depends(get_artifact_profile_store),
) -> Response:
    store.clear_user_default()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{profile_id}")
def get_artifact_profile(
    profile_id: str,
    version: int | None = None,
    store: ArtifactProfileStore = Depends(get_artifact_profile_store),
) -> dict[str, Any]:
    try:
        return store.get_profile(profile_id, version=version)
    except ArtifactProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_not_found_detail(exc)) from exc


@router.put("/{profile_id}")
def update_artifact_profile(
    profile_id: str,
    request: ArtifactProfileUpdate,
    store: ArtifactProfileStore = Depends(get_artifact_profile_store),
) -> dict[str, Any]:
    try:
        return store.update_profile(
            profile_id,
            request.profile.model_dump(exclude_none=True),
            expected_version=request.expected_version,
        )
    except ArtifactProfileValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ArtifactProfileConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ArtifactProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_not_found_detail(exc)) from exc


@router.get("/{profile_id}/versions")
def list_artifact_profile_versions(
    profile_id: str,
    store: ArtifactProfileStore = Depends(get_artifact_profile_store),
) -> list[dict[str, Any]]:
    try:
        return store.list_versions(profile_id)
    except ArtifactProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_not_found_detail(exc)) from exc


@router.post("/{profile_id}/restore/{version}")
def restore_artifact_profile_version(
    profile_id: str,
    version: int,
    store: ArtifactProfileStore = Depends(get_artifact_profile_store),
) -> dict[str, Any]:
    try:
        return store.restore_version(profile_id, version=version)
    except ArtifactProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_not_found_detail(exc)) from exc


def _not_found_detail(exc: ArtifactProfileNotFoundError) -> str:
    return f"artifact profile not found: {exc.args[0] if exc.args else ''}"
