import sqlite3

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient


pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_ai_run_snapshot_is_immutable_when_conversation_runtime_changes(sqlite_db):
    from app.services.ai_conversations import AIConversationStore

    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-snapshot",
        workspace_id="ws-snapshot",
        title="Snapshot thread",
        runtime_type="agent_runtime",
        agent_runtime_id="runtime-a",
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="Use the fixed workflow constraints",
        references=[],
        run_snapshot={
            "execution_mode": "workflow_constraint",
            "runtime_type": "agent_runtime",
            "agent_runtime_id": "runtime-a",
            "runtime_snapshot": {
                "recorded": True,
                "id": "runtime-a",
                "name": "Codex A",
                "provider": "codex",
                "prompt_transport": "codex_exec_json",
                "session_mode": "fresh",
            },
            "workflow_binding_snapshot": {
                "recorded": True,
                "workflow_id": "source_flow_sfmea_blackbox",
                "workflow_version_id": "wfv-fixed",
                "workflow_name": "Source Flow SFMEA",
                "mode": "constraint_answer",
            },
            "skills_snapshot": ["source-evidence-first", "sfmea"],
            "mcp_snapshot": ["gitnexus", "cgc"],
            "context_summary": {"workspace_id": "ws-snapshot", "reference_count": 0},
            "artifact_contract": {"required_outputs": ["sfmea.json"]},
        },
    )

    await store.update_conversation_runtime(
        conversation["id"],
        runtime_type="agent_runtime",
        agent_runtime_id="runtime-b",
    )
    historical = await store.get_run(created["run"]["id"])

    assert historical["execution_mode"] == "workflow_constraint"
    assert historical["runtime_type"] == "agent_runtime"
    assert historical["agent_runtime_id"] == "runtime-a"
    assert historical["runtime_snapshot"]["name"] == "Codex A"
    assert historical["workflow_binding_snapshot"]["workflow_version_id"] == "wfv-fixed"
    assert historical["skills_snapshot"] == ["source-evidence-first", "sfmea"]
    assert historical["mcp_snapshot"] == ["gitnexus", "cgc"]
    assert historical["artifact_contract"]["required_outputs"] == ["sfmea.json"]


async def test_legacy_ai_run_reports_unrecorded_snapshot_instead_of_current_runtime(sqlite_db):
    from app.services.ai_conversations import AIConversationStore

    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-legacy-snapshot",
        workspace_id="ws-legacy-snapshot",
        title="Legacy thread",
        runtime_type="agent_runtime",
        agent_runtime_id="runtime-current",
    )
    async with aiosqlite.connect(sqlite_db) as db:
        await db.execute(
            """
            INSERT INTO ai_conversation_runs
                (id, conversation_id, status, sequence, cursor, created_at)
            VALUES ('run-legacy', ?, 'completed', 1, 0, '2026-01-01T00:00:00Z')
            """,
            (conversation["id"],),
        )
        await db.commit()

    legacy = await store.get_run("run-legacy")

    assert legacy["execution_mode"] == "legacy"
    assert legacy["runtime_type"] == "unknown"
    assert legacy["agent_runtime_id"] is None
    assert legacy["runtime_snapshot"] == {
        "recorded": False,
        "status": "legacy",
        "label": "未记录",
    }
    assert legacy["workflow_binding_snapshot"]["recorded"] is False


async def test_ai_workbench_links_are_idempotent_and_queryable(sqlite_db):
    from app.services.ai_conversations import AIConversationStore
    from app.services.ai_workbench_links import AIWorkbenchLinkStore

    conversation = await AIConversationStore(sqlite_db).create_conversation(
        scope_type="workspace",
        scope_id="ws-links",
        workspace_id="ws-links",
        title="Linked thread",
    )
    store = AIWorkbenchLinkStore(sqlite_db)
    first = await store.create_link(
        conversation_id=conversation["id"],
        message_id="msg-source",
        ai_run_id="run-source",
        task_id="task-1",
        task_run_id="",
        relation_type="task_created_from_ai",
        metadata={"workflow_version_id": "wfv-1"},
    )
    duplicate = await store.create_link(
        conversation_id=conversation["id"],
        message_id="msg-source",
        ai_run_id="run-source",
        task_id="task-1",
        task_run_id="",
        relation_type="task_created_from_ai",
        metadata={"workflow_version_id": "wfv-1"},
    )

    assert duplicate["id"] == first["id"]
    by_conversation = await store.list_links(conversation_id=conversation["id"])
    by_task = await store.list_links(task_id="task-1")
    assert by_conversation == by_task == [first]
    assert first["metadata"] == {"workflow_version_id": "wfv-1"}


async def test_ai_thread_v2_schema_migration_is_idempotent(sqlite_db):
    from app.database import _MIGRATIONS

    expected_columns = {
        "execution_mode",
        "runtime_type",
        "agent_runtime_id",
        "runtime_snapshot_json",
        "workflow_binding_snapshot_json",
        "skills_snapshot_json",
        "mcp_snapshot_json",
        "context_summary_json",
        "artifact_contract_json",
        "metrics_json",
        "claimed_at",
    }
    async with aiosqlite.connect(sqlite_db) as db:
        async with db.execute("PRAGMA table_info(ai_conversation_runs)") as cur:
            columns = {str(row[1]) for row in await cur.fetchall()}
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ai_workbench_links'"
        ) as cur:
            links_table = await cur.fetchone()
        assert expected_columns.issubset(columns)
        assert links_table is not None

        for statement in _MIGRATIONS:
            try:
                await db.execute(statement)
            except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
                assert "duplicate column" in str(exc).lower()
        await db.commit()

        async with db.execute("PRAGMA table_info(ai_conversation_runs)") as cur:
            after = {str(row[1]) for row in await cur.fetchall()}
        assert expected_columns.issubset(after)


async def test_message_api_freezes_selected_agent_runtime_before_future_switch(
    sqlite_db,
    monkeypatch,
):
    from app.api import ai_conversations as ai_api
    from app.services.agent_runtimes import AgentRuntimeStore
    from app.services.ai_conversations import AIConversationStore
    from tests.test_ai_conversations import _test_app

    runtime_store = AgentRuntimeStore(sqlite_db)
    runtime_a = await runtime_store.create_runtime(
        {
            "name": "Codex A",
            "command": "codex-a",
            "prompt_transport": "codex_exec_json",
            "output_mode": "stream_json",
            "working_dir_mode": "project",
            "session_persistence": "resume_args",
            "mcp_profile": "gitnexus+cgc",
            "enabled": True,
        }
    )
    runtime_b = await runtime_store.create_runtime(
        {
            "name": "Codex B",
            "command": "codex-b",
            "prompt_transport": "codex_exec_json",
            "output_mode": "stream_json",
            "working_dir_mode": "project",
            "enabled": True,
        }
    )
    kicked: list[str] = []
    monkeypatch.setattr(ai_api, "kick_conversation_queue", kicked.append)

    app = _test_app(sqlite_db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/ai/conversations",
            json={
                "scope_type": "workspace",
                "scope_id": "ws-api-snapshot",
                "workspace_id": "ws-api-snapshot",
                "title": "API snapshot",
                "runtime_type": "agent_runtime",
                "agent_runtime_id": runtime_a["id"],
            },
        )
        assert created.status_code == 201
        conversation_id = created.json()["id"]
        sent = await client.post(
            f"/api/ai/conversations/{conversation_id}/messages",
            json={"content": "Read the workspace source"},
        )
        assert sent.status_code == 202
        switched = await client.patch(
            f"/api/ai/conversations/{conversation_id}",
            json={"runtime_type": "agent_runtime", "agent_runtime_id": runtime_b["id"]},
        )
        assert switched.status_code == 200

    run = await AIConversationStore(sqlite_db).get_run(sent.json()["run"]["id"])
    assert kicked == [conversation_id]
    assert run["agent_runtime_id"] == runtime_a["id"]
    assert run["runtime_snapshot"]["name"] == "Codex A"
    assert run["runtime_snapshot"]["prompt_transport"] == "codex_exec_json"
    assert run["runtime_snapshot"]["session_mode"] == "fresh"
    assert run["mcp_snapshot"] == ["gitnexus", "cgc"]


async def test_scheduler_uses_run_runtime_snapshot_not_current_conversation_runtime(
    sqlite_db,
    monkeypatch,
):
    from app.api import ai_conversations as ai_api
    from app.services.ai_run_snapshots import build_ai_run_snapshot
    from app.services.agent_runtimes import AgentRuntimeStore
    from app.services.ai_conversations import AIConversationStore

    runtime_store = AgentRuntimeStore(sqlite_db)
    runtime_a = await runtime_store.create_runtime(
        {
            "name": "Runtime A",
            "command": "runtime-a",
            "prompt_transport": "stdin",
            "output_mode": "plain",
            "working_dir_mode": "project",
            "enabled": True,
        }
    )
    runtime_b = await runtime_store.create_runtime(
        {
            "name": "Runtime B",
            "command": "runtime-b",
            "prompt_transport": "stdin",
            "output_mode": "plain",
            "working_dir_mode": "project",
            "enabled": True,
        }
    )
    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-frozen-scheduler",
        workspace_id="ws-frozen-scheduler",
        title="Frozen scheduler",
        runtime_type="agent_runtime",
        agent_runtime_id=runtime_a["id"],
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="Run with A",
        references=[],
        run_snapshot=build_ai_run_snapshot(
            conversation=conversation,
            runtime=runtime_a,
            references=[],
        ),
    )
    assert "runtime_execution_snapshot" not in created["run"]
    assert "runtime_execution_snapshot_json" not in created["run"]
    await runtime_store.update_runtime(
        runtime_a["id"],
        {"command": "runtime-a-mutated", "args": ["--mutated"]},
    )
    await store.update_conversation_runtime(
        conversation["id"],
        runtime_type="agent_runtime",
        agent_runtime_id=runtime_b["id"],
    )

    captured: list[tuple[str, str, list[str]]] = []
    finished = __import__("asyncio").Event()

    async def fake_run_agent_generation(*, store, run_id, runtime):
        captured.append((runtime["id"], runtime["command"], runtime["args"]))
        await store.fail_run(run_id, "test completed")
        finished.set()

    monkeypatch.setattr(ai_api, "run_agent_generation", fake_run_agent_generation)
    ai_api.kick_conversation_queue(conversation["id"])
    await __import__("asyncio").wait_for(finished.wait(), timeout=1)

    assert captured == [(runtime_a["id"], "runtime-a", [])]


def _published_workflow(data_dir, *, workflow_id="ai-task-flow"):
    from app.services.workflow_version_store import WorkflowVersionStore

    definition = {
        "id": workflow_id,
        "name": "AI Task Flow",
        "version": 1,
        "inputs": [
            {"id": "repo_path", "type": "directory", "required": True, "resolver": "workspace"},
            {"id": "analysis_target", "label": "分析目标", "type": "free_text", "required": True},
        ],
        "steps": [{"id": "analyze", "type": "agent_task", "provider": "builtin-llm"}],
        "outputs": [{"id": "report", "type": "markdown", "from": "analyze", "artifact": "report.md"}],
    }
    store = WorkflowVersionStore(data_dir / "workbench" / "workflows.db")
    header, draft = store.create_workflow(
        workflow_id=workflow_id,
        name=definition["name"],
        description="AI task bridge test",
        authoring_graph={"schema_version": 2, "workflow_id": workflow_id},
    )
    published = store.publish_version(
        draft.version_id,
        authoring_graph=draft.authoring_graph,
        compiled_definition=definition,
        compiled_plan={
            "plan_version": 1,
            "workflow_version_id": draft.version_id,
            "topological_order": ["analyze"],
            "nodes": [{"node_id": "analyze", "type": "agent_task", "depends_on": []}],
            "max_parallelism": 1,
        },
        validation={"valid": True, "errors": [], "warnings": []},
    )
    return header, published


async def test_new_ai_workflow_binding_freezes_current_published_version(sqlite_db):
    from app.config import settings
    from tests.test_ai_conversations import _test_app

    header, published = _published_workflow(
        settings.data_path,
        workflow_id="ai-binding-pin",
    )
    app = _test_app(sqlite_db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/conversations",
            json={
                "scope_type": "workspace",
                "scope_id": "ws-binding-pin",
                "workspace_id": "ws-binding-pin",
                "title": "Pinned binding",
                "initial_context": {"selected_workflow_id": header.workflow_id},
            },
        )

    assert response.status_code == 201, response.text
    context = response.json()["initial_context"]
    assert context["selected_workflow_version_id"] == published.version_id
    assert context["workflow_binding_snapshot"]["workflow_version_id"] == published.version_id
    assert context["workflow_binding_snapshot"]["mode"] == "constraint_answer"


async def test_ai_thread_creates_fixed_published_task_draft_and_origin_link(sqlite_db):
    from app.config import settings
    from app.services.ai_conversations import AIConversationStore
    from app.services.ai_workbench_links import AIWorkbenchLinkStore
    from tests.test_ai_conversations import _test_app

    workspace_id = "ws-ai-task-bridge"
    async with aiosqlite.connect(sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
            "VALUES (?, 'SPDK', '/repo/spdk', 1, '2026-01-01', '2026-01-01')",
            (workspace_id,),
        )
        await db.commit()
    header, published = _published_workflow(settings.data_path)
    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id=workspace_id,
        workspace_id=workspace_id,
        title="SPDK iSCSI login investigation",
        initial_context={
            "selected_workflow_id": header.workflow_id,
            "selected_workflow_version_id": published.version_id,
        },
    )
    source = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="Design iSCSI login black-box tests",
        references=[],
    )

    app = _test_app(sqlite_db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/ai/conversations/{conversation['id']}/task-drafts",
            json={
                "source_message_id": source["message"]["id"],
                "source_ai_run_id": source["run"]["id"],
                "workflow_id": header.workflow_id,
                "workflow_version_id": published.version_id,
                "mode": "draft",
                "compiled_plan": {"forged": True},
            },
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["task"]["workspace_id"] == workspace_id
    assert body["task"]["workflow_id"] == header.workflow_id
    assert body["task"]["workflow_version_id"] == published.version_id
    assert body["task"]["lifecycle_status"] == "draft"
    assert body["next_required_step"] == 3
    assert body["missing_inputs"] == [
        {"id": "analysis_target", "label": "分析目标", "type": "free_text"}
    ]
    assert "iSCSI login" in body["task"]["name"]
    assert "Design iSCSI login" in body["task"]["description"]

    links = await AIWorkbenchLinkStore(sqlite_db).list_links(task_id=body["task"]["task_id"])
    assert len(links) == 1
    assert links[0]["conversation_id"] == conversation["id"]
    assert links[0]["message_id"] == source["message"]["id"]
    assert links[0]["ai_run_id"] == source["run"]["id"]
    assert links[0]["relation_type"] == "task_created_from_ai"
    assert links[0]["metadata"]["workflow_version_id"] == published.version_id


async def test_ai_task_draft_rejects_unpublished_version_and_foreign_message(sqlite_db):
    from app.config import settings
    from app.services.ai_conversations import AIConversationStore
    from app.services.workflow_version_store import WorkflowVersionStore
    from tests.test_ai_conversations import _test_app

    workspace_id = "ws-ai-task-reject"
    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id=workspace_id,
        workspace_id=workspace_id,
        title="Owner thread",
    )
    other = await store.create_conversation(
        scope_type="workspace",
        scope_id=workspace_id,
        workspace_id=workspace_id,
        title="Other thread",
    )
    foreign = await store.create_user_message_and_run(
        conversation_id=other["id"],
        content="Foreign message",
        references=[],
    )
    version_store = WorkflowVersionStore(settings.data_path / "workbench" / "workflows.db")
    _, draft = version_store.create_workflow(
        workflow_id="draft-only-flow",
        name="Draft only",
        description="",
        authoring_graph={"schema_version": 2, "workflow_id": "draft-only-flow"},
    )

    app = _test_app(sqlite_db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unpublished = await client.post(
            f"/api/ai/conversations/{conversation['id']}/task-drafts",
            json={
                "workflow_id": "draft-only-flow",
                "workflow_version_id": draft.version_id,
                "mode": "draft",
            },
        )
        foreign_message = await client.post(
            f"/api/ai/conversations/{conversation['id']}/task-drafts",
            json={
                "source_message_id": foreign["message"]["id"],
                "workflow_id": "draft-only-flow",
                "workflow_version_id": draft.version_id,
                "mode": "draft",
            },
        )

    assert unpublished.status_code == 409
    assert "已发布" in unpublished.json()["detail"]
    assert foreign_message.status_code == 422
    assert "不属于当前线程" in foreign_message.json()["detail"]


async def test_claim_next_queued_run_is_atomic_under_concurrency(sqlite_db):
    import asyncio

    from app.services.ai_conversations import AIConversationStore

    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-atomic-claim",
        workspace_id="ws-atomic-claim",
        title="Atomic claim",
    )
    for content in ("first", "second"):
        await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content=content,
            references=[],
        )

    claims = await asyncio.gather(
        store.claim_next_queued_run(conversation["id"]),
        store.claim_next_queued_run(conversation["id"]),
    )

    claimed = [item for item in claims if item is not None]
    assert len(claimed) == 1
    assert claimed[0]["status"] == "running"
    runs = await store.list_runs(conversation["id"])
    assert [run["status"] for run in runs] == ["running", "queued"]


async def test_duplicate_queue_kick_spawns_once_and_then_advances(sqlite_db, monkeypatch):
    import asyncio

    from app.api import ai_conversations as ai_api
    from app.services.ai_conversations import AIConversationStore

    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-kick",
        workspace_id="ws-kick",
        title="Kick queue",
        runtime_type="agent_runtime",
        agent_runtime_id="runtime-kick",
    )
    for content in ("first", "second"):
        await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content=content,
            references=[],
            run_snapshot={
                "runtime_type": "agent_runtime",
                "agent_runtime_id": "runtime-kick",
                "runtime_snapshot": {"recorded": True, "name": "Codex"},
            },
        )

    started: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    all_done = asyncio.Event()

    async def fake_get_runtime(_self, _runtime_id):
        return {"id": "runtime-kick", "name": "Codex", "enabled": True}

    async def fake_agent_run(*, store, run_id, runtime):
        started.append(run_id)
        if len(started) == 1:
            first_started.set()
            await release_first.wait()
        await store.complete_run(run_id=run_id, content=f"done {len(started)}", references=[])
        if len(started) == 2:
            all_done.set()

    monkeypatch.setattr(ai_api.AgentRuntimeStore, "get_runtime", fake_get_runtime)
    monkeypatch.setattr(ai_api, "run_agent_generation", fake_agent_run)

    ai_api.kick_conversation_queue(conversation["id"])
    ai_api.kick_conversation_queue(conversation["id"])
    await asyncio.wait_for(first_started.wait(), timeout=1)
    await asyncio.sleep(0.03)
    assert len(started) == 1
    runs = await store.list_runs(conversation["id"])
    assert [run["status"] for run in runs] == ["running", "queued"]

    release_first.set()
    await asyncio.wait_for(all_done.wait(), timeout=1)
    assert len(started) == 2
    assert len(set(started)) == 2
    assert [run["status"] for run in await store.list_runs(conversation["id"])] == [
        "completed",
        "completed",
    ]


async def test_two_concurrent_message_posts_spawn_only_one_agent_at_a_time(sqlite_db, monkeypatch):
    import asyncio

    from app.api import ai_conversations as ai_api
    from app.services.ai_conversations import AIConversationStore
    from tests.test_ai_conversations import _test_app

    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-concurrent-post",
        workspace_id="ws-concurrent-post",
        title="Concurrent posts",
        runtime_type="agent_runtime",
        agent_runtime_id="runtime-concurrent",
    )
    started: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    all_done = asyncio.Event()

    async def fake_get_runtime(_self, _runtime_id):
        return {
            "id": "runtime-concurrent",
            "name": "Codex Concurrent",
            "provider": "codex",
            "enabled": True,
            "prompt_transport": "stdin",
        }

    async def fake_agent_run(*, store, run_id, runtime):
        started.append(run_id)
        if len(started) == 1:
            first_started.set()
            await release_first.wait()
        await store.complete_run(run_id=run_id, content=f"answer {len(started)}", references=[])
        if len(started) == 2:
            all_done.set()

    monkeypatch.setattr(ai_api.AgentRuntimeStore, "get_runtime", fake_get_runtime)
    monkeypatch.setattr(ai_api, "run_agent_generation", fake_agent_run)

    app = _test_app(sqlite_db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first_response, second_response = await asyncio.gather(
            client.post(f"/api/ai/conversations/{conversation['id']}/messages", json={"content": "first"}),
            client.post(f"/api/ai/conversations/{conversation['id']}/messages", json={"content": "second"}),
        )
        assert first_response.status_code == 202, first_response.text
        assert second_response.status_code == 202, second_response.text
        await asyncio.wait_for(first_started.wait(), timeout=1)
        await asyncio.sleep(0.03)
        runs = await store.list_runs(conversation["id"])
        assert sorted(run["status"] for run in runs) == ["queued", "running"]
        assert len(started) == 1

        release_first.set()
        await asyncio.wait_for(all_done.wait(), timeout=1)

    assert len(started) == 2
    assert len(set(started)) == 2
    assert [run["status"] for run in await store.list_runs(conversation["id"])] == [
        "completed",
        "completed",
    ]


async def test_agent_spawn_failure_advances_conversation_queue(sqlite_db, monkeypatch):
    import asyncio

    from app.api import ai_conversations as ai_api
    from app.services.ai_conversations import AIConversationStore

    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-spawn-failure",
        workspace_id="ws-spawn-failure",
        title="Spawn failure",
        runtime_type="agent_runtime",
        agent_runtime_id="runtime-spawn-failure",
    )
    for content in ("first", "second"):
        await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content=content,
            references=[],
            run_snapshot={
                "runtime_type": "agent_runtime",
                "agent_runtime_id": "runtime-spawn-failure",
                "runtime_snapshot": {"recorded": True, "name": "Codex", "provider": "codex"},
            },
        )

    attempts: list[str] = []
    completed = asyncio.Event()

    async def fake_get_runtime(_self, _runtime_id):
        return {"id": "runtime-spawn-failure", "name": "Codex", "provider": "codex", "enabled": True}

    async def fake_agent_run(*, store, run_id, runtime):
        attempts.append(run_id)
        if len(attempts) == 1:
            raise RuntimeError("spawn failed")
        await store.complete_run(run_id=run_id, content="second completed", references=[])
        completed.set()

    monkeypatch.setattr(ai_api.AgentRuntimeStore, "get_runtime", fake_get_runtime)
    monkeypatch.setattr(ai_api, "run_agent_generation", fake_agent_run)

    ai_api.kick_conversation_queue(conversation["id"])
    await asyncio.wait_for(completed.wait(), timeout=1)

    runs = await store.list_runs(conversation["id"])
    assert [run["status"] for run in runs] == ["failed", "completed"]
    assert len(attempts) == 2


async def test_retry_preserves_source_run_execution_snapshot(sqlite_db):
    from app.services.ai_conversations import AIConversationStore

    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-retry-snapshot",
        workspace_id="ws-retry-snapshot",
        title="Retry snapshot",
        runtime_type="agent_runtime",
        agent_runtime_id="runtime-a",
    )
    source = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="retry this exact run",
        references=[],
        run_snapshot={
            "execution_mode": "workflow_constraint",
            "runtime_type": "agent_runtime",
            "agent_runtime_id": "runtime-a",
            "runtime_snapshot": {"recorded": True, "name": "Runtime A"},
            "workflow_binding_snapshot": {
                "recorded": True,
                "workflow_version_id": "wfv-frozen",
            },
            "skills_snapshot": ["storage-test"],
            "mcp_snapshot": ["gitnexus"],
            "artifact_contract": {"required_outputs": ["report.md"]},
        },
    )
    await store.fail_run(source["run"]["id"], "failed")
    await store.update_conversation_runtime(
        conversation["id"],
        runtime_type="agent_runtime",
        agent_runtime_id="runtime-b",
    )

    retried = await store.retry_failed_run(
        conversation_id=conversation["id"],
        source_run_id=source["run"]["id"],
    )
    run = retried["run"]

    assert run["runtime_type"] == "agent_runtime"
    assert run["agent_runtime_id"] == "runtime-a"
    assert run["runtime_snapshot"]["name"] == "Runtime A"
    assert run["workflow_binding_snapshot"]["workflow_version_id"] == "wfv-frozen"
    assert run["skills_snapshot"] == ["storage-test"]
    assert run["mcp_snapshot"] == ["gitnexus"]


async def test_run_cockpit_bridge_reuses_ai_thread_and_keeps_context_public(sqlite_db):
    import asyncio
    import json
    from dataclasses import asdict

    from app.config import settings
    from app.services.ai_workbench_links import AIWorkbenchLinkStore
    from app.services.workbench_task_run import PreparedWorkbenchTaskRun
    from app.services.workbench_task_store import WorkbenchTaskStore
    from tests.test_ai_conversations import _test_app

    workspace_id = "ws-run-ai-bridge"
    async with aiosqlite.connect(sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
            "VALUES (?, 'SPDK', '/private/repositories/spdk', 1, '2026-01-01', '2026-01-01')",
            (workspace_id,),
        )
        await db.commit()
    header, published = _published_workflow(
        settings.data_path,
        workflow_id="run-ai-bridge-flow",
    )
    task = WorkbenchTaskStore(
        settings.data_path / "workbench" / "workflows.db"
    ).create_task(
        name="iSCSI Login SFMEA",
        workspace_id=workspace_id,
        workflow_id=header.workflow_id,
        workflow_version_id=published.version_id,
        lifecycle_status="ready",
    )
    run = PreparedWorkbenchTaskRun(
        task_run_id="task_run_ai_bridge",
        task_id=task.task_id,
        attempt_number=2,
        parent_task_run_id="task_run_parent",
        workflow_id=header.workflow_id,
        workspace_id=workspace_id,
        repo_path="/private/repositories/spdk",
        artifact_dir="/private/artifacts/task_run_ai_bridge",
        workflow_snapshot={"id": header.workflow_id, "name": header.name, "version": 1},
        input_snapshot={},
        task_bundle={"workflow_version_id": published.version_id},
        execution_status="failed",
        quality_status="blocked",
        delivery_status="partial",
    )
    run_dir = settings.data_path / "workbench" / "task_runs" / run.task_run_id
    run_dir.mkdir(parents=True)
    (run_dir / "task_run.json").write_text(json.dumps(asdict(run)), encoding="utf-8")
    (run_dir / "task_run_events.jsonl").write_text(
        json.dumps(
            {
                "event_id": 1,
                "task_run_id": run.task_run_id,
                "event_type": "node_failed",
                "payload": {"node_id": "analyze-login"},
                "created_at": "2026-01-01T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    from app.services.workflow_version_store import WorkflowVersionStore

    WorkflowVersionStore(
        settings.data_path / "workbench" / "workflows.db"
    ).update_workflow(header.workflow_id, name="Renamed after Attempt")

    app = _test_app(sqlite_db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first, second = await asyncio.gather(
            client.post(f"/api/ai/conversations/from-task-run/{run.task_run_id}"),
            client.post(f"/api/ai/conversations/from-task-run/{run.task_run_id}"),
        )

    assert {first.status_code, second.status_code} == {200, 201}
    assert first.json()["conversation"]["id"] == second.json()["conversation"]["id"]
    assert {first.json()["created"], second.json()["created"]} == {True, False}
    context = first.json()["conversation"]["initial_context"]
    assert context["task_id"] == task.task_id
    assert context["task_run_id"] == run.task_run_id
    assert context["attempt_number"] == 2
    assert context["workflow_version_id"] == published.version_id
    assert context["workflow_name"] == header.name
    assert context["current_node"] == "analyze-login"
    assert context["artifact_manifest_ref"]["task_run_id"] == run.task_run_id
    serialized = json.dumps(context, ensure_ascii=False)
    assert "/private/repositories" not in serialized
    assert "/private/artifacts" not in serialized

    links = await AIWorkbenchLinkStore(sqlite_db).list_links(task_run_id=run.task_run_id)
    assert len(links) == 1
    assert links[0]["relation_type"] == "run_discussed_by_ai"


async def test_agent_run_coordinator_enforces_global_and_provider_capacity():
    import asyncio

    from app.services.agent_run_coordinator import AgentRunCoordinator

    coordinator = AgentRunCoordinator(
        max_global_agent_processes=2,
        max_processes_per_provider=1,
    )
    codex_one_entered = asyncio.Event()
    release_codex_one = asyncio.Event()
    codex_two_entered = asyncio.Event()
    claude_entered = asyncio.Event()
    queued: list[dict[str, object]] = []

    async def hold_codex_one():
        async with coordinator.slot("codex"):
            codex_one_entered.set()
            await release_codex_one.wait()

    async def hold_codex_two():
        async with coordinator.slot("codex", on_queued=queued.append):
            codex_two_entered.set()

    async def hold_claude():
        async with coordinator.slot("claude"):
            claude_entered.set()

    first = asyncio.create_task(hold_codex_one())
    await asyncio.wait_for(codex_one_entered.wait(), timeout=1)
    second = asyncio.create_task(hold_codex_two())
    await asyncio.sleep(0.03)
    third = asyncio.create_task(hold_claude())
    await asyncio.wait_for(claude_entered.wait(), timeout=1)

    assert not codex_two_entered.is_set()
    assert queued == [
        {
            "active_process_count": 1,
            "global_queue_position": 1,
            "provider_queue_position": 1,
            "queued_reason": "等待 Codex 执行槽位，前方 0 个任务。",
            "provider": "codex",
        }
    ]
    snapshot = await coordinator.snapshot()
    assert snapshot["active_process_count"] == 1
    assert snapshot["active_by_provider"] == {"codex": 1}

    release_codex_one.set()
    await asyncio.wait_for(codex_two_entered.wait(), timeout=1)
    await asyncio.gather(first, second, third)
    assert (await coordinator.snapshot())["active_process_count"] == 0


async def test_public_timeline_pairs_tools_and_exposes_user_facing_categories(sqlite_db):
    from app.services.ai_conversations import AIConversationStore

    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-public-timeline",
        workspace_id="ws-public-timeline",
        title="Public timeline",
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="read source",
        references=[],
    )
    run_id = created["run"]["id"]
    await store.append_event(
        run_id=run_id,
        conversation_id=conversation["id"],
        event_type="delta",
        payload={"kind": "diagnostic", "content": "读取源码 lib/nvmf/tcp.c:L320-L480"},
    )
    await store.append_event(
        run_id=run_id,
        conversation_id=conversation["id"],
        event_type="tool_use",
        payload={"tool": "GitNexus", "call_id": "call-1", "message": "查询 reconnect 路径"},
    )
    await store.append_event(
        run_id=run_id,
        conversation_id=conversation["id"],
        event_type="tool_result",
        payload={"tool": "GitNexus", "call_id": "call-1", "message": "返回 18 个符号"},
    )

    events = await store.list_events_for_run(conversation["id"], run_id)
    source, tool_use, tool_result = events[-3:]
    assert source["timeline"]["category"] == "source_read"
    assert source["timeline"]["title"] == "读取源码"
    assert source["timeline"]["source_ref"] == "lib/nvmf/tcp.c:L320-L480"
    assert tool_use["timeline"]["category"] == "tool_call"
    assert tool_use["timeline"]["title"] == "调用 GitNexus"
    assert tool_result["timeline"]["title"] == "GitNexus 返回结果"
    assert tool_use["timeline"]["tool_pair_id"] == tool_result["timeline"]["tool_pair_id"]
    assert tool_result["timeline"]["status"] == "success"


async def test_ai_task_draft_rejects_superseded_builtin_version(sqlite_db):
    import copy

    from app.config import settings
    from app.services.ai_conversations import AIConversationStore
    from app.services.workflow_presets import active_builtin_workflow_presets
    from app.services.workflow_version_store import WorkflowVersionStore
    from tests.test_ai_conversations import _test_app

    workspace_id = "ws-ai-builtin-version-gate"
    async with aiosqlite.connect(sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
            "VALUES (?, 'Builtin gate', '/repo/builtin', 1, '2026-01-01', '2026-01-01')",
            (workspace_id,),
        )
        await db.commit()
    definition = copy.deepcopy(active_builtin_workflow_presets()[0]["definition"])
    versions = WorkflowVersionStore(settings.data_path / "workbench" / "workflows.db")
    versions.ensure_legacy_published_workflows([definition])
    old_version_id = versions.get_workflow(str(definition["id"])).published_version_id
    definition["description"] = "new canonical release"
    versions.ensure_legacy_published_workflows([definition])
    current_version_id = versions.get_workflow(str(definition["id"])).published_version_id
    assert old_version_id and current_version_id and old_version_id != current_version_id

    conversation = await AIConversationStore(sqlite_db).create_conversation(
        scope_type="workspace",
        scope_id=workspace_id,
        workspace_id=workspace_id,
        title="Old builtin binding",
        initial_context={
            "selected_workflow_id": definition["id"],
            "selected_workflow_version_id": old_version_id,
        },
    )
    app = _test_app(sqlite_db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/ai/conversations/{conversation['id']}/task-drafts",
            json={
                "workflow_id": definition["id"],
                "workflow_version_id": old_version_id,
            },
        )

    assert response.status_code == 409
    assert "最新发布版本" in response.json()["detail"]


async def test_ai_task_draft_validates_source_pair_and_replays_idempotently(sqlite_db):
    from app.config import settings
    from app.services.ai_conversations import AIConversationStore
    from tests.test_ai_conversations import _test_app

    workspace_id = "ws-ai-task-idempotency"
    async with aiosqlite.connect(sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
            "VALUES (?, 'Idempotent', '/repo/idempotent', 1, '2026-01-01', '2026-01-01')",
            (workspace_id,),
        )
        await db.commit()
    header, published = _published_workflow(
        settings.data_path,
        workflow_id="ai-task-idempotent-flow",
    )
    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id=workspace_id,
        workspace_id=workspace_id,
        title="Idempotent draft",
    )
    first_source = await store.create_user_message_and_run(
        conversation_id=conversation["id"], content="first", references=[]
    )
    second_source = await store.create_user_message_and_run(
        conversation_id=conversation["id"], content="second", references=[]
    )
    payload = {
        "source_message_id": first_source["message"]["id"],
        "source_ai_run_id": first_source["run"]["id"],
        "workflow_id": header.workflow_id,
        "workflow_version_id": published.version_id,
    }
    app = _test_app(sqlite_db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mismatch = await client.post(
            f"/api/ai/conversations/{conversation['id']}/task-drafts",
            json={**payload, "source_ai_run_id": second_source["run"]["id"]},
        )
        first = await client.post(
            f"/api/ai/conversations/{conversation['id']}/task-drafts", json=payload
        )
        replay = await client.post(
            f"/api/ai/conversations/{conversation['id']}/task-drafts", json=payload
        )

    assert mismatch.status_code == 422
    assert "不对应" in mismatch.json()["detail"]
    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["task"]["task_id"] == first.json()["task"]["task_id"]


async def test_custom_agent_runtime_persists_explicit_provider(sqlite_db):
    from app.services.agent_runtimes import AgentRuntimeStore

    runtime = await AgentRuntimeStore(sqlite_db).create_runtime(
        {
            "name": "Corporate Codex Wrapper",
            "provider": "codex",
            "command": "corp-agent",
            "prompt_transport": "stdin",
        }
    )

    assert runtime["provider"] == "codex"


async def test_agent_run_coordinator_refreshes_remaining_queue_positions():
    import asyncio

    from app.services.agent_run_coordinator import AgentRunCoordinator

    coordinator = AgentRunCoordinator(
        max_global_agent_processes=1,
        max_processes_per_provider=1,
    )
    release_first = asyncio.Event()
    release_second = asyncio.Event()
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    third_entered = asyncio.Event()
    second_updates: list[dict[str, object]] = []
    third_updates: list[dict[str, object]] = []

    async def first_job():
        async with coordinator.slot("codex"):
            first_entered.set()
            await release_first.wait()

    async def second_job():
        async with coordinator.slot("codex", on_queued=second_updates.append):
            second_entered.set()
            await release_second.wait()

    async def third_job():
        async with coordinator.slot("codex", on_queued=third_updates.append):
            third_entered.set()

    first = asyncio.create_task(first_job())
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    second = asyncio.create_task(second_job())
    third = asyncio.create_task(third_job())
    await asyncio.sleep(0.03)
    assert third_updates[-1]["provider_queue_position"] == 2

    release_first.set()
    await asyncio.wait_for(second_entered.wait(), timeout=1)
    await asyncio.sleep(0.03)
    assert third_updates[-1]["provider_queue_position"] == 1
    assert third_updates[-1]["queued_reason"] == "等待 Codex 执行槽位，前方 0 个任务。"

    release_second.set()
    await asyncio.wait_for(third_entered.wait(), timeout=1)
    await asyncio.gather(first, second, third)
