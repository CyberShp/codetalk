"""Current /api/tasks route contract smoke tests.

The old SQLAlchemy Repository task API was removed; detailed CRUD coverage now
lives in tests/test_tasks_api.py and tests/e2e/test_tasks.py.
"""

from app.api import tasks


def test_tasks_router_prefix_contract() -> None:
    assert tasks.router.prefix == "/api/tasks"


def test_task_response_schema_has_current_fields() -> None:
    fields = set(tasks.TaskResponse.model_fields)
    assert "repo_path" in fields
    assert "analysis_focus" in fields
    assert "material_ids" in fields
    assert "deepwiki_depth" not in fields
