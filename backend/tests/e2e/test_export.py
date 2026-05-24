"""E2E tests for /api/tasks/{task_id}/export endpoint."""

import uuid

from httpx import AsyncClient


async def test_export_nonexistent_task(e2e_client: AsyncClient):
    resp = await e2e_client.get(f"/api/tasks/{uuid.uuid4()}/export")
    assert resp.status_code == 404


async def test_export_task_no_outputs(e2e_client: AsyncClient, repo_path: str):
    """Export for a task with no output files should return 404."""
    create_resp = await e2e_client.post(
        "/api/tasks",
        json={
            "name": "Export Test",
            "repo_path": repo_path,
            "tools": ["gitnexus"],
            "analysis_focus": "Test export",
            "prompt_content": "Test prompt.",
            "deepwiki_depth": "balanced",
        },
    )
    task_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/tasks/{task_id}/export")
    assert resp.status_code == 404


async def test_export_invalid_format(e2e_client: AsyncClient, repo_path: str):
    """Export with invalid format should return 422."""
    create_resp = await e2e_client.post(
        "/api/tasks",
        json={
            "name": "Export Fmt Test",
            "repo_path": repo_path,
            "tools": ["gitnexus"],
            "analysis_focus": "Test export format",
            "prompt_content": "Test prompt.",
            "deepwiki_depth": "balanced",
        },
    )
    task_id = create_resp.json()["id"]

    resp = await e2e_client.get(
        f"/api/tasks/{task_id}/export",
        params={"format": "invalid_format"},
    )
    # Expect 422 for bad format or 404 if no outputs exist
    assert resp.status_code in (404, 422)


async def test_export_with_output_files(e2e_client: AsyncClient, repo_path: str, tmp_path, monkeypatch):
    """Export should work when output files exist."""
    from app.config import settings

    # Create task
    create_resp = await e2e_client.post(
        "/api/tasks",
        json={
            "name": "Export With Files",
            "repo_path": repo_path,
            "tools": ["gitnexus"],
            "analysis_focus": "Test export with files",
            "prompt_content": "Test prompt.",
            "deepwiki_depth": "balanced",
        },
    )
    task_id = create_resp.json()["id"]

    # Create output files in the outputs directory
    output_dir = settings.outputs_path / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "analysis_report.md").write_text(
        "# Analysis Report\n\nTest content.", encoding="utf-8"
    )

    resp = await e2e_client.get(f"/api/tasks/{task_id}/export")
    assert resp.status_code == 200
    # Should return file content
    assert len(resp.content) > 0


async def test_export_md_format(e2e_client: AsyncClient, repo_path: str, tmp_path, monkeypatch):
    """Export in markdown format."""
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/tasks",
        json={
            "name": "MD Export",
            "repo_path": repo_path,
            "tools": ["gitnexus"],
            "analysis_focus": "Markdown export test",
            "prompt_content": "Test prompt.",
            "deepwiki_depth": "balanced",
        },
    )
    task_id = create_resp.json()["id"]

    output_dir = settings.outputs_path / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.md").write_text("# Report\nContent here.", encoding="utf-8")

    resp = await e2e_client.get(
        f"/api/tasks/{task_id}/export",
        params={"format": "md"},
    )
    assert resp.status_code == 200
