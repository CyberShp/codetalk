from __future__ import annotations

import json
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from test_skill_build_pipeline import _file_digest, _full_review_evidence, _write_v24_source


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def skills_client(tmp_path, monkeypatch):
    from app.api import skills
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    app = FastAPI()
    app.include_router(skills.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_skill_api_build_review_publish_and_read_version(skills_client, tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_v24_source(source)

    created_project = await skills_client.post(
        "/api/skills/projects",
        json={"name": "CodeTalk Skills", "pack_id": "pack.codetalks"},
    )
    assert created_project.status_code == 201
    project_id = created_project.json()["project_id"]

    missing_project = await skills_client.get("/api/skills/projects/missing")
    assert missing_project.status_code == 404
    assert missing_project.json()["detail"]["code"] == "project_not_found"

    created_draft = await skills_client.post(
        f"/api/skills/projects/{project_id}/drafts/from-source",
        json={
            "source_root": str(source),
            "source_scenario_id": "module-analysis",
            "skill_id": "skill.codetalks-module-full-analysis",
        },
    )
    assert created_draft.status_code == 201
    draft = created_draft.json()
    assert draft["project_id"] == project_id
    assert Path(draft["filesystem_path"]).is_dir()

    built = await skills_client.post(f"/api/skills/drafts/{draft['draft_id']}/builds")
    assert built.status_code == 201
    build = built.json()
    assert build["status"] == "built"
    assert build["content_digest"].startswith("sha256:")

    blocked_publish = await skills_client.post(f"/api/skills/builds/{build['build_id']}/publish")
    assert blocked_publish.status_code == 409
    assert blocked_publish.json()["detail"]["code"] == "full_review_required"

    review = await skills_client.post(
        f"/api/skills/builds/{build['build_id']}/reviews/run",
        json={
            "scope": "full",
            "purpose": "api release review",
            "session_id": "api-review/f014-task7",
        },
    )
    assert review.status_code == 201
    review_body = review.json()
    assert review_body["decision"] == "approved"
    assert review_body["review_kind"] == "full"
    assert review_body["review_evidence"]["reviewed_file_digests"]
    assert review_body["review_evidence"]["provider"] == "deepseek"
    assert review_body["review_evidence"]["requested_model"] == "deepseek-v4-flash"
    assert review_body["review_evidence"]["declared_context_window_tokens"] == 200000
    assert review_body["review_evidence"]["requested_max_output_tokens"] == 4096

    published = await skills_client.post(f"/api/skills/builds/{build['build_id']}/publish")
    assert published.status_code == 201
    version = published.json()
    assert version["content_digest"] == build["content_digest"]
    assert version["review_evidence_digest"] == review_body["review_evidence_digest"]

    version_list = await skills_client.get("/api/skills/versions")
    assert version_list.status_code == 200
    assert [item["version_id"] for item in version_list.json()["items"]] == [version["version_id"]]

    filtered_version_list = await skills_client.get(
        "/api/skills/versions",
        params={"skill_id": "skill.codetalks-module-full-analysis"},
    )
    assert filtered_version_list.status_code == 200
    assert [item["version_id"] for item in filtered_version_list.json()["items"]] == [version["version_id"]]

    version_detail = await skills_client.get(f"/api/skills/versions/{version['version_id']}")
    manifest = await skills_client.get(f"/api/skills/versions/{version['version_id']}/manifest")
    ir = await skills_client.get(f"/api/skills/versions/{version['version_id']}/ir")
    assert version_detail.status_code == 200
    assert manifest.status_code == 200
    assert ir.status_code == 200
    assert ir.json()["skill_id"] == "skill.codetalks-module-full-analysis"
    assert manifest.json()["review_records"] == [review_body["review_id"]]

    missing_patch = await skills_client.post(
        f"/api/skills/reviews/{review_body['review_id']}/patches/missing/decision",
        json={"decision": "reject", "actor": "api-test"},
    )
    assert missing_patch.status_code == 404
    assert missing_patch.json()["detail"]["code"] == "patch_not_found"


async def test_skill_import_api_creates_drafts_from_uploaded_zip(skills_client, tmp_path: Path) -> None:
    archive = tmp_path / "pack.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as package:
        package.writestr("official-pack/SKILL.md", "# Skill\n")
        package.writestr("official-pack/workflow-manifest.json", "{}")
        package.writestr("official-pack/workflows/module-analysis.md", "# module\n")
        package.writestr("official-pack/workflows/root-cause.md", "# root cause\n")

    project = await skills_client.post("/api/skills/projects", json={"name": "Imported"})
    project_id = project.json()["project_id"]

    with archive.open("rb") as handle:
        imported = await skills_client.post(
            f"/api/skills/projects/{project_id}/imports",
            files={"file": ("pack.zip", handle, "application/zip")},
            data={"skill_id_prefix": "skill.codetalks"},
        )

    assert imported.status_code == 201
    body = imported.json()
    assert body["archive_digest"].startswith("sha256:")
    assert body["archive_root"] == "official-pack"
    assert [draft["source_scenario_id"] for draft in body["drafts"]] == [
        "module-analysis",
        "root-cause",
    ]
    assert all(draft["skill_id"].startswith("skill.codetalks.") for draft in body["drafts"])


async def test_skill_api_exposes_presets_and_writes_draft_file(skills_client) -> None:
    presets = await skills_client.get("/api/skills/presets")
    assert presets.status_code == 200
    preset_items = presets.json()["items"]
    assert {item["scenario_id"] for item in preset_items} == {
        "custom",
        "issue-regression",
        "module-analysis",
        "root-cause",
        "special-risk",
    }
    custom = next(item for item in preset_items if item["scenario_id"] == "custom")

    project = await skills_client.post("/api/skills/projects", json={"name": "API draft edits"})
    draft_response = await skills_client.post(
        f"/api/skills/projects/{project.json()['project_id']}/drafts/from-source",
        json={
            "source_root": custom["source_root"],
            "source_scenario_id": custom["scenario_id"],
            "skill_id": "skill.api-draft-edit",
        },
    )
    assert draft_response.status_code == 201
    draft = draft_response.json()
    first_build = await skills_client.post(f"/api/skills/drafts/{draft['draft_id']}/builds")
    assert first_build.status_code == 201

    written = await skills_client.post(
        f"/api/skills/drafts/{draft['draft_id']}/files",
        json={"relative_path": "references/tool-routing.md", "content": "# changed\n"},
    )
    assert written.status_code == 201
    assert written.json()["relative_path"] == "references/tool-routing.md"
    assert written.json()["digest"].startswith("sha256:")
    rejected = await skills_client.post(
        f"/api/skills/drafts/{draft['draft_id']}/files",
        json={"relative_path": "../escape.md", "content": "no"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "invalid_draft_file"

    second_build = await skills_client.post(f"/api/skills/drafts/{draft['draft_id']}/builds")
    assert second_build.status_code == 201
    assert second_build.json()["content_digest"] != first_build.json()["content_digest"]


async def test_skill_api_rejects_draft_file_write_through_symlink_ancestor_without_side_effect(
    skills_client,
    tmp_path: Path,
) -> None:
    presets = await skills_client.get("/api/skills/presets")
    custom = next(item for item in presets.json()["items"] if item["scenario_id"] == "custom")
    project = await skills_client.post("/api/skills/projects", json={"name": "Symlink draft edit"})
    draft_response = await skills_client.post(
        f"/api/skills/projects/{project.json()['project_id']}/drafts/from-source",
        json={
            "source_root": custom["source_root"],
            "source_scenario_id": custom["scenario_id"],
            "skill_id": "skill.api-draft-symlink",
        },
    )
    draft_root = Path(draft_response.json()["filesystem_path"])
    outside = tmp_path / "outside"
    outside.mkdir()
    (draft_root / "leaky").symlink_to(outside, target_is_directory=True)

    rejected = await skills_client.post(
        f"/api/skills/drafts/{draft_response.json()['draft_id']}/files",
        json={"relative_path": "leaky/subdir/escape.md", "content": "no"},
    )

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "invalid_draft_file"
    assert not (outside / "subdir").exists()


async def test_skill_api_returns_exact_4xx_for_invalid_resources_and_inputs(skills_client, tmp_path: Path) -> None:
    missing_draft_build = await skills_client.post("/api/skills/drafts/missing/builds")
    assert missing_draft_build.status_code == 404
    assert missing_draft_build.json()["detail"]["code"] == "draft_not_found"

    invalid_project = await skills_client.post("/api/skills/projects", json={"name": ""})
    assert invalid_project.status_code == 422
    assert invalid_project.json()["detail"]["code"] == "invalid_project"

    unsafe_source = await skills_client.post(
        "/api/skills/projects/missing/drafts/from-source",
        json={
            "source_root": str(tmp_path / "missing"),
            "source_scenario_id": "module-analysis",
            "skill_id": "skill.missing",
        },
    )
    assert unsafe_source.status_code == 404
    assert unsafe_source.json()["detail"]["code"] == "project_not_found"

    project = await skills_client.post("/api/skills/projects", json={"name": "Import errors"})
    bad_archive = tmp_path / "bad.zip"
    bad_archive.write_text("not a zip", encoding="utf-8")
    with bad_archive.open("rb") as handle:
        failed_import = await skills_client.post(
            f"/api/skills/projects/{project.json()['project_id']}/imports",
            files={"file": ("bad.zip", handle, "application/zip")},
        )
    assert failed_import.status_code == 422
    assert failed_import.json()["detail"]["code"] == "invalid_archive"


async def test_skill_api_maps_missing_release_records_to_structured_conflict(
    skills_client,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_v24_source(source)

    project = await skills_client.post("/api/skills/projects", json={"name": "Corrupt records"})
    draft = await skills_client.post(
        f"/api/skills/projects/{project.json()['project_id']}/drafts/from-source",
        json={
            "source_root": str(source),
            "source_scenario_id": "module-analysis",
            "skill_id": "skill.codetalks-module-full-analysis",
        },
    )
    build = await skills_client.post(f"/api/skills/drafts/{draft.json()['draft_id']}/builds")
    review = await skills_client.post(
        f"/api/skills/builds/{build.json()['build_id']}/reviews/run",
        json={"scope": "full"},
    )
    version = await skills_client.post(f"/api/skills/builds/{build.json()['build_id']}/publish")

    version_root = Path(version.json()["version_root"])
    manifest_path = Path(version.json()["manifest_path"])
    version_root.chmod(0o700)
    manifest_path.chmod(0o600)
    manifest_path.write_text("{", encoding="utf-8")
    broken_manifest = await skills_client.get(
        f"/api/skills/versions/{version.json()['version_id']}/manifest"
    )
    assert broken_manifest.status_code == 409
    assert broken_manifest.json()["detail"]["code"] == "version_manifest_unavailable"

    review_record_path = Path(review.json()["record_path"])
    review_record_path.parent.chmod(0o700)
    review_record_path.chmod(0o600)
    review_record_path.unlink()
    broken_review = await skills_client.get(f"/api/skills/reviews/{review.json()['review_id']}")
    assert broken_review.status_code == 409
    assert broken_review.json()["detail"]["code"] == "review_record_unavailable"

    broken_decision = await skills_client.post(
        f"/api/skills/reviews/{review.json()['review_id']}/patches/patch.missing/decision",
        json={"decision": "reject", "actor": "api-test"},
    )
    assert broken_decision.status_code == 409
    assert broken_decision.json()["detail"]["code"] == "review_record_unavailable"


async def test_skill_api_maps_unresolved_review_findings_to_publish_conflict(
    skills_client,
    tmp_path: Path,
) -> None:
    from app.api import skills

    source = tmp_path / "source"
    _write_v24_source(source)

    project = await skills_client.post("/api/skills/projects", json={"name": "Bypassed review"})
    draft = await skills_client.post(
        f"/api/skills/projects/{project.json()['project_id']}/drafts/from-source",
        json={
            "source_root": str(source),
            "source_scenario_id": "module-analysis",
            "skill_id": "skill.codetalks-module-full-analysis",
        },
    )
    build_response = await skills_client.post(f"/api/skills/drafts/{draft.json()['draft_id']}/builds")
    build_id = build_response.json()["build_id"]

    store = skills.skill_store()
    build = store.get_build(build_id)
    reviewed_paths = sorted(path.relative_to(build.unpacked_root).as_posix() for path in build.unpacked_root.rglob("*") if path.is_file())
    finding = {
        "finding_id": "finding.open-publish-api",
        "severity": "p1",
        "summary": "Open release blocker",
        "locations": [{"path": "references/tool-routing.md", "field": "content"}],
        "reason": "The candidate still has an unresolved blocker.",
        "impact": "Publication would hide unresolved review risk.",
        "recommendation": "Resolve or explicitly acknowledge the finding.",
        "disposition": "open",
    }
    evidence = {
        **_full_review_evidence(decision="approved", findings=[finding]),
        "reviewed_paths": reviewed_paths,
        "reviewed_file_digests": [
            {"path": path, "digest": _file_digest(build.unpacked_root / path)}
            for path in reviewed_paths
        ],
    }
    evidence_digest = "sha256:" + hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    review_record = {
        "schema_version": "skill-review-v1",
        "review_id": "review_bypassed_open_publish_api",
        "skill_id": "skill.codetalks-module-full-analysis",
        "content_digest": build.content_digest,
        "review_evidence_digest": evidence_digest,
        "review_evidence": evidence,
    }
    store.record_review(
        build_id=build_id,
        review_record=review_record,
        record_path=Path(build.build_root) / "reviews" / "review_bypassed_open_publish_api" / "skill-review-v1.json",
    )

    published = await skills_client.post(f"/api/skills/builds/{build_id}/publish")

    assert published.status_code == 409
    assert published.json()["detail"]["code"] == "review_findings_unresolved"
