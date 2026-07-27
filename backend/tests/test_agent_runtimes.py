import asyncio
import json
import os
import pathlib
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.database import get_db

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield


def _test_app(sqlite_db: str) -> FastAPI:
    from app.api import agent_runtimes, ai_conversations

    app = FastAPI(lifespan=_lifespan)
    app.include_router(agent_runtimes.router)
    app.include_router(ai_conversations.router)

    async def _override_get_db():
        conn = await aiosqlite.connect(sqlite_db)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    app.dependency_overrides[get_db] = _override_get_db
    return app


async def _seed_workspace(
    db_path: str,
    ws_id: str = "ws-agent",
    *,
    repo_path: str = "/tmp/codetalk-agent-project",
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
            "VALUES (?, 'Agent 项目', ?, 1, ?, ?)",
            (ws_id, repo_path, now, now),
        )
        await db.commit()
    return ws_id


class TestAgentRuntimes:
    async def test_agent_runtime_persists_network_requirement_with_fail_closed_default(self, sqlite_db):
        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/settings/agent-runtimes",
                json={"name": "Network default", "command": sys.executable},
            )

            assert created.status_code == 201
            runtime = created.json()
            assert runtime["requires_network"] is True

            updated = await client.put(
                f"/api/settings/agent-runtimes/{runtime['id']}",
                json={"requires_network": False},
            )

        assert updated.status_code == 200
        assert updated.json()["requires_network"] is False

    async def test_crud_agent_runtime_keeps_command_and_args_separate(self, sqlite_db):
        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Windows Claude Code",
                    "command": "ccr",
                    "args": ["code"],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                },
            )
            assert created.status_code == 201
            body = created.json()
            assert body["command"] == "ccr"
            assert body["args"] == ["code"]
            assert body["enabled"] is True

            listed = await client.get("/api/settings/agent-runtimes")
            assert listed.status_code == 200
            assert listed.json()["items"][0]["name"] == "Windows Claude Code"

    async def test_custom_agent_runtime_defaults_to_clowder_like_long_task_capture(self, sqlite_db):
        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Corp Agent",
                    "command": sys.executable,
                },
            )

        assert created.status_code == 201
        body = created.json()
        assert body["prompt_transport"] == "stdin"
        assert body["output_mode"] == "auto"
        assert body["completion_mode"] == "process_exit"
        assert body["idle_complete_seconds"] == 5
        assert body["sentinel_text"] == ""
        assert body["session_persistence"] == "none"
        assert body["resume_args"] == []
        assert body["timeout_seconds"] == 900

    @pytest.mark.parametrize(
        ("transport", "output_mode"),
        [
            ("claude_print_arg", "stream_json"),
            ("codex_exec_json", "stream_json"),
            ("opencode_run_arg", "auto"),
        ],
    )
    async def test_managed_agent_runtime_defaults_to_clowder_like_session_resume(
        self,
        sqlite_db,
        transport,
        output_mode,
    ):
        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": f"Managed {transport}",
                    "command": sys.executable,
                    "prompt_transport": transport,
                    "output_mode": output_mode,
                    "session_persistence": "none",
                    "resume_args": [],
                },
            )

        assert created.status_code == 201
        body = created.json()
        assert body["prompt_transport"] == transport
        assert body["session_persistence"] == "resume_args"
        assert body["resume_args"] == []

    async def test_agent_runtime_list_orders_managed_defaults_for_thread_default(self, sqlite_db):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.executemany(
                """
                INSERT INTO agent_runtimes
                    (id, name, command, args_json, prompt_transport, output_mode,
                     working_dir_mode, timeout_seconds, completion_mode,
                     session_persistence, resume_args_json, enabled, created_at, updated_at)
                VALUES (?, ?, ?, '[]', ?, ?, 'project', 900, 'process_exit', ?, '[]', 1, ?, ?)
                """,
                [
                    ("default-opencode", "OpenCode", "opencode", "opencode_run_arg", "auto", "resume_args", now, now),
                    ("custom-agent", "Custom Agent", "custom", "stdin", "plain", "none", now, now),
                    ("default-codex", "Codex", "codex", "codex_exec_json", "stream_json", "resume_args", now, now),
                    ("default-claude-code", "Claude Code", "claude", "claude_print_arg", "stream_json", "resume_args", now, now),
                ],
            )
            await db.commit()

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            listed = await client.get("/api/settings/agent-runtimes")

        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"][:3]] == [
            "default-claude-code",
            "default-codex",
            "default-opencode",
        ]

    async def test_ai_thread_rejects_disabled_agent_runtime_on_create_and_update(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db, "ws-disabled-runtime-api")
        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime_resp = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Disabled Thin Agent",
                    "command": sys.executable,
                    "args": ["--version"],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "fixed_working_dir": "",
                    "env": {},
                    "health_command": "",
                    "timeout_seconds": 30,
                    "enabled": False,
                },
            )
            assert runtime_resp.status_code == 201
            runtime_id = runtime_resp.json()["id"]

            rejected_create = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime_id,
                    "title": "Disabled runtime create",
                },
            )
            assert rejected_create.status_code == 400
            assert "已停用" in rejected_create.text

            conversation_resp = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "runtime_type": "builtin_llm",
                    "title": "Builtin first",
                },
            )
            assert conversation_resp.status_code == 201
            conversation_id = conversation_resp.json()["id"]

            rejected_update = await client.patch(
                f"/api/ai/conversations/{conversation_id}",
                json={
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime_id,
                },
            )
            assert rejected_update.status_code == 400
            assert "已停用" in rejected_update.text

    async def test_ai_thread_rejects_disabled_agent_runtime_before_persisting_message(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db, "ws-disabled-runtime-message-api")
        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime_resp = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Soon Disabled Thin Agent",
                    "command": sys.executable,
                    "args": ["--version"],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "timeout_seconds": 30,
                    "enabled": True,
                },
            )
            assert runtime_resp.status_code == 201
            runtime_id = runtime_resp.json()["id"]

            conversation_resp = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime_id,
                    "title": "Runtime later disabled",
                },
            )
            assert conversation_resp.status_code == 201
            conversation_id = conversation_resp.json()["id"]

            disabled = await client.put(
                f"/api/settings/agent-runtimes/{runtime_id}",
                json={"enabled": False},
            )
            assert disabled.status_code == 200

            rejected_message = await client.post(
                f"/api/ai/conversations/{conversation_id}/messages",
                json={"content": "基于当前 SPDK 源码，输出代码证据、SFMEA 和黑盒测试用例。"},
            )
            assert rejected_message.status_code == 400
            assert "已停用" in rejected_message.text

        async with aiosqlite.connect(sqlite_db) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM ai_messages WHERE conversation_id = ?",
                (conversation_id,),
            ) as cur:
                assert (await cur.fetchone())[0] == 0
            async with db.execute(
                "SELECT COUNT(*) FROM ai_conversation_runs WHERE conversation_id = ?",
                (conversation_id,),
            ) as cur:
                assert (await cur.fetchone())[0] == 0

    async def test_agent_runtime_rejects_shell_command_in_command_field(self, sqlite_db):
        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Bad CCR",
                    "command": "ccr code",
                    "args": [],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                },
            )

            assert created.status_code == 422
            detail = created.json()["detail"]
            assert "command 只能填写可执行文件" in detail
            assert 'args=["code"]' in detail

    async def test_agent_runtime_api_redacts_env_values_but_runtime_keeps_them(self, sqlite_db):
        app = _test_app(sqlite_db)
        secret = "agent-runtime-secret-value"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Secret Agent",
                    "command": sys.executable,
                    "args": ["-V"],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "env": {
                        "AGENT_TOKEN": secret,
                        "SAFE_FLAG": "enabled",
                    },
                },
            )
            assert created.status_code == 201
            runtime_id = created.json()["id"]
            assert created.json()["env"] == {
                "AGENT_TOKEN": "<redacted>",
                "SAFE_FLAG": "<redacted>",
            }
            assert secret not in json.dumps(created.json())

            listed = await client.get("/api/settings/agent-runtimes")
            assert listed.status_code == 200
            assert listed.json()["items"][0]["env"]["AGENT_TOKEN"] == "<redacted>"
            assert secret not in json.dumps(listed.json())

            loaded = await client.get(f"/api/settings/agent-runtimes/{runtime_id}")
            assert loaded.status_code == 200
            assert loaded.json()["env"]["AGENT_TOKEN"] == "<redacted>"
            assert secret not in json.dumps(loaded.json())

        from app.services.agent_runtimes import AgentRuntimeStore

        stored = await AgentRuntimeStore(sqlite_db).get_runtime(runtime_id)
        assert stored["env"]["AGENT_TOKEN"] == secret

    async def test_agent_runtime_probe_redacts_stderr_secrets(self, sqlite_db):
        app = _test_app(sqlite_db)
        secret = "agent-probe-secret-value"
        probe_code = (
            "import sys; "
            f"print('probe failed --api-key {secret}; token={secret}', file=sys.stderr); "
            "raise SystemExit(5)"
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Probe Secret Agent",
                    "command": sys.executable,
                    "args": ["-c", probe_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                },
            )
            assert runtime.status_code == 201

            probed = await client.post(f"/api/settings/agent-runtimes/{runtime.json()['id']}/probe")

            assert probed.status_code == 200
            body = probed.json()
            assert body["success"] is False
            assert "probe failed" in body["message"]
            assert secret not in body["message"]
            assert "<redacted>" in body["message"]

    async def test_agent_runtime_probe_prefers_stderr_when_stdout_has_banner(self, sqlite_db):
        app = _test_app(sqlite_db)
        secret = "agent-probe-banner-secret"
        probe_code = (
            "import sys; "
            "print('agent runtime startup banner: ok'); "
            f"print('fatal diagnostic: missing token {secret}', file=sys.stderr); "
            "raise SystemExit(7)"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Banner Then Failing Agent",
                    "command": sys.executable,
                    "args": ["-c", probe_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                },
            )
            assert runtime.status_code == 201

            probed = await client.post(f"/api/settings/agent-runtimes/{runtime.json()['id']}/probe")

            assert probed.status_code == 200
            body = probed.json()
            assert body["success"] is False
            assert "fatal diagnostic" in body["message"]
            assert "startup banner" not in body["message"]
            assert secret not in body["message"]
            assert "<redacted>" in body["message"]

    async def test_ai_thread_uses_agent_runtime_without_active_llm(self, sqlite_db, monkeypatch):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)

        from app.api import ai_conversations

        async def fail_if_llm_is_used():
            raise AssertionError("agent runtime conversations must not require active_chat_model_id")

        monkeypatch.setattr(ai_conversations, "create_llm_client_from_active", fail_if_llm_is_used)

        agent_code = (
            "import sys; "
            "prompt = sys.stdin.read(); "
            "print('CLI_AGENT_REPLY:' + prompt.split('用户问题：')[-1].strip().splitlines()[0])"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Mock Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
            assert runtime.status_code == 201
            runtime_id = runtime.json()["id"]

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "Agent 线程",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime_id,
                },
            )
            assert created.status_code == 201
            conversation = created.json()
            assert conversation["runtime_type"] == "agent_runtime"
            assert conversation["agent_runtime_id"] == runtime_id

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "解释这个模块的测试风险"},
            )
            assert posted.status_code == 202

            for _ in range(30):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assert "CLI_AGENT_REPLY:解释这个模块的测试风险" in body["items"][1]["content"]

            stream = await client.get(f"/api/ai/conversations/{conversation['id']}/stream")
            events = [
                json.loads(line.removeprefix("data: "))
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
            assert any(evt["event_type"] == "delta" for evt in events)

    async def test_ai_thread_agent_runtime_reads_selected_workspace_source_from_cwd(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        source = repo / "lib" / "nvmf" / "connect.c"
        source.parent.mkdir(parents=True)
        source.write_text(
            "int spdk_nvmf_agent_cwd_probe(void) { return 42; }\n",
            encoding="utf-8",
        )
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-cwd", repo_path=str(repo))
        app = _test_app(sqlite_db)

        from app.api import ai_conversations

        async def fail_if_llm_is_used():
            raise AssertionError("agent runtime conversations must not call the builtin LLM")

        monkeypatch.setattr(ai_conversations, "create_llm_client_from_active", fail_if_llm_is_used)

        agent_code = (
            "from pathlib import Path\n"
            "import os\n"
            "import sys\n"
            "prompt = sys.stdin.read()\n"
            "src = Path('lib/nvmf/connect.c')\n"
            "if not src.exists():\n"
            "    print('missing workspace source in cwd=' + os.getcwd(), file=sys.stderr)\n"
            "    raise SystemExit(9)\n"
            "text = src.read_text(encoding='utf-8')\n"
            "if 'spdk_nvmf_agent_cwd_probe' not in text:\n"
            "    print('source marker missing', file=sys.stderr)\n"
            "    raise SystemExit(10)\n"
            "if 'workspace_source' not in prompt or 'lib/nvmf/connect.c' not in prompt:\n"
            "    print('prompt lacks selected workspace source reference', file=sys.stderr)\n"
            "    raise SystemExit(11)\n"
            "print('## 结论\\nAGENT_CWD_SOURCE_OK:' + os.getcwd() + '\\n\\n## 代码证据\\n- `lib/nvmf/connect.c`: `spdk_nvmf_agent_cwd_probe` 已在当前 agent cwd 中读取。\\n- workspace_source 引用已进入 prompt。\\n\\n## 行为说明\\n1. Agent 以绑定 workspace 作为工作目录。\\n2. Agent 能直接读取选定源码文件并返回证据。')\n"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Workspace Source Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
            assert runtime.status_code == 201

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "Agent workspace 源码读取",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime.json()["id"],
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "请读取 lib/nvmf/connect.c 并确认 agent cwd"},
            )
            assert posted.status_code == 202

            for _ in range(40):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assert f"AGENT_CWD_SOURCE_OK:{repo}" in body["items"][1]["content"]

            stream = await client.get(f"/api/ai/conversations/{conversation['id']}/stream")
            events = [
                json.loads(line.removeprefix("data: "))
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
            assert any(
                evt["event_type"] == "status" and "工作区源码" in evt["payload"].get("message", "")
                for evt in events
            )

    async def test_workbench_ai_review_agent_runtime_uses_task_repo_as_cwd_without_workspace_row(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        source = repo / "lib" / "nvmf" / "connect.c"
        source.parent.mkdir(parents=True)
        source.write_text(
            "int nvmf_workbench_agent_cwd_probe(void) { return 17; }\n",
            encoding="utf-8",
        )
        data_root = tmp_path / "data"
        task_run_id = "task_run_agent_cwd_fallback"
        task_dir = data_root / "workbench" / "task_runs" / task_run_id
        task_dir.mkdir(parents=True)
        (task_dir / "task_run.json").write_text(
            json.dumps(
                {
                    "task_run_id": task_run_id,
                    "workflow_id": "module_analysis",
                    "workspace_id": "ws-workbench-agent-cwd",
                    "repo_path": str(repo),
                    "artifact_dir": str(task_dir),
                    "agent_runs": [],
                }
            ),
            encoding="utf-8",
        )

        from app.config import settings
        from app.api import ai_conversations

        monkeypatch.setattr(settings, "data_dir", str(data_root))

        async def fail_if_llm_is_used():
            raise AssertionError("agent runtime conversations must not call the builtin LLM")

        monkeypatch.setattr(ai_conversations, "create_llm_client_from_active", fail_if_llm_is_used)
        app = _test_app(sqlite_db)
        agent_code = (
            "from pathlib import Path\n"
            "import os\n"
            "import sys\n"
            "sys.stdin.read()\n"
            "src = Path('lib/nvmf/connect.c')\n"
            "if not src.exists():\n"
            "    print('missing workbench task source in cwd=' + os.getcwd(), file=sys.stderr)\n"
            "    raise SystemExit(9)\n"
            "if 'nvmf_workbench_agent_cwd_probe' not in src.read_text(encoding='utf-8'):\n"
            "    print('source marker missing', file=sys.stderr)\n"
            "    raise SystemExit(10)\n"
            "print('## 结论\\nWORKBENCH_CWD_SOURCE_OK:' + os.getcwd() + '\\n\\n## 代码证据\\n- `lib/nvmf/connect.c`: `nvmf_workbench_agent_cwd_probe` 已从 task repo cwd 读取。\\n- workbench task repo_path 被解析为 Agent 工作目录。\\n\\n## 行为说明\\n1. Workbench 线程没有 workspace row 时回退到 task_run repo_path。\\n2. Agent 在该目录读取源码并返回证据。')\n"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Workbench CWD Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
            assert runtime.status_code == 201

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workbench_task_run",
                    "scope_id": task_run_id,
                    "workspace_id": "ws-workbench-agent-cwd",
                    "memory_namespace": "workspace:ws-workbench-agent-cwd",
                    "title": "Workbench Agent CWD",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime.json()["id"],
                    "initial_context": {
                        "workspace_id": "ws-workbench-agent-cwd",
                        "repo_path": f"repo:{repo.name}",
                    },
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "读取 lib/nvmf/connect.c 并确认 workbench cwd"},
            )
            assert posted.status_code == 202

            for _ in range(40):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assert f"WORKBENCH_CWD_SOURCE_OK:{repo}" in body["items"][1]["content"]

    async def test_ai_thread_agent_runtime_prompt_has_machine_readable_source_first_contract(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        source = repo / "lib" / "nvmf" / "connect.c"
        source.parent.mkdir(parents=True)
        source.write_text(
            "int spdk_nvmf_source_first_contract_probe(void) { return 42; }\n",
            encoding="utf-8",
        )
        material = repo / "requirements.md"
        material.write_text("必须覆盖 reconnect timeout。\n", encoding="utf-8")
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-source-contract", repo_path=str(repo))
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspace_materials "
                "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
                "VALUES ('mat-agent-contract', ?, 'requirements.md', 'requirements', ?, 1, ?)",
                (ws_id, str(material), now),
            )
            await db.commit()
        app = _test_app(sqlite_db)

        from app.api import ai_conversations

        async def fail_if_llm_is_used():
            raise AssertionError("agent runtime conversations must not call the builtin LLM")

        monkeypatch.setattr(ai_conversations, "create_llm_client_from_active", fail_if_llm_is_used)

        agent_code = (
            "import sys\n"
            "prompt = sys.stdin.read()\n"
            "required = [\n"
            "  'SOURCE_FIRST_CONTRACT',\n"
            "  'workspace_sources:',\n"
            "  'lib/nvmf/connect.c',\n"
            "  'spdk_nvmf_source_first_contract_probe',\n"
            "  'workspace_materials:',\n"
            "  'requirements.md',\n"
            "  '必须覆盖 reconnect timeout',\n"
            "]\n"
            "missing = [item for item in required if item not in prompt]\n"
            "if missing:\n"
            "    print('missing source-first contract fields: ' + ', '.join(missing), file=sys.stderr)\n"
            "    raise SystemExit(12)\n"
            "print('## 结论\\nAGENT_SOURCE_FIRST_CONTRACT_OK\\n\\n## 代码证据\\n- `lib/nvmf/connect.c`: `spdk_nvmf_source_first_contract_probe` 出现在 SOURCE_FIRST_CONTRACT。\\n- `requirements.md`: 输入材料包含 reconnect timeout 约束。\\n\\n## 行为说明\\n1. Prompt 同时携带 workspace_sources 与 workspace_materials。\\n2. Agent 可先按源码和输入材料回答。')\n"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Source Contract Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
            assert runtime.status_code == 201

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "Agent source-first contract",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime.json()["id"],
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "请读取 lib/nvmf/connect.c 和 requirements.md 再回答"},
            )
            assert posted.status_code == 202

            for _ in range(40):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assert "AGENT_SOURCE_FIRST_CONTRACT_OK" in body["items"][1]["content"]

    async def test_ai_thread_agent_runtime_writes_invocation_manifest(
        self,
        sqlite_db,
        tmp_path,
    ):
        repo = tmp_path / "spdk"
        source = repo / "lib" / "iscsi" / "iscsi.c"
        source.parent.mkdir(parents=True)
        source.write_text(
            "int iscsi_login_invocation_probe(void) { return 0; }\n",
            encoding="utf-8",
        )
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-invocation", repo_path=str(repo))
        app = _test_app(sqlite_db)
        agent_code = (
            "import sys\n"
            "prompt = sys.stdin.read()\n"
            "required = ['TEST_ACTIVITY_CONTRACT', 'ARTIFACT_DELIVERY_CONTRACT', 'iscsi login']\n"
            "missing = [item for item in required if item not in prompt]\n"
            "if missing:\n"
            "    print('missing invocation prompt fields: ' + ', '.join(missing), file=sys.stderr)\n"
            "    raise SystemExit(13)\n"
            "print('## 结论\\n已基于源码分析 iSCSI login。\\n\\n## 代码证据\\n- `lib/iscsi/iscsi.c`: `iscsi_login_invocation_probe`。\\n\\n## 流程梳理\\n1. initiator 发起 login。\\n2. target 校验参数。\\n3. 返回明确 login 状态。\\n\\n## SFMEA\\n| failure mode | cause | effect | detection | severity | occurrence | detection score | RPN | mitigation |\\n| login 参数拒绝不清晰 | 参数协商边界缺失 | initiator 误判重试 | 检查 Login Response 与日志 | 7 | 3 | 4 | 84 | 增加非法参数黑盒用例 |\\n\\n## 黑盒测试用例\\n1. 前置条件：target 已启动；步骤：合法 initiator login；预期结果：进入 full feature；观测点：Login Response 与 session 状态；失败诊断线索：检查 target 配置。\\n2. 前置条件：target 已启动；步骤：提交非法 CHAP 参数；预期结果：login 失败且日志可诊断；观测点：错误码、日志和连接状态；失败诊断线索：检查认证配置。')\n"
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_agent_artifact_dir

        store = AIConversationStore(sqlite_db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Invocation Manifest Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                    "mcp_profile": "gitnexus+cgc",
                },
            )
            assert runtime.status_code == 201

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "Agent invocation manifest",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime.json()["id"],
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            user_task = (
                "请针对 iscsi login 输出源码证据、流程梳理、SFMEA 和黑盒测试用例；"
                "指定输出：项目结构、源码定向阅读、测试视角代码理解"
            )
            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": user_task},
            )
            assert posted.status_code == 202

            for _ in range(60):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                latest = await store.latest_run(conversation["id"])
                if latest and latest["status"] in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assert body["items"][-1]["actions"][0]["id"] == "test_activity_task_card"
            latest = await store.latest_run(conversation["id"])
            assert latest and latest["status"] == "failed"
            assert "质量门禁" in latest["error"]

        run_id = body["items"][0]["run_id"]
        manifest_path = ai_thread_agent_artifact_dir(conversation["id"], run_id) / "agent_invocation.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["source"] == "ai_thread"
        assert manifest["conversation_id"] == conversation["id"]
        assert manifest["run_id"] == run_id
        assert manifest["runtime"]["id"] == runtime.json()["id"]
        assert manifest["runtime"]["name"] == "Invocation Manifest Agent"
        assert manifest["cwd"] == str(repo)
        assert manifest["repo_path"] == str(repo)
        assert manifest["prompt"]["text"].find(user_task) >= 0
        assert manifest["prompt"]["sha256"]
        assert manifest["execution_contract"]["runtime_type"] == "agent_runtime"
        assert manifest["execution_contract"]["outputs"]["user_requested_outputs"] == [
            {
                "source": "user_message",
                "value": "项目结构、源码定向阅读、测试视角代码理解",
                "items": ["项目结构", "源码定向阅读", "测试视角代码理解"],
            },
            {
                "source": "test_activity_contract",
                "items": [
                    "sfmea.json",
                    "black_box_cases.json",
                    "business_flow.md",
                    "project_structure.md",
                ],
            },
        ]
        assert manifest["execution_contract"]["typed_events"] == [
            "answer",
            "thinking",
            "diagnostic",
            "status",
            "tool_use",
            "tool_result",
            "artifact",
            "error",
            "done",
        ]
        assert manifest["test_activity_contract"]["target"] == user_task
        assert manifest["test_activity_contract"]["executor_requirements"]["must_receive_full_user_input"] is True
        assert "sfmea.json" in manifest["artifact_contract"]
        assert "black_box_cases.json" in manifest["artifact_contract"]
        assert manifest["mcp_profile"] == "gitnexus+cgc"
        assert manifest["skills"] == []
        assert manifest["session"]["resume_session_id"] == ""
        assert manifest["artifact_dir"] == str(ai_thread_agent_artifact_dir(conversation["id"], run_id))

    async def test_ai_thread_agent_runtime_failure_redacts_stderr_secrets(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)
        secret = "agent-thread-secret-value"
        agent_code = (
            "import sys; "
            f"print('auth failed --token {secret}; Authorization: Bearer {secret}', file=sys.stderr); "
            "raise SystemExit(7)"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Failing Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
            assert runtime.status_code == 201

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "Agent 失败脱敏",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime.json()["id"],
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "触发失败"},
            )
            assert posted.status_code == 202

            latest = None
            for _ in range(30):
                fetched = await client.get(f"/api/ai/conversations/{conversation['id']}")
                latest = fetched.json()["latest_run"]
                if latest and latest["status"] == "failed":
                    break
                await asyncio.sleep(0.1)

            assert latest is not None
            assert latest["status"] == "failed"
            serialized_run = json.dumps(latest, ensure_ascii=False)
            assert secret not in serialized_run
            assert "执行器运行失败" in serialized_run
            events = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": latest["id"], "limit": 200},
            )
            serialized_events = json.dumps(events.json(), ensure_ascii=False)
            assert secret not in serialized_events
            assert "<redacted>" in serialized_events

            stream = await client.get(f"/api/ai/conversations/{conversation['id']}/stream")
            serialized_events = stream.text
            assert secret not in serialized_events
            assert "<redacted>" in serialized_events

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            assert secret not in json.dumps(messages.json(), ensure_ascii=False)

    async def test_ai_thread_agent_runtime_keeps_status_output_out_of_final_answer(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)
        agent_code = (
            "print('STATUS: 正在读取工作区源码 lib/nvmf/connect.c'); "
            "print('最终答案：STATUS_OUTPUT_FILTER_OK\\nstatus_output_separated=true\\n\\n## 结论\\n已经基于源码生成黑盒测试建议。\\n\\n"
            "## 代码证据\\n- lib/nvmf/connect.c: connect 状态检查。\\n"
            "- test/nvmf: 可承载连接失败路径。\\n\\n"
            "## 黑盒测试用例\\n"
            "1. 用例：合法 connect 成功；前置条件：target 已启动；步骤：发起连接；预期结果：连接建立。\\n"
            "2. 用例：非法参数 connect 失败；前置条件：target 已启动；步骤：提交非法参数；预期结果：返回失败状态。')"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Status Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
            assert runtime.status_code == 201

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "Agent 诊断折叠",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime.json()["id"],
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "分析 connect 的源码行为与可观测结果"},
            )
            assert posted.status_code == 202

            for _ in range(30):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assert "STATUS_OUTPUT_FILTER_OK" in body["items"][1]["content"]
            assert "已经基于源码生成黑盒测试建议" in body["items"][1]["content"]
            assert "## 代码证据" in body["items"][1]["content"]
            assert "## 黑盒测试用例" in body["items"][1]["content"]
            assert "正在读取工作区源码" not in body["items"][1]["content"]

            stream = await client.get(f"/api/ai/conversations/{conversation['id']}/stream")
            events = [
                json.loads(line.removeprefix("data: "))
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
            diagnostics = [
                event["payload"].get("content", "")
                for event in events
                if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
            ]
            answer_chunks = [
                event["payload"].get("content", "")
                for event in events
                if event["event_type"] == "delta" and event["payload"].get("kind") != "diagnostic"
            ]
            assert any("正在读取工作区源码" in item for item in diagnostics)
            assert all("正在读取工作区源码" not in item for item in answer_chunks)

    async def test_ai_thread_agent_runtime_idle_after_output_completes_without_process_exit(self, sqlite_db):
        app = _test_app(sqlite_db)
        repo = pathlib.Path(sqlite_db).parent / "repo"
        repo.mkdir()
        await _seed_workspace(sqlite_db, repo_path=str(repo))
        agent_code = (
            "import sys, time; "
            "sys.stdout.write('最终答案：NGA 已输出完整内容。\\n'); "
            "sys.stdout.flush(); "
            "time.sleep(30)"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=10) as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Idle Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "completion_mode": "idle_after_output",
                    "idle_complete_seconds": 1,
                    "timeout_seconds": 20,
                },
            )
            assert runtime.status_code == 201

            conversation = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": "ws-agent",
                    "workspace_id": "ws-agent",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime.json()["id"],
                    "title": "Idle completion",
                },
            )
            assert conversation.status_code == 201
            sent = await client.post(
                f"/api/ai/conversations/{conversation.json()['id']}/messages",
                json={"content": "运行 NGA"},
            )
            assert sent.status_code == 202
            run_id = sent.json()["run"]["id"]

            for _ in range(30):
                current = await client.get(f"/api/ai/conversations/{conversation.json()['id']}")
                latest = current.json()["latest_run"]
                if latest and latest["id"] == run_id and latest["status"] == "completed":
                    break
                await asyncio.sleep(0.2)
            else:
                pytest.fail("idle_after_output runtime did not complete")

            messages = await client.get(f"/api/ai/conversations/{conversation.json()['id']}/messages")
            assistant = [item for item in messages.json()["items"] if item["role"] == "assistant"][-1]
            assert "NGA 已输出完整内容。" in assistant["content"]
            final_conversation = await client.get(f"/api/ai/conversations/{conversation.json()['id']}")
            assert final_conversation.json()["status"] == "idle"

    async def test_ai_thread_agent_runtime_default_waits_for_process_exit_after_thinking_idle(
        self,
        sqlite_db,
        tmp_path,
    ):
        app = _test_app(sqlite_db)
        repo = tmp_path / "spdk"
        (repo / "lib" / "nvmf").mkdir(parents=True)
        (repo / "lib" / "nvmf" / "ctrlr.c").write_text(
            "int spdk_nvmf_ctrlr_connect_probe(void) { return 0; }\n",
            encoding="utf-8",
        )
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-default-process-exit", repo_path=str(repo))
        agent_script = tmp_path / "thinking_then_answer_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import sys, time",
                    "sys.stdin.read()",
                    "print('thinking: 正在读取工作区源码 lib/nvmf/ctrlr.c', flush=True)",
                    "time.sleep(1.4)",
                    "print('## 结论\\n已基于 `lib/nvmf/ctrlr.c` 完成源码分析。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: `spdk_nvmf_ctrlr_connect_probe`。\\n- `test/nvmf`: 可承载连接路径回归。\\n\\n## 流程梳理\\n1. Agent 先读取源码证据。\\n2. 等待内部分析完成后输出最终答案。', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Default Process Exit Agent",
                    "command": sys.executable,
                    "args": [str(agent_script)],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "idle_complete_seconds": 1,
                    "timeout_seconds": 10,
                },
            )
            assert runtime.status_code == 201

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Default process-exit agent",
            runtime_type="agent_runtime",
            agent_runtime_id=runtime.json()["id"],
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="基于当前 SPDK 源码，输出代码证据和流程梳理。",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(store=store, run_id=run_id, runtime=runtime.json())

        latest = await store.latest_run(conversation["id"])
        assert latest and latest["status"] == "completed"
        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "已基于 `lib/nvmf/ctrlr.c` 完成源码分析" in assistant["content"]
        assert "执行器没有返回有效内容" not in assistant["content"]
        events = await store.list_events_after(conversation["id"])
        diagnostics = "\n".join(
            event["payload"].get("content", "")
            for event in events
            if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
        )
        assert "正在读取工作区源码 lib/nvmf/ctrlr.c" in diagnostics

    async def test_ai_thread_agent_runtime_resumes_saved_cli_session(self, sqlite_db):
        app = _test_app(sqlite_db)
        repo = pathlib.Path(sqlite_db).parent / "resume-repo"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-resume", repo_path=str(repo))
        capture_file = pathlib.Path(sqlite_db).parent / "resume-prompts.jsonl"
        agent_code = (
            "import json, pathlib, sys; "
            "resume=''; "
            "args=sys.argv[1:]; "
            "resume=args[args.index('--resume') + 1] if '--resume' in args else ''; "
            "prompt=sys.stdin.read(); "
            f"capture=pathlib.Path({str(capture_file)!r}); "
            "capture.write_text((capture.read_text() if capture.exists() else '') + json.dumps({'resume': resume, 'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8'); "
            "sid='session-second' if resume else 'session-first'; "
            "print(json.dumps({'type':'system','subtype':'init','session_id':sid}, ensure_ascii=False)); "
            "print(('resumed:' + resume) if resume else 'fresh session'); "
            "sys.stdout.flush()"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Resume Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "auto",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                    "session_persistence": "resume_args",
                    "resume_args": ["-c", agent_code, "--resume", "{session_id}"],
                },
            )
            assert runtime.status_code == 201
            runtime_id = runtime.json()["id"]

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "Agent resume",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime_id,
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            first = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "第一轮"},
            )
            assert first.status_code == 202
            for _ in range(30):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                if len(messages.json()["items"]) == 2:
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("first agent run did not complete")

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            assert messages.json()["items"][-1]["content"] == "fresh session"

            async with aiosqlite.connect(sqlite_db) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM ai_agent_runtime_sessions WHERE conversation_id = ? AND agent_runtime_id = ?",
                    (conversation["id"], runtime_id),
                ) as cur:
                    row = await cur.fetchone()
            assert row is not None
            assert row["resume_session_id"] == "session-first"

            second = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "第二轮"},
            )
            assert second.status_code == 202
            for _ in range(30):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                if len(messages.json()["items"]) == 4:
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("second agent run did not complete")

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            assert messages.json()["items"][-1]["content"] == "resumed:session-first"

            async with aiosqlite.connect(sqlite_db) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM ai_agent_runtime_sessions WHERE conversation_id = ? AND agent_runtime_id = ?",
                    (conversation["id"], runtime_id),
                ) as cur:
                    updated = await cur.fetchone()
            assert updated is not None
            assert updated["resume_session_id"] == "session-second"

            captured_prompts = [json.loads(line) for line in capture_file.read_text(encoding="utf-8").splitlines()]
            assert len(captured_prompts) == 2
            assert captured_prompts[0]["resume"] == ""
            assert captured_prompts[0]["prompt"].count("第一轮") == 1
            assert captured_prompts[1]["resume"] == "session-first"
            assert "第二轮" in captured_prompts[1]["prompt"]
            assert captured_prompts[1]["prompt"].count("第二轮") == 1
            assert "第一轮" not in captured_prompts[1]["prompt"]
            assert "fresh session" not in captured_prompts[1]["prompt"]

    async def test_ai_thread_agent_runtime_self_heals_stale_resume_session(self, sqlite_db, tmp_path):
        repo = tmp_path / "stale-resume-repo"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-stale-resume", repo_path=str(repo))
        capture_file = tmp_path / "stale-resume-invocations.jsonl"
        agent_script = tmp_path / "stale_resume_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, pathlib, sys",
                    f"capture = pathlib.Path({str(capture_file)!r})",
                    "args = sys.argv[1:]",
                    "resume = args[args.index('--resume') + 1] if '--resume' in args else ''",
                    "prompt = sys.stdin.read()",
                    "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'resume': resume, 'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
                    "if resume:",
                    "    print('No conversation found with session ID ' + resume, file=sys.stderr)",
                    "    sys.exit(1)",
                    "print(json.dumps({'type':'system','subtype':'init','session_id':'session-recovered'}, ensure_ascii=False), flush=True)",
                    "print('## 结论\\nRECOVERED_FRESH_SESSION_ANSWER: 已重新创建会话并完成源码分析。\\n\\n## 代码证据\\n- `lib/iscsi/iscsi.c`: login 状态机。\\n- `test/iscsi_tgt`: 可承载黑盒回归。\\n\\n## 黑盒测试用例\\n- 用例：正常登录；前置条件：target 已启动；步骤：initiator 发起 login；预期结果：进入 Full Feature Phase；观测点：响应状态和日志。', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Stale resume self-heal",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-stale-resume",
        )
        previous = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="上一轮：请记录 iSCSI CHAP 失败恢复场景",
            references=[],
        )
        await store.complete_run(
            run_id=previous["run"]["id"],
            content="## 结论\nPREVIOUS_CONTEXT_MARKER: CHAP 失败后应验证重连恢复。",
            references=[],
            model="agent:test",
        )
        await store.upsert_agent_runtime_session(
            conversation_id=conversation["id"],
            agent_runtime_id="runtime-stale-resume",
            cli_session_id="session-stale",
            resume_session_id="session-stale",
            metadata={"run_id": "old-run"},
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="继续基于当前源码分析 iSCSI 登录行为",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-stale-resume",
                "name": "Stale Resume Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
                "session_persistence": "resume_args",
                "resume_args": [str(agent_script), "--resume", "{session_id}"],
            },
        )

        latest = await store.latest_run(conversation["id"])
        assert latest and latest["status"] == "completed"
        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "RECOVERED_FRESH_SESSION_ANSWER" in assistant["content"]
        assert "No conversation found" not in assistant["content"]

        captured = [json.loads(line) for line in capture_file.read_text(encoding="utf-8").splitlines()]
        assert [item["resume"] for item in captured] == ["session-stale", ""]
        assert captured[0]["prompt"].count("继续基于当前源码分析") == 1
        assert "PREVIOUS_CONTEXT_MARKER" not in captured[0]["prompt"]
        assert captured[1]["prompt"].count("继续基于当前源码分析") == 1
        assert "历史助手回复" in captured[1]["prompt"]
        assert "PREVIOUS_CONTEXT_MARKER" in captured[1]["prompt"]
        assert "CHAP 失败后应验证重连恢复" in captured[1]["prompt"]

        session = await store.get_agent_runtime_session(
            conversation_id=conversation["id"],
            agent_runtime_id="runtime-stale-resume",
        )
        assert session is not None
        assert session["resume_session_id"] == "session-recovered"

        events = await store.list_events_after(conversation["id"])
        diagnostics = "\n".join(
            event["payload"].get("content", "")
            for event in events
            if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
        )
        assert "旧会话已失效" in diagnostics

    async def test_ai_thread_agent_runtime_repairs_with_fresh_session_after_stale_resume_self_heal(
        self,
        sqlite_db,
        tmp_path,
    ):
        repo = tmp_path / "stale-resume-repair-repo"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-stale-resume-repair", repo_path=str(repo))
        capture_file = tmp_path / "stale-resume-repair-invocations.jsonl"
        agent_script = tmp_path / "stale_resume_repair_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, pathlib, sys",
                    f"capture = pathlib.Path({str(capture_file)!r})",
                    "args = sys.argv[1:]",
                    "resume = args[args.index('--resume') + 1] if '--resume' in args else ''",
                    "prompt = sys.stdin.read()",
                    "previous = len(capture.read_text(encoding='utf-8').splitlines()) if capture.exists() else 0",
                    "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'turn': previous + 1, 'resume': resume, 'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
                    "if resume:",
                    "    print('No conversation found with session ID ' + resume, file=sys.stderr)",
                    "    sys.exit(1)",
                    "if previous == 1:",
                    "    print('你好，有什么需要帮助？', flush=True)",
                    "else:",
                    "    print('## 结论\\nSTALE_RESUME_REPAIR_FRESH_FINAL: 已在 fresh 会话中完成自动续跑。\\n\\n## 代码证据\\n- `lib/iscsi/iscsi.c`: login 状态机。\\n- `test/iscsi_tgt`: 可承载黑盒回归。\\n\\n## 流程梳理\\n1. 旧 session resume 失败。\\n2. CodeTalk 丢弃旧 session 并 fresh 重试。\\n3. 薄回答触发 repair，repair 仍使用 fresh，而不是旧 session。\\n\\n## 黑盒测试用例\\n- 用例：正常登录；前置条件：target 已启动；步骤：initiator 发起 login；预期结果：进入 Full Feature Phase；观测点：响应状态和日志。', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Stale resume repair self-heal",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-stale-resume-repair",
        )
        await store.upsert_agent_runtime_session(
            conversation_id=conversation["id"],
            agent_runtime_id="runtime-stale-resume-repair",
            cli_session_id="session-stale",
            resume_session_id="session-stale",
            metadata={"run_id": "old-run"},
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="继续基于当前源码分析 iSCSI 登录行为，说明关键文件、调用顺序和异常恢复依据。",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-stale-resume-repair",
                "name": "Stale Resume Repair Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
                "session_persistence": "resume_args",
                "resume_args": [str(agent_script), "--resume", "{session_id}"],
            },
        )

        latest = await store.latest_run(conversation["id"])
        assert latest and latest["status"] == "completed"
        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "STALE_RESUME_REPAIR_FRESH_FINAL" in assistant["content"]
        assert "No conversation found" not in assistant["content"]

        captured = [json.loads(line) for line in capture_file.read_text(encoding="utf-8").splitlines()]
        assert [item["resume"] for item in captured] == ["session-stale", "", ""]
        assert "上一次执行器输出过短" in captured[2]["prompt"]

        session = await store.get_agent_runtime_session(
            conversation_id=conversation["id"],
            agent_runtime_id="runtime-stale-resume-repair",
        )
        assert session is None

    async def test_ai_thread_agent_runtime_repairs_thin_answer_with_latest_resume_session(
        self,
        sqlite_db,
        tmp_path,
    ):
        repo = tmp_path / "resume-repair-repo"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-resume-repair", repo_path=str(repo))
        capture_file = tmp_path / "resume-repair-invocations.jsonl"
        agent_script = tmp_path / "resume_repair_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, pathlib, sys, time",
                    f"capture = pathlib.Path({str(capture_file)!r})",
                    "args = sys.argv[1:]",
                    "session = args[args.index('--session') + 1] if '--session' in args else ''",
                    "prompt = args[-1] if args else ''",
                    "previous = len(capture.read_text(encoding='utf-8').splitlines()) if capture.exists() else 0",
                    "capture.write_text((capture.read_text(encoding='utf-8') if capture.exists() else '') + json.dumps({'turn': previous + 1, 'session': session, 'prompt': prompt, 'argv': args}, ensure_ascii=False) + '\\n', encoding='utf-8')",
                    "if previous == 0:",
                    "    events = [",
                    "        {'type':'thread.started','thread_id':'repair-session-first'},",
                    "        {'type':'message','role':'assistant','content':'你好，有什么需要帮助？'},",
                    "        {'type':'result','status':'success','thread_id':'repair-session-first'},",
                    "    ]",
                    "else:",
                    "    answer = '## 结论\\nRESUME_REPAIR_FINAL: 自动续跑沿用了最新 Agent session。\\n\\n## 代码证据\\n- `README.md`: 当前工作区证据。\\n- `lib/nvmf/ctrlr.c`: connect 流程候选。\\n\\n## 流程梳理\\n1. 首次 Agent 建立 CLI session。\\n2. 薄回答触发 CodeTalk repair。\\n3. repair 通过 --session 续接上一次会话，而不是重新初始化。\\n\\n## SFMEA\\n| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |\\n| connect timeout | transport delay | 连接失败 | 8 | 3 | 4 | 96 | 增加 timeout 黑盒观测 |\\n\\n## 黑盒测试用例\\n1. 用例：正常连接；前置条件：target 已启动；步骤：initiator 发起 connect；预期结果：连接成功；观测点：状态和日志；失败诊断线索：检查 NQN、listener 和 target 日志。\\n2. 用例：连接超时；前置条件：注入网络延迟；步骤：发起 connect；预期结果：超时失败且可重试；观测点：错误码、日志和重连状态；失败诊断线索：检查延迟注入、重试参数和日志时间线。'",
                    "    events = [",
                    "        {'type':'thread.started','thread_id':'repair-session-second'},",
                    "        {'type':'message','role':'assistant','content':answer},",
                    "        {'type':'result','status':'success','thread_id':'repair-session-second'},",
                    "    ]",
                    "for event in events:",
                    "    print(json.dumps(event, ensure_ascii=False), flush=True)",
                    "    time.sleep(0.02)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Resume repair",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-resume-repair",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="基于当前源码分析 NVMe-oF connect，说明关键文件、调用顺序和异常恢复依据。",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-resume-repair",
                "name": "Resume Repair Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "opencode_run_arg",
                "output_mode": "auto",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
                "session_persistence": "resume_args",
            },
        )

        latest = await store.latest_run(conversation["id"])
        assert latest and latest["status"] == "completed"
        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "RESUME_REPAIR_FINAL" in assistant["content"]
        assert "你好，有什么需要帮助" not in assistant["content"]

        captured = [json.loads(line) for line in capture_file.read_text(encoding="utf-8").splitlines()]
        assert [item["session"] for item in captured] == ["", "repair-session-first"]
        assert "上一次执行器输出过短" in captured[1]["prompt"]
        assert "--session" in captured[1]["argv"]

        session = await store.get_agent_runtime_session(
            conversation_id=conversation["id"],
            agent_runtime_id="runtime-resume-repair",
        )
        assert session is not None
        assert session["resume_session_id"] == "repair-session-second"

    async def test_ai_thread_claude_transport_manages_print_mode_and_resume_without_user_args(self, sqlite_db):
        app = _test_app(sqlite_db)
        repo = pathlib.Path(sqlite_db).parent / "claude-provider-repo"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-claude-provider", repo_path=str(repo))
        capture_file = pathlib.Path(sqlite_db).parent / "claude-argv.jsonl"
        agent_script = pathlib.Path(sqlite_db).parent / "fake_claude_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, pathlib, sys",
                    f"path = pathlib.Path({str(capture_file)!r})",
                    "args = sys.argv[1:]",
                    "path.write_text((path.read_text() if path.exists() else '') + json.dumps(args, ensure_ascii=False) + '\\n')",
                    "resume = args[args.index('--resume') + 1] if '--resume' in args else ''",
                    "sid = 'claude-second' if resume else 'claude-first'",
                    "print(json.dumps({'type':'system','subtype':'init','session_id':sid}, ensure_ascii=False))",
                    "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':('resumed:' + resume) if resume else 'fresh claude'}]}}, ensure_ascii=False))",
                    "sys.stdout.flush()",
                ]
            )
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Managed Claude",
                    "command": sys.executable,
                    "args": [str(agent_script)],
                    "prompt_transport": "claude_print_arg",
                    "output_mode": "stream_json",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                    "session_persistence": "resume_args",
                },
            )
            assert runtime.status_code == 201, runtime.text
            runtime_id = runtime.json()["id"]

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "Managed Claude",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime_id,
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            for expected in ("fresh claude", "resumed:claude-first"):
                posted = await client.post(
                    f"/api/ai/conversations/{conversation['id']}/messages",
                    json={"content": f"问：{expected}"},
                )
                assert posted.status_code == 202
                for _ in range(30):
                    messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                    items = messages.json()["items"]
                    if items and items[-1]["role"] == "assistant" and expected in items[-1]["content"]:
                        break
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail(f"managed Claude run did not produce {expected}")

            captured = [json.loads(line) for line in capture_file.read_text().splitlines()]
            assert "-p" in captured[0]
            assert "--output-format" in captured[0]
            assert "stream-json" in captured[0]
            assert "--include-partial-messages" in captured[0]
            assert "--verbose" in captured[0]
            assert "--resume" not in captured[0]
            assert captured[1][captured[1].index("--resume") + 1] == "claude-first"

    async def test_ai_thread_codex_transport_builds_exec_json_resume_without_sentinel(self, sqlite_db):
        app = _test_app(sqlite_db)
        repo = pathlib.Path(sqlite_db).parent / "codex-provider-repo"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-codex-provider", repo_path=str(repo))
        capture_file = pathlib.Path(sqlite_db).parent / "codex-invocations.jsonl"
        agent_script = pathlib.Path(sqlite_db).parent / "fake_codex_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, pathlib, sys",
                    f"path = pathlib.Path({str(capture_file)!r})",
                    "args = sys.argv[1:]",
                    "stdin = sys.stdin.read()",
                    "path.write_text((path.read_text() if path.exists() else '') + json.dumps({'argv': args, 'stdin': stdin}, ensure_ascii=False) + '\\n')",
                    "resume = args[args.index('resume') + 1] if 'resume' in args else ''",
                    "tid = 'codex-second' if resume else 'codex-first'",
                    "print(json.dumps({'type':'thread.started','thread_id':tid}, ensure_ascii=False))",
                    "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':('resumed:' + resume) if resume else 'fresh codex'}}, ensure_ascii=False))",
                    "sys.stdout.flush()",
                ]
            )
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Managed Codex",
                    "command": sys.executable,
                    "args": [str(agent_script)],
                    "prompt_transport": "codex_exec_json",
                    "output_mode": "stream_json",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                    "session_persistence": "resume_args",
                },
            )
            assert runtime.status_code == 201, runtime.text
            runtime_id = runtime.json()["id"]

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "Managed Codex",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime_id,
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            prompts = [
                ("fresh codex", "问：fresh codex"),
                ("resumed:codex-first", "问：resumed:codex-first"),
            ]
            for expected, user_prompt in prompts:
                posted = await client.post(
                    f"/api/ai/conversations/{conversation['id']}/messages",
                    json={"content": user_prompt},
                )
                assert posted.status_code == 202
                for _ in range(30):
                    messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                    items = messages.json()["items"]
                    if items and items[-1]["role"] == "assistant" and expected in items[-1]["content"]:
                        break
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail(f"managed Codex run did not produce {expected}")

            captured = [json.loads(line) for line in capture_file.read_text().splitlines()]
            first_argv = captured[0]["argv"]
            second_argv = captured[1]["argv"]
            assert "--json" in first_argv
            assert "resume" not in first_argv
            assert "fresh codex" not in " ".join(first_argv)
            assert "问：fresh codex" in captured[0]["stdin"]
            assert "resume" in second_argv
            assert second_argv[second_argv.index("resume") + 1] == "codex-first"
            assert "--json" in second_argv
            assert "resumed:codex-first" not in " ".join(second_argv)
            assert "问：resumed:codex-first" in captured[1]["stdin"]

    async def test_agent_runtime_codex_exit_one_after_substantive_output_is_success(self, tmp_path):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_script = tmp_path / "fake_codex_exit_one_after_answer.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "print(json.dumps({'type':'thread.started','thread_id':'codex-exit-one'}, ensure_ascii=False), flush=True)",
                    "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'CODEX_EXIT_ONE_FINAL_ANSWER 已基于源码完成分析。'}}, ensure_ascii=False), flush=True)",
                    "print('Codex CLI exited with code 1 after final answer', file=sys.stderr, flush=True)",
                    "raise SystemExit(1)",
                ]
            ),
            encoding="utf-8",
        )

        chunks: list[str] = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "codex_exec_json",
                "output_mode": "stream_json",
                "timeout_seconds": 10,
            },
            prompt="读取源码后回答",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert "CODEX_EXIT_ONE_FINAL_ANSWER 已基于源码完成分析。" in output

    async def test_ai_thread_agent_runtime_keeps_json_status_events_out_of_final_answer(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)
        agent_code = (
            "import json; "
            "print(json.dumps({'type':'status','message':'正在调用外部 agent 读取源码'}, ensure_ascii=False)); "
            "print(json.dumps({'content':'最终答案：STATUS_EVENT_FILTER_OK\\nstatus_event_separated=true'}, ensure_ascii=False))"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "JSON Status Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "stream_json",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
            assert runtime.status_code == 201

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "JSON Agent 诊断折叠",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime.json()["id"],
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "分析外部 agent JSON 状态流"},
            )
            assert posted.status_code == 202

            for _ in range(30):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assert "STATUS_EVENT_FILTER_OK" in body["items"][1]["content"]
            assert "正在调用外部 agent" not in body["items"][1]["content"]

            stream = await client.get(f"/api/ai/conversations/{conversation['id']}/stream")
            events = [
                json.loads(line.removeprefix("data: "))
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
            diagnostics = [
                event["payload"].get("content", "")
                for event in events
                if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
            ]
            assert any("正在调用外部 agent" in item for item in diagnostics)

    async def test_ai_thread_agent_runtime_keeps_json_error_events_out_of_final_answer(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)
        agent_code = (
            "import json; "
            "print(json.dumps({'type':'error','error':{'message':'临时工具错误：索引尚未就绪'}}, ensure_ascii=False)); "
            "print(json.dumps({'content':'最终答案：外部 agent 已恢复并完成源码证据分析。'}, ensure_ascii=False))"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "JSON Error Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "stream_json",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
            assert runtime.status_code == 201

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "JSON Agent 错误诊断折叠",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime.json()["id"],
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "分析外部 agent JSON 错误事件"},
            )
            assert posted.status_code == 202

            for _ in range(30):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assert "外部 agent 已恢复并完成源码证据分析。" in body["items"][1]["content"]
            assert "临时工具错误" not in body["items"][1]["content"]

            stream = await client.get(f"/api/ai/conversations/{conversation['id']}/stream")
            events = [
                json.loads(line.removeprefix("data: "))
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
            diagnostics = [
                event["payload"].get("content", "")
                for event in events
                if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
            ]
            assert any("临时工具错误：索引尚未就绪" in item for item in diagnostics)

    async def test_ai_thread_agent_runtime_keeps_response_reasoning_out_of_final_answer(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)
        agent_code = (
            "import json; "
            "print(json.dumps({'type':'response.reasoning_text.delta','delta':'内部推理：先搜索源码'}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.output_text.delta','delta':'最终答案：## 结论\\n已完成可交付分析。\\n\\n## 代码证据\\n- lib/nvmf/connect.c: 响应路径。\\n- test/nvmf: 回归入口。\\n\\n## 流程梳理\\n1. 读取输入。\\n2. 输出结论。'}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.refusal.delta','delta':'拒绝诊断：策略提示'}, ensure_ascii=False))"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Responses Reasoning Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "auto",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
            assert runtime.status_code == 201

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "Responses reasoning 诊断折叠",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime.json()["id"],
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "分析 Responses reasoning 输出"},
            )
            assert posted.status_code == 202

            for _ in range(30):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assert "已完成可交付分析" in body["items"][1]["content"]
            assert "## 代码证据" in body["items"][1]["content"]
            assert "## 流程梳理" in body["items"][1]["content"]
            assert "内部推理" not in body["items"][1]["content"]
            assert "拒绝诊断" not in body["items"][1]["content"]

            stream = await client.get(f"/api/ai/conversations/{conversation['id']}/stream")
            events = [
                json.loads(line.removeprefix("data: "))
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
            diagnostics = [
                event["payload"].get("content", "")
                for event in events
                if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
            ]
            assert any("内部推理：先搜索源码" in item for item in diagnostics)
            assert any("拒绝诊断：策略提示" in item for item in diagnostics)

    async def test_ai_thread_agent_runtime_collapses_full_source_dump_from_visible_answer(
        self,
        sqlite_db,
        tmp_path,
    ):
        repo = tmp_path / "spdk"
        source = repo / "lib" / "nvmf" / "auth.c"
        source.parent.mkdir(parents=True)
        source.write_text(
            "\n".join(
                [
                    "/* SPDX-License-Identifier: BSD-3-Clause */",
                    '#include "spdk/stdinc.h"',
                    '#include "spdk/nvmf.h"',
                    '#include "nvmf_internal.h"',
                    "",
                    "static int spdk_nvmf_auth_probe_0(void) { return 0; }",
                    *[
                        f"static int spdk_nvmf_auth_probe_{index}(void) {{ return {index}; }}"
                        for index in range(1, 70)
                    ],
                ]
            ),
            encoding="utf-8",
        )
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-source-dump", repo_path=str(repo))
        app = _test_app(sqlite_db)
        agent_code = (
            "from pathlib import Path\n"
            "text = Path('lib/nvmf/auth.c').read_text(encoding='utf-8')\n"
            "print(text)\n"
            "print('## 结论\\nSOURCE_DUMP_FILTER_OK\\nsource_dump_hidden=true\\n源码全文已读取，证据文件为 lib/nvmf/auth.c。')\n"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "Source Dump Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
            assert runtime.status_code == 201

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "Agent 源码全文折叠",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime.json()["id"],
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "基于 nvmf auth 源码总结外部行为边界，不要输出源码全文"},
            )
            assert posted.status_code == 202

            for _ in range(40):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assistant = body["items"][1]["content"]
            assert "SOURCE_DUMP_FILTER_OK" in assistant
            assert "源码全文" in assistant
            assert "已折叠" in assistant
            assert "lib/nvmf/auth.c" in assistant
            assert '#include "spdk/stdinc.h"' not in assistant
            assert "spdk_nvmf_auth_probe_69" not in assistant

            stream = await client.get(f"/api/ai/conversations/{conversation['id']}/stream")
            events = [
                json.loads(line.removeprefix("data: "))
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
            answer_chunks = [
                event["payload"].get("content", "")
                for event in events
                if event["event_type"] == "delta" and event["payload"].get("kind") != "diagnostic"
            ]
            visible_stream = "".join(answer_chunks)
            assert "SOURCE_DUMP_FILTER_OK" in visible_stream
            assert "源码全文已读取" in visible_stream
            assert "lib/nvmf/auth.c" in visible_stream
            assert '#include "spdk/stdinc.h"' not in visible_stream
            assert "spdk_nvmf_auth_probe_69" not in visible_stream

    async def test_ai_thread_agent_runtime_keeps_tool_events_out_of_final_answer(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)
        agent_code = (
            "import json; "
            "print(json.dumps({'type':'tool_use','message':'正在调用 rg 搜索源码'}, ensure_ascii=False)); "
            "print(json.dumps({'content':'最终答案：已根据源码证据完成分析。'}, ensure_ascii=False))"
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            runtime = await client.post(
                "/api/settings/agent-runtimes",
                json={
                    "name": "JSON Tool Agent",
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "stream_json",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
            assert runtime.status_code == 201

            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "title": "JSON Agent 工具诊断折叠",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": runtime.json()["id"],
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "分析外部 agent 工具事件"},
            )
            assert posted.status_code == 202

            for _ in range(30):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assert "已根据源码证据完成分析。" in body["items"][1]["content"]
            assert "正在调用 rg 搜索源码" not in body["items"][1]["content"]

            stream = await client.get(f"/api/ai/conversations/{conversation['id']}/stream")
            events = [
                json.loads(line.removeprefix("data: "))
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
            diagnostics = [
                event["payload"].get("content", "")
                for event in events
                if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
            ]
            assert any("正在调用 rg 搜索源码" in item for item in diagnostics)

    async def test_ai_thread_agent_runtime_streams_safe_answer_before_process_exit(self, sqlite_db, tmp_path):
        repo = tmp_path / "live-repo"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-live-stream", repo_path=str(repo))
        agent_script = tmp_path / "slow_live_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import sys",
                    "import time",
                    "sys.stdin.read()",
                    "print('agent-runtime-live-first-delta', flush=True)",
                    "time.sleep(2)",
                    "print('agent-runtime-live-final-delta', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Agent live stream",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-live-stream",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="开始一个长时间运行的 agent 调查",
            references=[],
        )
        run_id = created["run"]["id"]
        task = asyncio.create_task(
            run_agent_generation(
                store=store,
                run_id=run_id,
                runtime={
                    "id": "runtime-live-stream",
                    "name": "Live Stream Agent",
                    "command": sys.executable,
                    "args": [str(agent_script)],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
        )
        try:
            for _ in range(40):
                events = await store.list_events_after(conversation["id"])
                live_answer_seen = any(
                    event["event_type"] == "delta"
                    and event["payload"].get("kind") != "diagnostic"
                    and "agent-runtime-live-first-delta" in event["payload"].get("content", "")
                    for event in events
                )
                if live_answer_seen:
                    latest = await store.latest_run(conversation["id"])
                    assert latest and latest["status"] == "running"
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("agent runtime answer delta was not visible while the process was still running")

            await task
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "agent-runtime-live-first-delta" in assistant["content"]
        assert "agent-runtime-live-final-delta" in assistant["content"]

    async def test_ai_thread_agent_runtime_repairs_thin_greeting_answer(self, sqlite_db, tmp_path):
        repo = tmp_path / "spdk"
        (repo / "lib" / "iscsi").mkdir(parents=True)
        (repo / "lib" / "iscsi" / "iscsi.c").write_text(
            "int iscsi_login_probe(void) { return 0; }\n",
            encoding="utf-8",
        )
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-thin-answer", repo_path=str(repo))
        state_file = tmp_path / "thin-agent-state.txt"
        prompt_log = tmp_path / "thin-agent-prompts.jsonl"
        agent_script = tmp_path / "thin_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, pathlib, sys",
                    f"state = pathlib.Path({str(state_file)!r})",
                    f"prompt_log = pathlib.Path({str(prompt_log)!r})",
                    "prompt = sys.stdin.read()",
                    "previous = int(state.read_text() or '0') if state.exists() else 0",
                    "prompt_log.write_text((prompt_log.read_text() if prompt_log.exists() else '') + json.dumps({'turn': previous + 1, 'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
                    "state.write_text(str(previous + 1), encoding='utf-8')",
                    "if previous == 0:",
                    "    print('thinking: 已检查 lib/iscsi/iscsi.c', flush=True)",
                    "    print('你好，有什么需要帮助？', flush=True)",
                    "else:",
                    "    print('## 结论\\n已基于 `lib/iscsi/iscsi.c` 输出 iSCSI login 黑盒测试设计。\\n\\n## 代码证据\\n- `lib/iscsi/iscsi.c`: `iscsi_login_probe`。\\n- `test/iscsi_tgt`: 可承载 login 失败路径回归。\\n\\n## 流程梳理\\n1. initiator 发起 login。\\n2. target 校验参数并返回状态。\\n\\n## SFMEA\\n| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |\\n| login 参数越界 | 协商值非法 | login 被拒绝 | 8 | 3 | 4 | 96 | 增加边界 PDU 测试 |\\n\\n## 黑盒测试用例\\n1. 用例：合法 login 成功；前置条件：target 已启动；步骤：发起 login；预期结果：进入 full feature；观测点：Login Response 和 session 状态。\\n2. 用例：非法参数 login 失败；前置条件：target 已启动；步骤：提交越界参数；预期结果：返回失败状态并记录日志；失败诊断线索：若状态仍 running，排查 target 配置与 initiator 参数。', flush=True)",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Thin answer repair",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-thin-answer",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content=(
                "基于当前 SPDK 源码分析 iSCSI login 行为，"
                "说明关键文件、调用顺序和异常恢复依据。"
            ),
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-thin-answer",
                "name": "Thin Answer Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        prompts = [json.loads(line) for line in prompt_log.read_text(encoding="utf-8").splitlines()]
        assert [item["turn"] for item in prompts] == [1, 2]
        assert "上一次执行器输出过短" in prompts[1]["prompt"]
        assert "不要只问候用户" in prompts[1]["prompt"]

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "你好，有什么需要帮助" not in assistant["content"]
        assert "## 结论" in assistant["content"]
        assert "lib/iscsi/iscsi.c" in assistant["content"]

        events = await store.list_events_after(conversation["id"])
        diagnostics = "\n".join(
            event["payload"].get("content", "")
            for event in events
            if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
        )
        assert "输出过短" in diagnostics

    async def test_ai_thread_agent_runtime_rejects_structured_answer_missing_requested_sfmea(self):
        from app.services.ai_conversations import _agent_answer_requires_repair

        user_message = (
            "基于当前 SPDK 源码，分析 iSCSI login 流程，"
            "输出代码证据、流程梳理、SFMEA 和黑盒测试用例。"
        )
        thin_answer = "\n".join(
            [
                "## 结论",
                "已基于 `lib/iscsi/iscsi.c` 输出 iSCSI login 黑盒测试设计。",
                "## 代码证据",
                "- `lib/iscsi/iscsi.c`: `iscsi_conn_login_do_work`。",
                "## 流程梳理",
                "1. initiator 发起 login。",
                "2. target 校验参数并返回状态。",
                "## 黑盒测试用例",
                "1. 前置条件：target 已启动。步骤：发起 login。预期结果：返回明确状态并记录日志。",
            ]
        )

        assert _agent_answer_requires_repair(user_message, thin_answer, []) is True

        complete_answer = thin_answer + "\n".join(
            [
                "",
                "## SFMEA",
                "| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |",
                "| login auth bypass | CHAP 配置错误 | 非授权 initiator 接入 | 9 | 3 | 4 | 108 | 增加拒绝路径测试 |",
                "2. 用例：非法 InitiatorName 被拒绝；前置条件、步骤、预期结果、观测点完整。",
                "失败诊断线索：如果未返回拒绝状态，优先排查 target CHAP 配置和 initiator 登录参数。",
            ]
        )

        assert _agent_answer_requires_repair(user_message, complete_answer, []) is False

    async def test_ai_thread_agent_runtime_rejects_blackbox_cases_without_observability(self):
        from app.services.ai_conversations import _agent_answer_requires_repair

        user_message = "针对 iSCSI 登录写两个黑盒用例，先读源码证据"
        coarse_answer = "\n".join(
            [
                "## 代码证据",
                "- `lib/iscsi/iscsi.c:1539`: CHAP AuthMethod 协商路径。",
                "- `test/iscsi_tgt`: 可承载登录黑盒回归。",
                "## 黑盒测试用例",
                "### TC-01 正常登录",
                "前置条件：target 已启动；步骤：initiator 发起 iSCSI Login；预期结果：进入 Full Feature Phase。",
                "### TC-02 CHAP 失败",
                "前置条件：target 开启 CHAP；步骤：使用错误 secret 登录；预期结果：Login Response 拒绝。",
            ]
        )

        assert _agent_answer_requires_repair(user_message, coarse_answer, []) is True

        executable_answer = coarse_answer + "\n".join(
            [
                "",
                "观测点：Login Response status class/detail、`iscsi_get_connections` state/login_phase、target 日志。",
                "失败诊断线索：若状态进入 running，检查 CHAP 配置是否未启用；若无日志，检查 initiator 是否真正触发登录。",
            ]
        )

        assert _agent_answer_requires_repair(user_message, executable_answer, []) is False

        single_case_answer = "\n".join(
            [
                "## 代码证据",
                "- `lib/iscsi/iscsi.c:1539`: CHAP AuthMethod 协商路径。",
                "- `test/iscsi_tgt`: 可承载登录黑盒回归。",
                "## 黑盒测试用例",
                "### TC-01 正常登录",
                "前置条件：target 已启动；步骤：initiator 发起 iSCSI Login；预期结果：进入 Full Feature Phase。",
                "观测点：Login Response status class/detail、`iscsi_get_connections` state/login_phase、target 日志。",
                "失败诊断线索：若状态进入 running，检查 CHAP 配置是否未启用；若无日志，检查 initiator 是否真正触发登录。",
            ]
        )
        assert _agent_answer_requires_repair(user_message, single_case_answer, []) is True

    async def test_ai_thread_agent_runtime_rejects_irrelevant_source_evidence_for_specific_flow(self):
        from app.services.ai_conversations import _agent_answer_requires_repair

        user_message = (
            "请基于当前 SPDK 工作区源码分析 NVMe-oF target connect 到 IO ready 的主链路，"
            "输出代码证据、流程梳理、SFMEA 和黑盒测试用例。"
        )
        weak_source_evidence = (
            "## 结论\n"
            "已生成 SPDK NVMe-oF connect 测试设计。\n\n"
            "## 代码证据\n"
            "- `lib/nvmf/ctrlr.c:42`: `struct spdk_nvmf_custom_admin_cmd`。\n"
            "- `lib/nvmf/ctrlr.c:47`: `g_nvmf_custom_admin_cmd_hdlrs`。\n\n"
            "## 流程梳理\n"
            "1. initiator 发起 connect。\n"
            "2. target 建立 controller 并进入 IO ready。\n\n"
            "## SFMEA\n"
            "| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |\n"
            "| connect timeout | transport delay | 连接失败 | 8 | 3 | 4 | 96 | 增加超时用例 |\n\n"
            "## 黑盒测试用例\n"
            "1. 用例：正常 connect；前置条件：target 已启动；步骤：发起连接；"
            "预期结果：连接成功；观测点：RPC 状态和日志；失败诊断线索：检查 listener。\n"
            "2. 用例：connect timeout；前置条件：网络延迟；步骤：发起连接；"
            "预期结果：超时失败；观测点：错误码和日志；失败诊断线索：检查超时配置。\n"
        )
        relevant_source_evidence = weak_source_evidence.replace(
            "`struct spdk_nvmf_custom_admin_cmd`",
            "`nvmf_ctrlr_connect` handles the connect command",
        )

        assert _agent_answer_requires_repair(user_message, weak_source_evidence, []) is True
        assert _agent_answer_requires_repair(user_message, relevant_source_evidence, []) is False

    async def test_ai_thread_agent_runtime_does_not_treat_codex_as_code_task(self):
        from app.services.ai_conversations import _agent_answer_requires_repair

        assert _agent_answer_requires_repair("问：fresh codex", "fresh codex", []) is False
        assert (
            _agent_answer_requires_repair(
                "请读取工作区源码并说明 Codex transport stdin",
                "fresh codex stdin",
                [],
            )
            is True
        )
        assert (
            _agent_answer_requires_repair(
                "第一轮：验证 Codex transport stdin prompt delivery",
                "CODEX_STDIN_REPLY prompt_transport_ok=true fresh",
                [{"source_type": "workspace_source", "title": "README.md"}],
            )
            is False
        )

    async def test_ai_thread_agent_runtime_accepts_explicit_probe_style_agent_output(self):
        from app.services.ai_conversations import _agent_answer_requires_repair

        references = [
            {"source_type": "workspace_source", "title": "lib/nvmf/material_probe.c"},
            {"source_type": "workspace_material", "title": "requirements.md"},
        ]

        assert (
            _agent_answer_requires_repair(
                "请分析 lib/nvmf/material_probe.c，并结合 requirements.md 生成黑盒测试重点",
                "MATERIAL_SOURCE_CONTEXT_OK requirements.md lib/nvmf/material_probe.c\n",
                references,
            )
            is False
        )
        assert (
            _agent_answer_requires_repair(
                "第一行：分析 SPDK iSCSI login\n第二行：输出流程梳理\n第三行：生成 SFMEA 和黑盒测试用例",
                "\n".join(
                    [
                        "MANAGED_MULTILINE_AGENT_REPLY",
                        "argv_has_full_multiline=true",
                        "prompt_file_has_full_multiline=true",
                        "argv_line_occurrences=1/1/1",
                    ]
                ),
                [],
            )
            is False
        )
        assert (
            _agent_answer_requires_repair(
                "第一行：分析 SPDK reconnect\n第二行：保留上下文再发送",
                "\n".join(
                    [
                        "KEYBOARD_AGENT_REPLY",
                        "has_multiline_prompt=true",
                        "user_line_occurrences=1/1",
                    ]
                ),
                [],
            )
            is False
        )
        assert (
            _agent_answer_requires_repair(
                "分析 SPDK NVMe-oF target connect 到 IO 提交流程，并列出关键文件证据",
                "\n".join(
                    [
                        "SPDK agent completed analysis",
                        "Evidence: lib/nvmf/ctrlr.c nvmf_ctrlr_connect",
                        "Flow: connect request -> controller setup -> IO queue ready",
                    ]
                ),
                [],
            )
            is False
        )
        assert _agent_answer_requires_repair("基于源码分析 SPDK iSCSI login", "你好，有什么需要帮助", []) is True

        source_grounded_blackbox = (
            "## 结论\n"
            "已生成结构化产物，最终 result 事件作为唯一正文来源。\n\n"
            "## 代码证据\n"
            "- `lib/iscsi/iscsi.c`: 登录状态机源码文件用于约束测试范围。\n"
            "- `test/iscsi_tgt`: 可映射黑盒登录回归。\n\n"
            "## 流程梳理\n"
            "1. Agent 先执行源码查找。\n"
            "2. 工具输出进入折叠过程。\n"
            "3. result 字段产出最终测试设计。\n\n"
            "## 黑盒测试用例\n"
            "1. TC-01 Result 登录场景：前置条件 target 已启动，步骤执行 iSCSI Login，"
            "预期结果可观测，观测点为 Login Response、session 状态和日志。\n"
            "2. TC-02 Result 登录场景：前置条件 target 已启动，步骤执行 CHAP Login，"
            "预期结果可观测，观测点为 Login Response、session 状态和日志。\n"
        )
        assert (
            _agent_answer_requires_repair(
                "针对 iSCSI 登录生成黑盒测试用例\n先查源码，再把正式答案作为最终结果输出",
                source_grounded_blackbox,
                [{"source_type": "workspace_source"}],
            )
            is False
        )

        blackbox_without_triage = (
            "## 代码证据\n"
            "- `lib/nvmf/ctrlr.c`: connect 入口证据。\n\n"
            "## 黑盒测试用例\n"
            "1. 用例：正常连接；前置条件：target 已启动；步骤：initiator 发起 connect；"
            "预期结果：连接成功；观测点：RPC 状态和日志。\n"
            "2. 用例：连接超时；前置条件：注入网络延迟；步骤：发起 connect 并等待超时；"
            "预期结果：返回超时错误；观测点：错误码和日志。\n"
        )
        assert _agent_answer_requires_repair("输出完整测试设计和黑盒测试用例", blackbox_without_triage, []) is True

    async def test_ai_thread_agent_runtime_repairs_one_line_source_answer(self, sqlite_db, tmp_path):
        repo = tmp_path / "spdk"
        (repo / "lib" / "nvmf").mkdir(parents=True)
        (repo / "lib" / "nvmf" / "ctrlr.c").write_text(
            "int nvmf_ctrlr_connect(void) { return 0; }\n",
            encoding="utf-8",
        )
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-one-line-source", repo_path=str(repo))
        state_file = tmp_path / "one-line-agent-state.txt"
        prompt_log = tmp_path / "one-line-agent-prompts.jsonl"
        agent_script = tmp_path / "one_line_source_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, pathlib, sys",
                    f"state = pathlib.Path({str(state_file)!r})",
                    f"prompt_log = pathlib.Path({str(prompt_log)!r})",
                    "prompt = sys.stdin.read()",
                    "previous = int(state.read_text() or '0') if state.exists() else 0",
                    "prompt_log.write_text((prompt_log.read_text() if prompt_log.exists() else '') + json.dumps({'turn': previous + 1, 'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
                    "state.write_text(str(previous + 1), encoding='utf-8')",
                    "if previous == 0:",
                    "    print('最终答案：已完成源码分析。', flush=True)",
                    "else:",
                    "    print('## 结论\\n已基于 `lib/nvmf/ctrlr.c` 总结 connect 入口。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: `nvmf_ctrlr_connect` 是本轮入口候选。\\n\\n## 行为总结\\n1. 外部连接请求进入 target connect 处理。\\n2. 入口负责校验连接上下文并进入控制器建立路径。', flush=True)",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="One-line source answer repair",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-one-line-source",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="请阅读工作区源码，总结 lib/nvmf/ctrlr.c 里的 connect 入口",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-one-line-source",
                "name": "One-line Source Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        prompts = [json.loads(line) for line in prompt_log.read_text(encoding="utf-8").splitlines()]
        assert [item["turn"] for item in prompts] == [1, 2]
        assert "上一次执行器输出过短" in prompts[1]["prompt"]

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "已完成源码分析。" not in assistant["content"]
        assert "lib/nvmf/ctrlr.c" in assistant["content"]
        assert "nvmf_ctrlr_connect" in assistant["content"]

    async def test_ai_thread_agent_runtime_does_not_repair_generic_generation_wording(self):
        from app.services.ai_conversations import _agent_answer_requires_repair

        assert (
            _agent_answer_requires_repair(
                "DIAGNOSTIC_FOLD_RUN 生成答案，并把思考过程默认折叠",
                "FINAL_DIAGNOSTIC_ANSWER: black-box reconnect timeout should observe RPC error, log, and state recovery",
                [],
            )
            is False
        )

    async def test_ai_thread_agent_runtime_preserves_multiline_user_task_in_prompt(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        (repo / "lib" / "nvmf").mkdir(parents=True)
        (repo / "lib" / "nvmf" / "ctrlr.c").write_text(
            "int nvmf_ctrlr_connect(void) { return 0; }\n",
            encoding="utf-8",
        )
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-multiline-prompt", repo_path=str(repo))
        prompt_log = tmp_path / "multiline-agent-prompts.jsonl"
        agent_script = tmp_path / "multiline_prompt_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, pathlib, sys",
                    f"prompt_log = pathlib.Path({str(prompt_log)!r})",
                    "prompt = sys.stdin.read()",
                    "previous = prompt_log.read_text(encoding='utf-8') if prompt_log.exists() else ''",
                    "prompt_log.write_text(previous + json.dumps({'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
                    "assert 'NVMe-oF target connect 到 IO ready' in prompt",
                    "assert '按代码证据 -> 流程梳理 -> SFMEA -> 黑盒测试用例输出' in prompt",
                    "assert prompt.find('NVMe-oF target connect 到 IO ready') < prompt.find('按代码证据 -> 流程梳理 -> SFMEA -> 黑盒测试用例输出')",
                    "print('## 结论\\n已收到完整多行任务，并基于 `lib/nvmf/ctrlr.c` 输出结果。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: `nvmf_ctrlr_connect`。\\n\\n## 流程梳理\\n1. connect 进入 controller 建立路径。\\n\\n## SFMEA\\n| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |\\n| connect timeout | transport delay | 连接失败 | 8 | 3 | 4 | 96 | 增加超时用例 |\\n\\n## 黑盒测试用例\\n1. 用例：正常连接；前置条件：target 已启动；步骤：发起 connect；预期结果：连接成功；观测点：状态和日志；失败诊断线索：若状态未 ready，检查 listener、subsystem NQN 和 target 日志。\\n2. 用例：连接超时；前置条件：网络延迟；步骤：发起 connect；预期结果：超时失败；观测点：错误码和日志；失败诊断线索：若没有超时错误，检查注入条件、重试参数和日志时间线。', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation
        from app.services import ai_conversations as ai_service

        monkeypatch.setattr(
            ai_service,
            "_requires_strict_test_activity_quality_gate",
            lambda _message: False,
        )

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Multiline task prompt",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-multiline-prompt",
        )
        user_task = (
            "请基于当前 SPDK 工作区源码分析 NVMe-oF target connect 到 IO ready 的主链路。\n"
            "按代码证据 -> 流程梳理 -> SFMEA -> 黑盒测试用例输出；过程默认折叠，完整产物可下载。"
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content=user_task,
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-multiline-prompt",
                "name": "Multiline Prompt Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        prompts = [json.loads(line) for line in prompt_log.read_text(encoding="utf-8").splitlines()]
        assert len(prompts) == 1
        assert "NVMe-oF target connect 到 IO ready" in prompts[0]["prompt"]
        assert "按代码证据 -> 流程梳理 -> SFMEA -> 黑盒测试用例输出" in prompts[0]["prompt"]

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "已收到完整多行任务" in assistant["content"]
        assert "下载完整产物" in assistant["content"]

    async def test_ai_thread_agent_runtime_fails_structured_task_when_repair_stays_incomplete(
        self,
        sqlite_db,
        tmp_path,
    ):
        repo = tmp_path / "spdk"
        (repo / "lib" / "nvmf").mkdir(parents=True)
        (repo / "lib" / "nvmf" / "ctrlr.c").write_text(
            "int nvmf_ctrlr_connect(void) { return 0; }\n",
            encoding="utf-8",
        )
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-soft-warning", repo_path=str(repo))
        agent_script = tmp_path / "soft_warning_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import sys",
                    "sys.stdin.read()",
                    "print('SPDK agent completed analysis', flush=True)",
                    "print('Evidence: lib/nvmf/ctrlr.c nvmf_ctrlr_connect', flush=True)",
                    "print('Flow: connect request -> controller setup -> IO queue ready', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Incomplete structured answer",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-soft-warning",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="分析 SPDK NVMe-oF target connect，并输出代码证据、流程梳理、SFMEA 和黑盒测试用例",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-soft-warning",
                "name": "Soft Warning Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        run = await store.get_run(run_id)
        assert run["status"] == "failed"
        assert "仍未产出可验收" in (run["error"] or "")
        messages = await store.list_messages(conversation["id"])
        assert [item["role"] for item in messages] == ["user"]
        events = await store.list_events_after(conversation["id"])
        diagnostics = "\n".join(
            event["payload"].get("content", "")
            for event in events
            if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
        )
        assert "上一次执行器输出过短" in diagnostics

    async def test_ai_thread_agent_runtime_fails_source_task_when_only_diagnostics_return(
        self,
        sqlite_db,
        tmp_path,
    ):
        repo = tmp_path / "spdk"
        (repo / "lib" / "nvmf").mkdir(parents=True)
        (repo / "lib" / "nvmf" / "ctrlr.c").write_text(
            "int nvmf_ctrlr_connect(void) { return 0; }\n",
            encoding="utf-8",
        )
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-diagnostic-only", repo_path=str(repo))
        prompt_log = tmp_path / "diagnostic_only_prompts.jsonl"
        agent_script = tmp_path / "diagnostic_only_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, pathlib, sys",
                    f"prompt_log = pathlib.Path({str(prompt_log)!r})",
                    "prompt = sys.stdin.read()",
                    "prompt_log.write_text((prompt_log.read_text() if prompt_log.exists() else '') + json.dumps({'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
                    "print('TOOL: rg nvmf_ctrlr_connect lib/nvmf/ctrlr.c', flush=True)",
                    "print('lib/nvmf/ctrlr.c:1:int nvmf_ctrlr_connect(void) { return 0; }', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Diagnostic only source task",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-diagnostic-only",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="请阅读工作区源码，总结 lib/nvmf/ctrlr.c 里的 connect 入口",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-diagnostic-only",
                "name": "Diagnostic Only Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        prompts = [json.loads(line) for line in prompt_log.read_text(encoding="utf-8").splitlines()]
        assert len(prompts) == 2
        assert "上一次执行器输出过短" in prompts[1]["prompt"]

        run = await store.get_run(run_id)
        assert run["status"] == "failed"
        assert "仍未产出可验收" in (run["error"] or "")

        messages = await store.list_messages(conversation["id"])
        assert [item["role"] for item in messages] == ["user"]

        events = await store.list_events_after(conversation["id"])
        diagnostics = "\n".join(
            event["payload"].get("content", "")
            for event in events
            if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
        )
        answer_events = [
            event["payload"].get("content", "")
            for event in events
            if event["event_type"] == "delta" and event["payload"].get("kind") != "diagnostic"
        ]
        assert "rg nvmf_ctrlr_connect" in diagnostics
        assert "执行器没有返回有效内容" not in "\n".join(answer_events)

    async def test_ai_thread_agent_runtime_keeps_agent_file_artifact_body_out_of_visible_answer(
        self,
        sqlite_db,
        tmp_path,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-file-artifact", repo_path=str(repo))
        agent_script = tmp_path / "file_artifact_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import os, pathlib, sys",
                    "sys.stdin.read()",
                    "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
                    "artifact_dir.mkdir(parents=True, exist_ok=True)",
                    "(artifact_dir / 'handoff.md').write_text('# Agent Handoff\\n\\nConcise saved file.\\n', encoding='utf-8')",
                    "print('已生成文件：handoff.md', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="File artifact thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-file-artifact",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="保存一个简短交接文件",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-file-artifact",
                "name": "File Artifact Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "已生成结构化产物" in assistant["content"]
        assert "下载完整产物" in assistant["content"]
        assert "Concise saved file" not in assistant["content"]
        assert "已生成文件：handoff.md" not in assistant["content"]
        assert "SFMEA" not in assistant["content"]
        assert "黑盒用例" not in assistant["content"]
        assert any(action["id"] == "download_run_artifact" for action in assistant["actions"])

        artifact_text = ai_thread_artifact_path(conversation["id"], run_id).read_text(encoding="utf-8")
        assert "# Agent Handoff" in artifact_text
        assert "Concise saved file" in artifact_text
        assert "已生成文件：handoff.md" not in artifact_text

    async def test_ai_thread_claude_partial_messages_do_not_pollute_answer_or_artifact(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-claude-partials", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        agent_script = tmp_path / "claude_partial_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "sys.stdin.read()",
                    "final_text = '## 黑盒测试用例\\n' + '\\n'.join([f'{index}. 前置条件：target 已启动。步骤：执行 iSCSI 登录场景 {index}。预期结果：Login Response 可观测。' for index in range(1, 9)]) + '\\n### TC-02 CHAP 失败\\n预期结果：Login Response 拒绝。\\n'",
                    "events = [",
                    "  {'type':'system','subtype':'init','session_id':'claude-session'},",
                    "  {'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'thinking_delta','thinking':'我先搜索源码'}}},",
                    "  {'type':'assistant','message':{'content':[{'type':'tool_use','name':'Bash','input':{'command':'grep -n \"login\" lib/iscsi/iscsi.c'}}]}},",
                    "  {'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'## 黑盒测试用例\\n### TC-01 正常登录\\n前置条件：target 已启动。\\n'}}},",
                    "  {'type':'message','role':'assistant','content':[{'type':'text','text':final_text}]},",
                    "  {'type':'result','status':'success','session_id':'claude-session'},",
                    "]",
                    "for event in events:",
                    "    print(json.dumps(event, ensure_ascii=False), flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Claude partial thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-claude-partials",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="说明 iSCSI 登录的外部可观测行为",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-claude-partials",
                "name": "Claude Partial Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "stream_json",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        content = assistant["content"]
        assert "## 黑盒测试用例" in content
        assert content.count("## 黑盒测试用例") == 1
        assert "已生成结构化产物" in content
        assert "TC-02 CHAP 失败" in content
        assert "THINKING" not in content
        assert "我先搜索源码" not in content
        assert "tool_use" not in content
        assert "grep -n" not in content

        artifact = ai_thread_artifact_path(conversation["id"], run_id)
        assert artifact.exists()
        artifact_text = artifact.read_text(encoding="utf-8")
        assert "## 黑盒测试用例" in artifact_text
        assert artifact_text.count("## 黑盒测试用例") == 1
        assert "TC-02 CHAP 失败" in artifact_text
        assert "THINKING" not in artifact_text
        assert "grep -n" not in artifact_text

        events = await store.list_events_after(conversation["id"])
        diagnostics = "\n".join(
            event["payload"].get("content", "")
            for event in events
            if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
        )
        assert "我先搜索源码" in diagnostics
        assert "Bash" in diagnostics

    async def test_ai_thread_claude_tool_result_stream_block_is_diagnostic_not_answer(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-claude-tool-result-block", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        agent_script = tmp_path / "claude_tool_result_block_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "sys.stdin.read()",
                    "answer = '## 黑盒测试用例\\n' + ''.join([f'{index}. TC-{index:02d} 正常登录变体：前置条件 target 已启动，步骤执行 iSCSI Login 场景 {index}，预期结果进入 Full Feature Phase 或返回明确 Login Response，观测点为 Login Response、session state 和 target 日志，失败诊断线索为检查 CHAP、InitiatorName 和 target 日志。\\n' for index in range(1, 9)])",
                    "events = [",
                    "  {'type':'system','subtype':'init','session_id':'claude-session'},",
                    "  {'type':'stream_event','event':{'type':'content_block_start','index':0,'content_block':{'type':'tool_result','tool_use_id':'toolu_1'}}},",
                    "  {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'1115:iscsi_conn_login_pdu_success_complete(void *arg)\\n1125:iscsi_conn_login_pdu_success_complete(void *arg)\\nlib/iscsi/iscsi.c:1539:\\tAuthMethod=CHAP\\n'}}},",
                    "  {'type':'stream_event','event':{'type':'content_block_stop','index':0}},",
                    "  {'type':'stream_event','event':{'type':'content_block_start','index':1,'content_block':{'type':'text'}}},",
                    "  {'type':'stream_event','event':{'type':'content_block_delta','index':1,'delta':{'type':'text_delta','text':answer}}},",
                    "  {'type':'stream_event','event':{'type':'content_block_stop','index':1}},",
                    "  {'type':'result','status':'success','session_id':'claude-session'},",
                    "]",
                    "for event in events:",
                    "    print(json.dumps(event, ensure_ascii=False), flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Claude tool result block thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-claude-tool-result-block",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="说明 iSCSI 登录的外部可观测行为",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-claude-tool-result-block",
                "name": "Claude Tool Result Block Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "stream_json",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "## 黑盒测试用例" in assistant["content"]
        assert "已生成结构化产物" in assistant["content"]
        assert "TC-01 正常登录变体" in assistant["content"]
        assert "iscsi_conn_login_pdu_success_complete" not in assistant["content"]
        assert "AuthMethod=CHAP" not in assistant["content"]

        artifact_text = ai_thread_artifact_path(conversation["id"], run_id).read_text(encoding="utf-8")
        assert "## 黑盒测试用例" in artifact_text
        assert "TC-01 正常登录变体" in artifact_text
        assert "iscsi_conn_login_pdu_success_complete" not in artifact_text
        assert "AuthMethod=CHAP" not in artifact_text

        events = await store.list_events_after(conversation["id"])
        answer_events = [
            event["payload"].get("content", "")
            for event in events
            if event["event_type"] == "delta" and event["payload"].get("kind") != "diagnostic"
        ]
        diagnostics = "\n".join(
            event["payload"].get("content", "")
            for event in events
            if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
        )
        assert not any("iscsi_conn_login_pdu_success_complete" in item for item in answer_events)
        assert not any("AuthMethod=CHAP" in item for item in answer_events)
        assert "iscsi_conn_login_pdu_success_complete" in diagnostics
        assert "1125:iscsi_conn_login_pdu_success_complete" in diagnostics
        assert "AuthMethod=CHAP" in diagnostics

    async def test_ai_thread_claude_result_event_can_carry_final_answer_after_tool_use(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-claude-result-final", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        agent_script = tmp_path / "claude_result_final_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "sys.stdin.read()",
                    "answer = '## 黑盒测试用例\\n' + ''.join([f'{index}. TC-{index:02d} 登录场景：前置条件 target 已启动，步骤执行 iSCSI Login 场景 {index}，预期结果可观测。\\n' for index in range(1, 9)])",
                    "events = [",
                    "  {'type':'system','subtype':'init','session_id':'claude-session'},",
                    "  {'type':'assistant','message':{'content':[{'type':'tool_use','name':'Bash','input':{'command':'grep -n \"login\" lib/iscsi/iscsi.c'}}]}},",
                    "  {'type':'stream_event','event':{'type':'content_block_start','index':0,'content_block':{'type':'tool_result','tool_use_id':'toolu_1'}}},",
                    "  {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'1115:iscsi_conn_login_pdu_success_complete(void *arg)\\n'}}},",
                    "  {'type':'stream_event','event':{'type':'content_block_stop','index':0}},",
                    "  {'type':'result','subtype':'success','status':'success','session_id':'claude-session','result':answer},",
                    "]",
                    "for event in events:",
                    "    print(json.dumps(event, ensure_ascii=False), flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Claude result final thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-claude-result-final",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="说明 iSCSI 登录的外部可观测行为",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-claude-result-final",
                "name": "Claude Result Final Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "stream_json",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "## 黑盒测试用例" in assistant["content"]
        assert "已生成结构化产物" in assistant["content"]
        assert "TC-08 登录场景" not in assistant["content"]
        assert "iscsi_conn_login_pdu_success_complete" not in assistant["content"]
        assert "grep -n" not in assistant["content"]

        artifact_text = ai_thread_artifact_path(conversation["id"], run_id).read_text(encoding="utf-8")
        assert "## 黑盒测试用例" in artifact_text
        assert "TC-08 登录场景" in artifact_text
        assert "iscsi_conn_login_pdu_success_complete" not in artifact_text
        assert "grep -n" not in artifact_text

        events = await store.list_events_after(conversation["id"])
        diagnostics = "\n".join(
            event["payload"].get("content", "")
            for event in events
            if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
        )
        assert "Bash" in diagnostics
        assert "iscsi_conn_login_pdu_success_complete" in diagnostics

    async def test_ai_thread_claude_assistant_message_text_replaces_partial_answer(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-claude-assistant-final", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        agent_script = tmp_path / "claude_assistant_final_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "sys.stdin.read()",
                    "final_text = '## 黑盒测试用例\\n' + ''.join([f'{index}. TC-{index:02d} Login：前置条件 target 已启动，预期结果可观测。\\n' for index in range(1, 9)])",
                    "events = [",
                    "  {'type':'system','subtype':'init','session_id':'claude-session'},",
                    "  {'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'## 黑盒测试用例\\n### partial 应被最终 assistant 替换\\n'}}},",
                    "  {'type':'assistant','message':{'role':'assistant','content':[{'type':'text','text':final_text}]}},",
                    "  {'type':'result','status':'success','session_id':'claude-session'},",
                    "]",
                    "for event in events:",
                    "    print(json.dumps(event, ensure_ascii=False), flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Claude assistant final thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-claude-assistant-final",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="说明 iSCSI 登录的外部可观测行为",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-claude-assistant-final",
                "name": "Claude Assistant Final Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "stream_json",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert assistant["content"].count("## 黑盒测试用例") == 1
        assert "已生成结构化产物" in assistant["content"]
        assert "TC-08 Login" not in assistant["content"]
        assert "partial 应被最终 assistant 替换" not in assistant["content"]

        artifact_text = ai_thread_artifact_path(conversation["id"], run_id).read_text(encoding="utf-8")
        assert artifact_text.count("## 黑盒测试用例") == 1
        assert "TC-08 Login" in artifact_text
        assert "partial 应被最终 assistant 替换" not in artifact_text

    async def test_ai_thread_agent_runtime_adopts_markdown_file_from_agent_artifact_dir(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-artifact-file", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        agent_script = tmp_path / "artifact_file_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import os, pathlib, sys",
                    "sys.stdin.read()",
                    "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
                    "artifact_dir.mkdir(parents=True, exist_ok=True)",
                    "report = '# Agent 生成报告\\n\\n## 黑盒测试用例\\n' + ''.join([f'{index}. TC-{index:02d}：前置条件 target 已启动。步骤执行 SPDK 登录场景。预期结果可观测。\\n' for index in range(1, 9)])",
                    "(artifact_dir / 'spdk-blackbox.md').write_text(report, encoding='utf-8')",
                    "print('已生成文件：spdk-blackbox.md', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation
        from app.services import ai_conversations as ai_service

        monkeypatch.setattr(
            ai_service,
            "_requires_strict_test_activity_quality_gate",
            lambda _message: False,
        )

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Agent artifact file thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-artifact-file",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="生成完整黑盒测试用例并保存为文件",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-artifact-file",
                "name": "Artifact File Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "已生成结构化产物" in assistant["content"]
        assert "下载完整产物" in assistant["content"]
        assert "TC-08" not in assistant["content"]
        assert any(action["id"] == "download_run_artifact" for action in assistant["actions"])

        artifact_text = ai_thread_artifact_path(conversation["id"], run_id).read_text(encoding="utf-8")
        assert "## 黑盒测试用例" in artifact_text
        assert "TC-08" in artifact_text
        assert "已生成文件：spdk-blackbox.md" not in artifact_text

    async def test_ai_thread_agent_runtime_rejects_shallow_adopted_artifact(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-artifact-before-repair", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        prompt_log = tmp_path / "artifact_before_repair_prompts.jsonl"
        agent_script = tmp_path / "artifact_before_repair_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, os, pathlib, sys",
                    f"prompt_log = pathlib.Path({str(prompt_log)!r})",
                    "prompt = sys.stdin.read()",
                    "previous = prompt_log.read_text(encoding='utf-8') if prompt_log.exists() else ''",
                    "prompt_log.write_text(previous + json.dumps({'prompt': prompt}, ensure_ascii=False) + '\\n', encoding='utf-8')",
                    "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
                    "assert artifact_dir.is_absolute(), artifact_dir",
                    "artifact_dir.mkdir(parents=True, exist_ok=True)",
                    "report = '# SPDK 黑盒测试设计\\n\\n## 黑盒测试用例\\n' + ''.join([f'TC-{index:02d}: 外部可观测路径，执行连接并检查日志状态。\\n' for index in range(1, 9)])",
                    "(artifact_dir / 'spdk-blackbox.md').write_text(report, encoding='utf-8')",
                    "print('已生成文件：spdk-blackbox.md', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Artifact before repair thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-artifact-before-repair",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content=(
                "请基于当前 SPDK 源码输出完整的代码分析、流程梳理、SFMEA 和黑盒测试用例，"
                "并保存为文件。"
            ),
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-artifact-before-repair",
                "name": "Artifact Before Repair Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        prompts = [json.loads(line) for line in prompt_log.read_text(encoding="utf-8").splitlines()]
        assert len(prompts) == 1

        run = await store.get_run(run_id)
        assert run["status"] == "failed"
        assert "质量门禁" in run["error"]
        messages = await store.list_messages(conversation["id"])
        assert [item["role"] for item in messages] == ["user", "assistant"]
        assert messages[-1]["actions"][0]["id"] == "test_activity_task_card"
        assert ai_thread_artifact_path(conversation["id"], run_id).exists() is False

    async def test_ai_thread_agent_runtime_downloads_complete_inline_test_design(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-inline-complete-artifact", repo_path=str(repo))
        agent_script = tmp_path / "inline_complete_design_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import sys",
                    "sys.stdin.read()",
                    "print('## 结论\\n已完成 SPDK connect 完整测试设计。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: `nvmf_ctrlr_connect`。\\n- `test/nvmf`: 可承载连接测试。\\n\\n## 流程梳理\\n1. initiator 发起连接。\\n2. target 建立 controller。\\n\\n## SFMEA\\n| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |\\n| connect timeout | 网络抖动 | 连接失败 | 8 | 3 | 4 | 96 | 增加超时与重试观测 |\\n\\n## 黑盒测试用例\\n1. 用例：正常连接；前置条件：target 已启动；步骤：发起连接；预期结果：连接成功；观测点：日志和状态；失败诊断线索：若状态未 ready，检查 listener、NQN 和 target 日志。\\n2. 用例：连接超时；前置条件：注入网络延迟；步骤：发起连接；预期结果：超时失败且可重试；观测点：错误码和日志；失败诊断线索：若未触发超时，检查延迟注入、重试参数和日志时间线。', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation
        from app.services import ai_conversations as ai_service

        monkeypatch.setattr(
            ai_service,
            "_requires_strict_test_activity_quality_gate",
            lambda _message: False,
        )

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Inline complete test design",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-inline-complete-design",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="请输出完整的代码分析、流程梳理、SFMEA 和黑盒测试用例",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-inline-complete-design",
                "name": "Inline Complete Design Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "已生成结构化产物" in assistant["content"]
        assert "下载完整产物" in assistant["content"]
        assert "connect timeout" not in assistant["content"]
        assert any(action["id"] == "download_run_artifact" for action in assistant["actions"])

        artifact_text = ai_thread_artifact_path(conversation["id"], run_id).read_text(encoding="utf-8")
        assert "## SFMEA" in artifact_text
        assert "connect timeout" in artifact_text
        assert "## 黑盒测试用例" in artifact_text
        assert "用例：连接超时" in artifact_text

    async def test_ai_thread_agent_runtime_rejects_shallow_four_piece_test_design_without_complete_word(
        self,
        sqlite_db,
        tmp_path,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-inline-four-piece-artifact", repo_path=str(repo))
        agent_script = tmp_path / "inline_four_piece_design_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import sys",
                    "sys.stdin.read()",
                    "print('## 结论\\n已完成 SPDK connect 测试设计。\\n\\n## 代码证据\\n- `lib/nvmf/ctrlr.c`: `nvmf_ctrlr_connect`。\\n- `test/nvmf`: 可承载连接测试。\\n\\n## 流程梳理\\n1. initiator 发起连接。\\n2. target 建立 controller。\\n\\n## SFMEA\\n| failure mode | cause | effect | severity | occurrence | detection | RPN | mitigation |\\n| connect timeout | 网络抖动 | 连接失败 | 8 | 3 | 4 | 96 | 增加超时与重试观测 |\\n\\n## 黑盒测试用例\\n1. 用例：正常连接；前置条件：target 已启动；步骤：发起连接；预期结果：连接成功；观测点：日志和状态。\\n2. 用例：连接超时；前置条件：注入网络延迟；步骤：发起连接；预期结果：超时失败且可重试；观测点：错误码和日志。', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Inline four piece test design",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-inline-four-piece-design",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="请输出代码分析、流程梳理、SFMEA 和黑盒测试用例",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-inline-four-piece-design",
                "name": "Inline Four Piece Design Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        run = await store.get_run(run_id)
        assert [item["role"] for item in messages] == ["user", "assistant"]
        assert messages[-1]["actions"][0]["id"] == "test_activity_task_card"
        assert run["status"] == "failed"
        assert "质量门禁" in run["error"]
        assert not ai_thread_artifact_path(conversation["id"], run_id).exists()

    async def test_ai_thread_agent_runtime_always_downloads_adopted_short_artifact(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-short-artifact", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        agent_script = tmp_path / "short_artifact_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import os, pathlib, sys",
                    "sys.stdin.read()",
                    "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
                    "artifact_dir.mkdir(parents=True, exist_ok=True)",
                    "(artifact_dir / 'handoff.md').write_text('# Agent Handoff\\n\\nConcise saved file.\\n', encoding='utf-8')",
                    "print('已生成文件：handoff.md', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Short artifact thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-short-artifact",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="保存一个简短交接文件",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-short-artifact",
                "name": "Short Artifact Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "已生成结构化产物" in assistant["content"]
        assert "Concise saved file" not in assistant["content"]
        assert any(action["id"] == "download_run_artifact" for action in assistant["actions"])

        artifact_text = ai_thread_artifact_path(conversation["id"], run_id).read_text(encoding="utf-8")
        assert "# Agent Handoff" in artifact_text
        assert "Concise saved file" in artifact_text
        assert "已生成文件：handoff.md" not in artifact_text

    async def test_ai_thread_agent_runtime_merges_multiple_markdown_artifacts(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-multi-artifact", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        agent_script = tmp_path / "multi_artifact_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import os, pathlib, sys",
                    "sys.stdin.read()",
                    "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
                    "artifact_dir.mkdir(parents=True, exist_ok=True)",
                    "(artifact_dir / 'flow.md').write_text('# 流程梳理\\n\\n| Profile | Score |\\n|---|---:|\\n| P1 | 100 |\\n\\nFLOW_ARTIFACT_ONLY\\n', encoding='utf-8')",
                    "(artifact_dir / 'sfmea.md').write_text('# SFMEA\\n\\nSFMEA_ARTIFACT_ONLY\\n', encoding='utf-8')",
                    "print('已生成文件：flow.md sfmea.md', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Multi artifact thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-multi-artifact",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="保存两个 Markdown 分析文件并合并为下载结果",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-multi-artifact",
                "name": "Multi Artifact Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "已生成结构化产物" in assistant["content"]
        assert "FLOW_ARTIFACT_ONLY" not in assistant["content"]
        assert "SFMEA_ARTIFACT_ONLY" not in assistant["content"]
        assert any(action["id"] == "download_run_artifact" for action in assistant["actions"])

        artifact_text = ai_thread_artifact_path(conversation["id"], run_id).read_text(encoding="utf-8")
        assert "FLOW_ARTIFACT_ONLY" in artifact_text
        assert "SFMEA_ARTIFACT_ONLY" in artifact_text
        assert "|---|---:|" in artifact_text
        assert "flow.md" in artifact_text
        assert "sfmea.md" in artifact_text
        assert "已生成文件：flow.md sfmea.md" not in artifact_text

    async def test_ai_thread_agent_runtime_redacts_secrets_from_adopted_artifact(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-redacted-artifact", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        agent_script = tmp_path / "leaky_artifact_agent.py"
        leaked_key = "sk-agentArtifactLeakSecret1234567890"
        leaked_token = "artifactTokenLeak12345"
        agent_script.write_text(
            "\n".join(
                [
                    "import os, pathlib, sys",
                    "sys.stdin.read()",
                    "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
                    "artifact_dir.mkdir(parents=True, exist_ok=True)",
                    (
                        "(artifact_dir / 'leaky.md').write_text("
                        "'# Agent Report\\n\\nSAFE_ARTIFACT_BODY\\n\\n"
                        f"api_key={leaked_key}\\n"
                        f"token={leaked_token}\\n', "
                        "encoding='utf-8')"
                    ),
                    "print('已生成文件：leaky.md', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Leaky artifact thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-leaky-artifact",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="保存一个报告文件，文件里不能泄露密钥",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-leaky-artifact",
                "name": "Leaky Artifact Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        artifact_text = ai_thread_artifact_path(conversation["id"], run_id).read_text(encoding="utf-8")
        assert "SAFE_ARTIFACT_BODY" in artifact_text
        assert leaked_key not in artifact_text
        assert leaked_token not in artifact_text
        assert "<redacted>" in artifact_text

    async def test_ai_thread_agent_runtime_adopts_json_artifact_without_markdown_only_copy(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-json-artifact", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        agent_script = tmp_path / "json_artifact_agent.py"
        leaked_key = "sk-jsonArtifactLeakSecret1234567890"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, os, pathlib, sys",
                    "sys.stdin.read()",
                    "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
                    "artifact_dir.mkdir(parents=True, exist_ok=True)",
                    "payload = {",
                    "    'sfmea': [{'failure_mode': 'connect timeout', 'rpn': 216}],",
                    "    'black_box_cases': [{'id': 'TC-NVMF-JSON-01', 'expected': 'observable timeout'}],",
                    f"    'api_key': '{leaked_key}',",
                    "}",
                    "(artifact_dir / 'sfmea_cases.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')",
                    "print('已生成文件：sfmea_cases.json', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="JSON artifact thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-json-artifact",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="保存 JSON 分析结果文件并在下载前脱敏",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-json-artifact",
                "name": "JSON Artifact Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "已生成结构化产物" in assistant["content"]
        assert "完整 Markdown" not in assistant["content"]
        assert "TC-NVMF-JSON-01" not in assistant["content"]
        assert any(action["id"] == "download_run_artifact" for action in assistant["actions"])

        artifact_text = ai_thread_artifact_path(conversation["id"], run_id).read_text(encoding="utf-8")
        assert '"sfmea": [' in artifact_text
        assert '"black_box_cases": [' in artifact_text
        assert "TC-NVMF-JSON-01" in artifact_text
        assert leaked_key not in artifact_text
        assert "<redacted>" in artifact_text

    async def test_ai_thread_agent_runtime_ignores_audit_artifacts_from_download_package(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-audit-artifacts", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        agent_script = tmp_path / "audit_artifact_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import os, pathlib, sys",
                    "sys.stdin.read()",
                    "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
                    "artifact_dir.mkdir(parents=True, exist_ok=True)",
                    "(artifact_dir / 'report.md').write_text('# 用户结果\\n\\nVISIBLE_REPORT_RESULT\\n', encoding='utf-8')",
                    "(artifact_dir / 'raw_output.jsonl').write_text('{\"event\":\"RAW_AGENT_TRACE_SHOULD_NOT_DOWNLOAD\"}\\n', encoding='utf-8')",
                    "(artifact_dir / 'diagnostics.txt').write_text('DIAGNOSTIC_TRACE_SHOULD_NOT_DOWNLOAD\\n', encoding='utf-8')",
                    "print('已生成文件：report.md raw_output.jsonl diagnostics.txt', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, ai_thread_artifact_path, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Audit artifact thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-audit-artifact",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="保存最终报告，同时保留内部执行日志",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-audit-artifact",
                "name": "Audit Artifact Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        artifact_text = ai_thread_artifact_path(conversation["id"], run_id).read_text(encoding="utf-8")
        assert "VISIBLE_REPORT_RESULT" in artifact_text
        assert "RAW_AGENT_TRACE_SHOULD_NOT_DOWNLOAD" not in artifact_text
        assert "DIAGNOSTIC_TRACE_SHOULD_NOT_DOWNLOAD" not in artifact_text
        assert "raw_output.jsonl" not in artifact_text
        assert "diagnostics.txt" not in artifact_text

    async def test_ai_thread_agent_runtime_cancel_terminates_blocked_process_promptly(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-cancel-kill", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        marker = tmp_path / "agent-was-not-killed.txt"
        agent_script = tmp_path / "blocked_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import pathlib, sys, time",
                    "sys.stdin.read()",
                    "print('cancel-kill-first-delta', flush=True)",
                    "time.sleep(3)",
                    f"pathlib.Path({str(marker)!r}).write_text('process survived cancellation', encoding='utf-8')",
                    "print('cancel-kill-after-sleep', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Cancel kill thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-cancel-kill",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="启动一个会被取消的 agent",
            references=[],
        )
        run_id = created["run"]["id"]

        task = asyncio.create_task(
            run_agent_generation(
                store=store,
                run_id=run_id,
                runtime={
                    "id": "runtime-cancel-kill",
                    "name": "Cancel Kill Agent",
                    "command": sys.executable,
                    "args": [str(agent_script)],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
        )
        try:
            for _ in range(40):
                events = await store.list_events_after(conversation["id"])
                if any("cancel-kill-first-delta" in event["payload"].get("content", "") for event in events):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("agent runtime did not emit its first delta")

            cancelled = await store.cancel_run(conversation["id"])
            assert cancelled and cancelled["status"] == "cancelled"

            await asyncio.wait_for(task, timeout=0.8)
            await asyncio.sleep(0.2)

            latest = await store.latest_run(conversation["id"])
            messages = await store.list_messages(conversation["id"])
            events = await store.list_events_after(conversation["id"])
            assert latest and latest["status"] == "cancelled"
            assert [item["role"] for item in messages] == ["user"]
            assert any(
                event["event_type"] == "delta"
                and event["payload"].get("kind") == "diagnostic"
                and "用户已停止本轮 Agent" in event["payload"].get("content", "")
                for event in events
            )
            assert not marker.exists()
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    async def test_ai_thread_agent_runtime_cancel_terminates_child_processes(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-cancel-child", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        child_marker = tmp_path / "agent-child-survived-cancel.txt"
        child_script = tmp_path / "child_agent_worker.py"
        child_script.write_text(
            "\n".join(
                [
                    "import pathlib, sys, time",
                    "time.sleep(1.5)",
                    "pathlib.Path(sys.argv[1]).write_text('child survived cancellation', encoding='utf-8')",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        agent_script = tmp_path / "parent_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import subprocess, sys, time",
                    "sys.stdin.read()",
                    f"subprocess.Popen([sys.executable, {str(child_script)!r}, {str(child_marker)!r}])",
                    "print('cancel-child-first-delta', flush=True)",
                    "time.sleep(5)",
                    "print('cancel-child-after-sleep', flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Cancel child thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-cancel-child",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="启动一个会再拉子进程的 agent",
            references=[],
        )
        run_id = created["run"]["id"]

        task = asyncio.create_task(
            run_agent_generation(
                store=store,
                run_id=run_id,
                runtime={
                    "id": "runtime-cancel-child",
                    "name": "Cancel Child Agent",
                    "command": sys.executable,
                    "args": [str(agent_script)],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "working_dir_mode": "project",
                    "timeout_seconds": 10,
                },
            )
        )
        try:
            for _ in range(40):
                events = await store.list_events_after(conversation["id"])
                if any("cancel-child-first-delta" in event["payload"].get("content", "") for event in events):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("agent runtime did not emit its first child-process delta")

            cancelled = await store.cancel_run(conversation["id"])
            assert cancelled and cancelled["status"] == "cancelled"

            await asyncio.wait_for(task, timeout=0.8)
            await asyncio.sleep(2)

            latest = await store.latest_run(conversation["id"])
            messages = await store.list_messages(conversation["id"])
            assert latest and latest["status"] == "cancelled"
            assert [item["role"] for item in messages] == ["user"]
            assert not child_marker.exists()
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    async def test_agent_runtime_output_parser_cleans_terminal_noise_and_unwraps_json(self):
        from app.services.agent_cli_bridge import (
            AGENT_ANSWER_DELTA_PREFIX,
            AGENT_FINAL_ANSWER_PREFIX,
            _decode,
            _parse_event_text,
        )

        assert _parse_event_text("\x1b[32m正文片段\x1b[0m\r\n", "plain") == "正文片段"
        assert _parse_event_text("\r\x1b[2K⠋ 12\r\x1b[2K⠙ 47\r\x1b[2K最终答案\n", "plain") == "最终答案"
        assert _parse_event_text("\x1b(B最终答案：字符集切换噪声已清理\n", "plain") == "最终答案：字符集切换噪声已清理"
        assert _parse_event_text("\x1bP1;2;3+q54321\x1b\\最终答案：DCS 噪声已清理\n", "plain") == "最终答案：DCS 噪声已清理"
        assert _parse_event_text("\x1b^nga-progress-418\x1b\\最终答案：PM 噪声已清理\n", "plain") == "最终答案：PM 噪声已清理"
        assert _parse_event_text("\x1b_nga-apc-9527\x1b\\最终答案：APC 噪声已清理\n", "plain") == "最终答案：APC 噪声已清理"
        assert _parse_event_text("\x9d8;;file:///tmp/nga-2468\x07最终答案：8-bit OSC 噪声已清理\n", "plain") == "最终答案：8-bit OSC 噪声已清理"
        assert _parse_event_text("1\n2\n47%\n12/100\n最终答案\n", "plain") == "最终答案"
        assert _parse_event_text("■■■■⬝⬝⬝⬝■■■■■⬝⬝⬝兼容\n", "plain") == "兼容"
        assert _decode("源码证据：连接失败".encode("gbk")) == "源码证据：连接失败"
        assert (
            _parse_event_text(
                json.dumps({"choices": [{"delta": {"content": "源码证据"}}]}, ensure_ascii=False),
                "stream_json",
            )
            == f"{AGENT_ANSWER_DELTA_PREFIX}源码证据"
        )
        assert (
            _parse_event_text(
                f"data: {json.dumps({'choices': [{'delta': {'content': 'SSE 源码证据'}}]}, ensure_ascii=False)}\n",
                "stream_json",
            )
            == f"{AGENT_ANSWER_DELTA_PREFIX}SSE 源码证据"
        )
        assert (
            _parse_event_text(
                f"event: message\ndata: {json.dumps({'content': 'SSE event 源码证据'}, ensure_ascii=False)}\n\n",
                "stream_json",
            )
            == "SSE event 源码证据"
        )
        assert _parse_event_text("data: [DONE]\n", "stream_json") == ""
        assert (
            _parse_event_text(
                json.dumps({"content": [{"type": "text", "text": "材料证据"}]}, ensure_ascii=False),
                "stream_json",
            )
            == "材料证据"
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "最终答案：Responses API 最终正文。"}],
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            == f"{AGENT_FINAL_ANSWER_PREFIX}最终答案：Responses API 最终正文。"
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": "最终答案：completed 事件里的完整正文。",
                                        }
                                    ],
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            == f"{AGENT_FINAL_ANSWER_PREFIX}最终答案：completed 事件里的完整正文。"
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "response.output_text.done",
                        "text": "最终答案：output_text.done 完整正文。",
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            == f"{AGENT_FINAL_ANSWER_PREFIX}最终答案：output_text.done 完整正文。"
        )
        stream_state: dict[int, str] = {}
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "stream_event",
                        "event": {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "tool_result"},
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
                stream_state=stream_state,
            )
            == ""
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "stream_event",
                        "event": {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {
                                "type": "text_delta",
                                "text": "1115:iscsi_conn_login_pdu_success_complete(void *arg)\n1125:iscsi_conn_login_pdu_success_complete(void *arg)\n",
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
                stream_state=stream_state,
            )
            == "TOOL: 1115:iscsi_conn_login_pdu_success_complete(void *arg)\n"
            "TOOL: 1125:iscsi_conn_login_pdu_success_complete(void *arg)\n"
        )
        assert (
            _parse_event_text(
                json.dumps({"event": "message", "data": {"content": "NGA 包装正文：源码证据"}}, ensure_ascii=False),
                "stream_json",
            )
            == "NGA 包装正文：源码证据"
        )
        assert (
            _parse_event_text(
                json.dumps({"event": "reasoning", "payload": {"text": "内部推理：先搜索源码"}}, ensure_ascii=False),
                "stream_json",
            )
            == "THINKING: 内部推理：先搜索源码"
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Claude 源码证据"}},
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            == "Claude 源码证据"
        )
        assert (
            _parse_event_text(
                json.dumps({"type": "tool_use", "message": "正在调用 rg 搜索源码"}, ensure_ascii=False),
                "stream_json",
            )
            == "TOOL: 正在调用 rg 搜索源码"
        )
        assert (
            _parse_event_text(
                json.dumps({"type": "log", "message": "正在读取 lib/nvmf/ctrlr.c，已处理 12/100"}, ensure_ascii=False),
                "stream_json",
            )
            == "STATUS: 正在读取 lib/nvmf/ctrlr.c，已处理 12/100"
        )
        assert (
            _parse_event_text(
                json.dumps({"event": "progress", "data": {"message": "扫描 lib/bdev，命中 47 条候选"}}, ensure_ascii=False),
                "stream_json",
            )
            == "STATUS: 扫描 lib/bdev，命中 47 条候选"
        )
        assert (
            _parse_event_text(
                json.dumps({"kind": "warning", "message": "工具返回了非关键告警"}, ensure_ascii=False),
                "stream_json",
            )
            == "STATUS: 工具返回了非关键告警"
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "tool_use",
                        "sessionID": "opencode-session-1",
                        "part": {
                            "type": "tool_use",
                            "tool": "grep",
                            "state": {"input": {"pattern": "spdk_nvmf", "path": "lib/nvmf"}},
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            == 'TOOL: grep {"pattern": "spdk_nvmf", "path": "lib/nvmf"}'
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "error",
                        "error": {
                            "name": "OpenCodeToolError",
                            "data": {"message": "opencode grep failed while reading lib/nvmf"},
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            == "ERROR: opencode grep failed while reading lib/nvmf"
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "item.updated",
                        "item": {
                            "type": "todo_list",
                            "todo_items": [
                                {"id": "read", "content": "读取 lib/nvmf 源码", "status": "completed"},
                                {"id": "sfmea", "content": "生成 SFMEA", "status": "in_progress"},
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            == "STATUS: task_progress read=completed: 读取 lib/nvmf 源码; sfmea=in_progress: 生成 SFMEA"
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "item.started",
                        "item": {
                            "type": "mcp_tool_call",
                            "server": "gitnexus",
                            "tool": "search",
                            "arguments": {"query": "spdk_nvmf_connect"},
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            == 'TOOL: mcp:gitnexus/search {"query": "spdk_nvmf_connect"}'
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "command": "rg spdk_nvmf_connect lib/nvmf",
                            "status": "completed",
                            "exit_code": 0,
                            "aggregated_output": "lib/nvmf/ctrlr.c: spdk_nvmf_connect",
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            == "TOOL: command: rg spdk_nvmf_connect lib/nvmf\n"
            "TOOL: status: completed\n"
            "TOOL: exit_code: 0\n"
            "TOOL: lib/nvmf/ctrlr.c: spdk_nvmf_connect"
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "item.updated",
                        "item": {
                            "type": "command_execution",
                            "command": "rg spdk_nvmf_connect lib/nvmf",
                            "status": "in_progress",
                            "aggregated_output": "\n".join(
                                f"lib/nvmf/ctrlr.c:{line}: spdk_nvmf_connect"
                                for line in range(200)
                            ),
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            is None
        )
        compacted = _parse_event_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "rg spdk_nvmf_connect lib/nvmf",
                        "status": "completed",
                        "exit_code": 0,
                        "aggregated_output": "\n".join(f"source line {line}" for line in range(200)),
                    },
                },
                ensure_ascii=False,
            ),
            "stream_json",
        )
        assert compacted is not None
        assert "TOOL: source line 0" in compacted
        assert "TOOL: ... 194 lines omitted ..." in compacted
        assert "TOOL: source line 199" in compacted
        assert "source line 100" not in compacted
        assert _parse_event_text(json.dumps({"type": "message_start", "index": 0}), "stream_json") == ""
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "stream_event",
                        "event": {
                            "type": "content_block_delta",
                            "delta": {"type": "thinking_delta", "thinking": "先搜索源码"},
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            == "THINKING: 先搜索源码"
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "stream_event",
                        "event": {
                            "type": "content_block_delta",
                            "delta": {"type": "text_delta", "text": "Claude 正文"},
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            == "Claude 正文"
        )
        assert (
            _parse_event_text(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "tool_use", "name": "Read", "input": {"file": "lib/nvmf/connect.c"}}
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
                "stream_json",
            )
            == 'TOOL: Read {"file": "lib/nvmf/connect.c"}'
        )

    async def test_ai_thread_agent_runtime_folds_log_progress_events_out_of_answer(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-log-progress", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        agent_script = tmp_path / "log_progress_agent.py"
        final_answer = (
            "## 结论\n"
            "FINAL_LOG_EVENT_ANSWER 已基于源码完成 connect 黑盒分析。\n\n"
            "## 代码证据\n"
            "- `lib/nvmf/ctrlr.c`: connect 路径证据。\n"
            "- `test/nvmf`: 可承载黑盒连接回归。\n\n"
            "## 黑盒测试用例\n"
            "- 用例：正常连接；前置条件：target 已启动；步骤：发起 connect；"
            "预期结果：连接成功；观测点：RPC 状态、日志和连接状态。"
        )
        agent_script.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "sys.stdin.read()",
                    "events = [",
                    "  {'type': 'log', 'message': '正在读取 lib/nvmf/ctrlr.c，已处理 12/100'},",
                    "  {'event': 'progress', 'data': {'message': '扫描 lib/bdev，命中 47 条候选'}},",
                    "  {'kind': 'warning', 'message': '工具返回了非关键告警'},",
                    "  {'type': 'debug', 'message': \"argv=['nga','run','12345']\"},",
                    f"  {{'type': 'result', 'status': 'success', 'result': {final_answer!r}}},",
                    "]",
                    "for event in events:",
                    "    print(json.dumps(event, ensure_ascii=False), flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Log progress folding thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-log-progress",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="分析 SPDK connect 的源码行为与可观测结果",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-log-progress",
                "name": "Log Progress Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "stream_json",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "FINAL_LOG_EVENT_ANSWER" in assistant["content"]
        assert "正在读取 lib/nvmf/ctrlr.c" not in assistant["content"]
        assert "扫描 lib/bdev" not in assistant["content"]
        assert "工具返回了非关键告警" not in assistant["content"]
        assert "nga" not in assistant["content"]
        assert "12345" not in assistant["content"]

        events = await store.list_events_after(conversation["id"])
        diagnostics = "\n".join(
            event["payload"].get("content", "")
            for event in events
            if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
        )
        assert "正在读取 lib/nvmf/ctrlr.c" in diagnostics
        assert "扫描 lib/bdev" in diagnostics
        assert "工具返回了非关键告警" in diagnostics

    async def test_ai_thread_agent_runtime_folds_split_thinking_and_source_dump_out_of_answer(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-split-thinking", repo_path=str(repo))
        monkeypatch.chdir(tmp_path)
        agent_script = tmp_path / "split_thinking_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "sys.stdin.read()",
                    "events = [",
                    "  {'content': 'THINKING: '},",
                    "  {'content': '我先核对工作区 iSCSI 登录相关源码，再'},",
                    "  {'content': '据此设计黑盒用例。'},",
                    "  {'content': 'Bash {\"command\": \"grep -n login lib/iscsi/iscsi.c | head -60\"}'},",
                    "  {'content': '1125:iscsi_conn_login_pdu_success_complete(void *arg)\\n'},",
                    "  {'content': 'lib/iscsi/iscsi.c:1539:\\t\\trc = iscsi_op_login_update_param(conn, \"AuthMethod\", \"CHAP\", \"CHAP\");\\n'},",
                    "  {'content': '## 黑盒测试用例\\n'},",
                    "  {'content': '### TC-01 正常登录\\n'},",
                    "  {'content': '前置条件：target 已启动；步骤：initiator 发起 login；预期结果：进入 Full Feature Phase；观测点：Login Response、session state 和 target 日志；失败诊断线索：若登录失败，检查 CHAP、InitiatorName 和 target 日志。\\n'},",
                    "  {'content': '### TC-02 CHAP 失败\\n'},",
                    "  {'content': '前置条件：target 开启 CHAP；步骤：使用错误 secret 发起 login；预期结果：Login Response 拒绝；观测点：错误码、session state 和 target 日志；失败诊断线索：若未拒绝，检查认证配置是否生效。\\n'},",
                    "]",
                    "for event in events:",
                    "    print(json.dumps(event, ensure_ascii=False), flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Split thinking folding thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-split-thinking",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="说明 iSCSI 登录的外部可观测行为",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-split-thinking",
                "name": "Split Thinking Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "stdin",
                "output_mode": "stream_json",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "## 黑盒测试用例" in assistant["content"]
        assert "TC-01 正常登录" in assistant["content"]
        assert "THINKING" not in assistant["content"]
        assert "我先核对工作区" not in assistant["content"]
        assert "Bash" not in assistant["content"]
        assert "iscsi_conn_login_pdu_success_complete" not in assistant["content"]
        assert "AuthMethod" not in assistant["content"]

        events = await store.list_events_after(conversation["id"])
        diagnostics = "\n".join(
            event["payload"].get("content", "")
            for event in events
            if event["event_type"] == "delta" and event["payload"].get("kind") == "diagnostic"
        )
        assert "我先核对工作区 iSCSI 登录相关源码" in diagnostics
        assert "Bash" in diagnostics
        assert "iscsi_conn_login_pdu_success_complete" in diagnostics
        assert "AuthMethod" in diagnostics

    async def test_agent_runtime_stream_decodes_gbk_stdout(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stdout.buffer.write('源码证据：连接失败'.encode('gbk')); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        assert "".join(chunks) == "源码证据：连接失败"

    async def test_agent_runtime_stream_cleans_isolated_fallback_artifact_dir(self, tmp_path):
        from app.services.agent_cli_bridge import stream_agent_runtime

        cwd = tmp_path / "agent-cwd"
        cwd.mkdir()
        agent_code = (
            "import json, os, pathlib, sys; "
            "artifact_dir=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR']); "
            "artifact_dir.mkdir(parents=True, exist_ok=True); "
            "(artifact_dir/'result.json').write_text(json.dumps({'ok': True}), encoding='utf-8'); "
            "print(str(artifact_dir)); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="write artifact",
            cwd=str(cwd),
        ):
            chunks.append(chunk)

        artifact_dir = "".join(chunks).strip()
        assert artifact_dir
        assert (tmp_path / "agent-cwd" / "result.json").exists() is False
        assert pathlib.Path(artifact_dir).name.startswith("codetalk-agent-runtime-")
        assert pathlib.Path(artifact_dir).exists() is False

    async def test_agent_runtime_exposes_full_multiline_prompt_file(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        prompt = "第一行任务\n第二行必须保留\n第三行包含 SFMEA 和黑盒测试"
        agent_code = (
            "import os, pathlib, sys; "
            "path=pathlib.Path(os.environ['CODETALK_AGENT_PROMPT_FILE']); "
            "sys.stdout.write(path.read_text(encoding='utf-8')); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "argv_last",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt=prompt,
            cwd=None,
        ):
            chunks.append(chunk)

        assert "".join(chunks) == prompt

    async def test_managed_agent_transports_preserve_full_multiline_prompt_argument_and_file(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        prompt = (
            "第一行：分析 SPDK iSCSI login\n"
            "第二行：输出流程梳理\n"
            "第三行：生成 SFMEA 和黑盒测试用例"
        )
        agent_code = (
            "import json, os, pathlib, sys; "
            "prompt_file=pathlib.Path(os.environ['CODETALK_AGENT_PROMPT_FILE']).read_text(encoding='utf-8'); "
            "stdin=sys.stdin.read(); "
            "print(json.dumps({'argv': sys.argv[1:], 'prompt_file': prompt_file, 'stdin': stdin}, ensure_ascii=False), flush=True)"
        )
        cases = [
            ("stdin", lambda argv, captured: captured["stdin"]),
            ("claude_print_arg", lambda argv, captured: argv[argv.index("-p") + 1]),
            ("codex_exec_json", lambda argv, captured: captured["stdin"]),
            ("opencode_run_arg", lambda argv, captured: argv[-1]),
        ]

        for transport, prompt_arg in cases:
            chunks = []
            async for chunk in stream_agent_runtime(
                runtime={
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": transport,
                    "output_mode": "plain",
                    "timeout_seconds": 10,
                },
                prompt=prompt,
                cwd=None,
            ):
                chunks.append(chunk)

            captured = json.loads("".join(chunks))
            argv = captured["argv"]
            assert captured["prompt_file"] == prompt
            assert prompt_arg(argv, captured) == prompt
            if transport == "stdin":
                assert argv == []
            elif transport == "claude_print_arg":
                assert "--output-format" in argv
                assert "stream-json" in argv
                assert "--include-partial-messages" in argv
                assert "--verbose" in argv
            elif transport == "codex_exec_json":
                assert "exec" in argv
                assert "--json" in argv
                assert prompt not in argv
            else:
                assert argv[-4:-1] == ["run", "--format", "json"]

    async def test_opencode_managed_transport_resumes_session_and_requests_json_format(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = "import json, sys; print(json.dumps(sys.argv[1:], ensure_ascii=False), flush=True)"
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "opencode_run_arg",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="继续分析源码",
            cwd=None,
            resume_session_id="opencode-session-1",
        ):
            chunks.append(chunk)

        args = json.loads("".join(chunks))
        assert args[:5] == ["run", "--session", "opencode-session-1", "--format", "json"]
        assert args[-1] == "继续分析源码"

    async def test_agent_runtime_idle_completion_extends_while_stderr_is_active(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys, time; "
            "print('首段源码分析。', flush=True); "
            "\nfor i in range(5):\n"
            "    sys.stderr.write(f'thinking: still reading source {i}\\n'); sys.stderr.flush(); time.sleep(0.35)\n"
            "print('最终答案：stderr 活动期间不应被 idle 提前截断。', flush=True)"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
                "completion_mode": "idle_after_output",
                "idle_complete_seconds": 1,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert "首段源码分析" in output
        assert "最终答案：stderr 活动期间不应被 idle 提前截断。" in output

    async def test_agent_runtime_activity_extends_configured_timeout(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import time; "
            "\nfor i in range(6):\n"
            "    print(f'STATUS: still working {i}', flush=True); time.sleep(0.25)\n"
            "print('最终答案：持续活动的任务不应被绝对计时误杀。', flush=True)"
        )
        chunks: list[str] = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 1,
                "completion_mode": "process_exit",
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        assert "最终答案：持续活动的任务不应被绝对计时误杀。" in "".join(chunks)

    async def test_agent_runtime_reports_stderr_progress_without_polluting_answer(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys, time; "
            "sys.stderr.write('reading workspace source: lib/nvmf/connect.c\\n'); sys.stderr.flush(); "
            "time.sleep(0.05); "
            "sys.stderr.write('building SFMEA evidence table\\n'); sys.stderr.flush(); "
            "time.sleep(0.05); "
            "print('最终答案：stderr 进度只应进入折叠过程。', flush=True)"
        )
        chunks: list[str] = []
        progress: list[str] = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
            stderr_update=progress.append,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.strip() == "最终答案：stderr 进度只应进入折叠过程。"
        assert any("reading workspace source: lib/nvmf/connect.c" in item for item in progress)
        assert any("building SFMEA evidence table" in item for item in progress)

    async def test_agent_runtime_stream_decodes_utf16le_stdout_from_windows_shells(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stdout.buffer.write('最终答案：已完成源码分析。'.encode('utf-16le')); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output == "最终答案：已完成源码分析。"
        assert "�" not in output

    async def test_agent_runtime_stream_preserves_gbk_text_in_mixed_terminal_noise(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stdout.write('\\x1b[32m47%\\n12/100\\n'); "
            "sys.stdout.buffer.write(bytes([0x80, 0x81, 0x8D, 0x90, 0x9D]) + b'\\n'); "
            "sys.stdout.flush(); "
            "sys.stdout.write('\\r\\x1b[2K⠋ 12\\r\\x1b[2K⠙ 47\\r\\x1b[2K'); "
            "sys.stdout.flush(); "
            "sys.stdout.buffer.write('源码证据：连接失败\\n'.encode('gbk')); "
            "sys.stdout.write('FINAL_NOISE_CLEAN_ANSWER: 已完成源码分析。\\n'); "
            "sys.stdout.write('\\x1b[0m'); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert "源码证据：连接失败" in output
        assert "FINAL_NOISE_CLEAN_ANSWER: 已完成源码分析。" in output
        assert "47%" not in output
        assert "12/100" not in output
        assert "�" not in output

    async def test_agent_runtime_stream_drops_numeric_progress_noise(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stdout.write('1\\n2\\n47%\\n12/100\\n'); "
            "sys.stdout.flush(); "
            "sys.stdout.write('最终答案：已完成源码分析。\\n'); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.strip() == "最终答案：已完成源码分析。"
        assert "47%" not in output
        assert "12/100" not in output

    async def test_agent_runtime_stream_drops_split_osc_terminal_sequence(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys, time; "
            "sys.stdout.write('\\x1b]8;;file:///tmp/nga-session-12345'); "
            "sys.stdout.flush(); "
            "time.sleep(0.05); "
            "sys.stdout.write('\\x07最终答案：已完成源码分析。\\n'); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.strip() == "最终答案：已完成源码分析。"
        assert "file:///tmp/nga-session-12345" not in output
        assert "8;;" not in output

    async def test_agent_runtime_stream_drops_split_dcs_terminal_sequence(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys, time; "
            "sys.stdout.write('\\x1bP1;2;3+q54321'); "
            "sys.stdout.flush(); "
            "time.sleep(0.05); "
            "sys.stdout.write('\\x1b\\\\最终答案：DCS 噪声已清理。\\n'); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.strip() == "最终答案：DCS 噪声已清理。"
        assert "54321" not in output

    async def test_agent_runtime_stream_strips_progress_glyph_prefix_before_answer(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stdout.write('■■■■⬝⬝⬝⬝■■■■■⬝⬝⬝兼容\\n'); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.strip() == "兼容"
        assert "■" not in output
        assert "⬝" not in output

    async def test_agent_runtime_stream_drops_binary_gibberish_replacement_noise(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stdout.buffer.write(bytes([0x80, 0x81, 0x8D, 0x90, 0x9D]) + b'\\n'); "
            "sys.stdout.flush(); "
            "sys.stdout.write('最终答案：已完成源码分析。\\n'); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.strip() == "最终答案：已完成源码分析。"
        assert "�" not in output

    async def test_agent_runtime_stream_drops_mojibake_numeric_noise(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stdout.write('æº\\x90ç\\xa0\\x8112345\\n'); "
            "sys.stdout.write('榛戠爜67890\\n'); "
            "sys.stdout.write('最终答案：已完成源码分析，覆盖 3 条风险。\\n'); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.strip() == "最终答案：已完成源码分析，覆盖 3 条风险。"
        assert "æº" not in output
        assert "榛戠爜" not in output
        assert "67890" not in output

    async def test_agent_runtime_stream_drops_symbol_numeric_terminal_noise(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stdout.write('▒▒▒▒▒1293847560\\n'); "
            "sys.stdout.write('▛▛▛▛8899001122\\n'); "
            "sys.stdout.write('╳╳╳╳╳╳4455667788\\n'); "
            "sys.stdout.write('最终答案：已完成源码分析，覆盖 3 条风险。\\n'); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.strip() == "最终答案：已完成源码分析，覆盖 3 条风险。"
        assert "▒▒▒▒▒1293847560" not in output
        assert "▛▛▛▛8899001122" not in output
        assert "╳╳╳╳╳╳4455667788" not in output

    async def test_agent_runtime_plain_stream_preserves_utf8_split_across_read_boundary(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stdout.buffer.write(b'a' * 4095 + bytes([0xe6])); "
            "sys.stdout.flush(); "
            "sys.stdout.buffer.write(bytes([0xba, 0x90]) + '码证据'.encode('utf-8')); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.endswith("源码证据")
        assert "�" not in output

    async def test_agent_runtime_auto_mode_cleans_plain_noise_before_json_answer(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import json, sys; "
            "sys.stdout.write('1\\n47%\\n'); "
            "sys.stdout.buffer.write(bytes([0x80, 0x81, 0x8D, 0x90, 0x9D]) + b'\\n'); "
            "sys.stdout.flush(); "
            "print(json.dumps({'content':'最终答案：auto 模式已完成源码分析。'}, ensure_ascii=False))"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.strip() == "最终答案：auto 模式已完成源码分析。"
        assert "47%" not in output
        assert "�" not in output

    async def test_agent_runtime_auto_mode_drops_openai_response_metadata_events(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import json, sys; "
            "print(json.dumps({'type':'response.created','response':{'id':'resp_1'}}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.output_item.added','item':{'id':'msg_1','type':'message'}}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.output_text.delta','delta':'最终答案：auto 模式保留正文。'}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.completed','response':{'status':'completed'}}, ensure_ascii=False)); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.strip() == "最终答案：auto 模式保留正文。"
        assert "response.created" not in output
        assert "response.completed" not in output

    async def test_agent_runtime_auto_mode_uses_openai_output_item_done_as_final_answer(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import json, sys; "
            "print(json.dumps({'type':'response.created','response':{'id':'resp_1'}}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.output_item.added','item':{'id':'msg_1','type':'message','role':'assistant'}}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.output_item.done','item':{'id':'msg_1','type':'message','role':'assistant','content':[{'type':'output_text','text':'## 结论\\nResponses API 最终回答已保留。\\n\\n## 代码证据\\n- lib/iscsi/iscsi.c: login 状态机。'}]}}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.completed','response':{'status':'completed'}}, ensure_ascii=False)); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert "Responses API 最终回答已保留" in output
        assert "lib/iscsi/iscsi.c" in output
        assert "response.output_item.done" not in output
        assert "response.completed" not in output

    async def test_agent_runtime_auto_mode_uses_openai_response_completed_output_as_final_answer(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import json, sys; "
            "print(json.dumps({'type':'response.created','response':{'id':'resp_1'}}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.completed','response':{'status':'completed','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'## 结论\\ncompleted.output 最终回答已保留。\\n\\n## 代码证据\\n- lib/nvmf/ctrlr.c: connect 路径。'}]}]}}, ensure_ascii=False)); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert "completed.output 最终回答已保留" in output
        assert "lib/nvmf/ctrlr.c" in output
        assert "response.completed" not in output

    async def test_agent_runtime_auto_mode_uses_openai_output_text_done_as_final_answer(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import json, sys; "
            "print(json.dumps({'type':'response.created','response':{'id':'resp_1'}}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.output_text.done','text':'## 结论\\noutput_text.done 最终回答已保留。\\n\\n## 代码证据\\n- lib/bdev/bdev.c: submit 路径。'}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.completed','response':{'status':'completed'}}, ensure_ascii=False)); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert "output_text.done 最终回答已保留" in output
        assert "lib/bdev/bdev.c" in output
        assert "response.output_text.done" not in output

    async def test_agent_runtime_auto_mode_only_surfaces_clowder_style_agent_text_events(self):
        from app.services.agent_cli_bridge import stream_agent_runtime
        from app.services.ai_conversations import _agent_output_segments

        agent_code = (
            "import json, sys; "
            "events=["
            "{'type':'system','subtype':'init','session_id':'claude-session'},"
            "{'type':'assistant','message':{'content':[{'type':'tool_use','name':'Read','input':{'file':'secret.py'}}]}},"
            "{'type':'assistant','message':{'content':[{'type':'text','text':'Claude 正文回答。'}]}},"
            "{'type':'thread.started','thread_id':'codex-thread'},"
            "{'type':'turn.started'},"
            "{'type':'item.completed','item':{'type':'command_execution','command':'rg token'}},"
            "{'type':'item.completed','item':{'type':'agent_message','text':'Codex 正文回答。'}},"
            "{'type':'message','role':'assistant','content':'Gemini 正文回答。'},"
            "{'type':'tool_result','content':'internal result'},"
            "{'type':'result','status':'success'}"
            "]; "
            "[print(json.dumps(event, ensure_ascii=False)) for event in events]; "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        segments = [segment for chunk in chunks for segment in _agent_output_segments(chunk)]
        answer = "".join(content for kind, content in segments if kind == "answer")
        diagnostics = "\n".join(content for kind, content in segments if kind == "diagnostic")
        all_visible = answer + diagnostics
        assert answer == "Claude 正文回答。Codex 正文回答。Gemini 正文回答。"
        assert "session_id" not in all_visible
        assert "command_execution" not in all_visible
        assert "thread.started" not in all_visible
        assert "tool_use" not in answer
        assert "tool_result" not in answer
        assert "internal result" in diagnostics

    async def test_agent_runtime_auto_mode_folds_mixed_assistant_content_parts(self):
        from app.services.agent_cli_bridge import stream_agent_runtime
        from app.services.ai_conversations import _agent_output_segments

        agent_code = (
            "import json, sys; "
            "event={"
            "'type':'message',"
            "'role':'assistant',"
            "'content':["
            "{'type':'thinking','text':'内部推理：先列出工具计划'},"
            "{'type':'tool_result','content':'cat /secret/path returned internal-only trace'},"
            "{'type':'text','text':'最终答案：只展示源码分析结论。'}"
            "]"
            "}; "
            "print(json.dumps(event, ensure_ascii=False)); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        segments = [segment for chunk in chunks for segment in _agent_output_segments(chunk)]
        answer = "".join(content for kind, content in segments if kind == "answer")
        diagnostics = "\n".join(content for kind, content in segments if kind == "diagnostic")

        assert answer == "只展示源码分析结论。"
        assert "内部推理" not in answer
        assert "tool_result" not in answer
        assert "secret/path" not in answer
        assert "内部推理：先列出工具计划" in diagnostics
        assert "cat /secret/path returned internal-only trace" in diagnostics

    async def test_agent_runtime_plain_tool_invocation_parentheses_fold_as_diagnostics(self):
        from app.services.ai_conversations import _agent_output_segments

        raw = "\n".join(
            [
                "Read(file_path='lib/nvmf/ctrlr.c')",
                "Bash(command='rg nvmf_ctrlr_connect lib/nvmf')",
                "lib/nvmf/ctrlr.c:1125:nvmf_ctrlr_connect(void *arg)",
                "## 结论",
                "最终答案：只展示源码分析结论。",
            ]
        )
        segments = _agent_output_segments(raw)
        answer = "".join(content for kind, content in segments if kind == "answer")
        diagnostics = "\n".join(content for kind, content in segments if kind == "diagnostic")

        assert "只展示源码分析结论。" in answer
        assert "Read(file_path" not in answer
        assert "Bash(command" not in answer
        assert "nvmf_ctrlr_connect(void" not in answer
        assert "Read(file_path='lib/nvmf/ctrlr.c')" in diagnostics
        assert "Bash(command='rg nvmf_ctrlr_connect lib/nvmf')" in diagnostics
        assert "nvmf_ctrlr_connect(void" in diagnostics

    async def test_agent_runtime_plain_mode_drops_cli_banner_without_hiding_answer(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stdout.write('Claude Code v1.2.3\\n'); "
            "sys.stdout.write('cwd: /tmp/project\\n'); "
            "sys.stdout.write('Welcome to Claude Code\\n'); "
            "sys.stdout.write('Ready for your next task.\\n'); "
            "sys.stdout.write('Tip: press Ctrl+C to stop generation\\n'); "
            "sys.stdout.write('╭──────────────────────────────╮\\n'); "
            "sys.stdout.write('│ Thinking…                    │\\n'); "
            "sys.stdout.write('│ Session ready                │\\n'); "
            "sys.stdout.write('> 分析 SPDK 流程\\n'); "
            "sys.stdout.write('最终答案：只展示用户需要看的回答。\\n'); "
            "sys.stdout.write('╰──────────────────────────────╯\\n'); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.strip() == "最终答案：只展示用户需要看的回答。"
        assert "Claude Code" not in output
        assert "cwd:" not in output
        assert "Thinking" not in output
        assert "Welcome" not in output
        assert "Ready" not in output
        assert "Tip:" not in output
        assert "Session ready" not in output

    async def test_agent_runtime_auto_mode_keeps_response_reasoning_out_of_answer(self):
        from app.services.agent_cli_bridge import stream_agent_runtime
        from app.services.ai_conversations import _agent_output_segments

        agent_code = (
            "import json, sys; "
            "print(json.dumps({'type':'response.reasoning_text.delta','delta':'内部推理：先搜索源码。'}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.output_text.delta','delta':'最终答案：只展示可交付正文。'}, ensure_ascii=False)); "
            "print(json.dumps({'type':'response.refusal.delta','delta':'拒绝诊断：策略提示。'}, ensure_ascii=False)); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        segments = [segment for chunk in chunks for segment in _agent_output_segments(chunk)]
        answer = "".join(content for kind, content in segments if kind == "answer")
        diagnostics = [content for kind, content in segments if kind == "diagnostic"]
        assert answer.strip() == "只展示可交付正文。"
        assert "内部推理" not in answer
        assert "拒绝诊断" not in answer
        assert any("内部推理：先搜索源码。" in item for item in diagnostics)
        assert any("拒绝诊断：策略提示。" in item for item in diagnostics)

    async def test_agent_runtime_codex_agent_message_delta_chunks_surface_as_answer(self):
        from app.services.agent_cli_bridge import stream_agent_runtime
        from app.services.ai_conversations import _agent_output_segments

        agent_code = (
            "import json, sys; "
            "events=["
            "{'type':'thread.started','thread_id':'codex-delta-session'},"
            "{'type':'item.started','item':{'type':'command_execution','command':'rg nvmf_connect lib/nvmf'}},"
            "{'type':'item.updated','item':{'type':'agent_message','delta':'最终答案：'}},"
            "{'type':'item.updated','item':{'type':'agent_message','delta':'已基于源码完成分析。'}},"
            "{'type':'item.completed','item':{'type':'agent_message'}}"
            "]; "
            "[print(json.dumps(event, ensure_ascii=False), flush=True) for event in events]; "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "stream_json",
                "timeout_seconds": 10,
            },
            prompt="读取源码后回答",
            cwd=None,
        ):
            chunks.append(chunk)

        segments = [segment for chunk in chunks for segment in _agent_output_segments(chunk)]
        answer = "".join(content for kind, content in segments if kind == "answer")
        diagnostics = "\n".join(content for kind, content in segments if kind == "diagnostic")
        assert answer == "已基于源码完成分析。"
        assert "command: rg nvmf_connect lib/nvmf" in diagnostics
        assert "thread.started" not in answer + diagnostics

    async def test_codex_turn_completed_finishes_even_when_cli_process_keeps_stdout_open(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import json, sys, time; "
            "sys.stdin.read(); "
            "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'最终答案：已完成。'}}, ensure_ascii=False), flush=True); "
            "print(json.dumps({'type':'turn.completed','usage':{}}, ensure_ascii=False), flush=True); "
            "time.sleep(5)"
        )
        chunks: list[str] = []

        async with asyncio.timeout(2):
            async for chunk in stream_agent_runtime(
                runtime={
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "codex_exec_json",
                    "output_mode": "stream_json",
                    "timeout_seconds": 10,
                    "completion_mode": "process_exit",
                },
                prompt="读取源码后回答",
                cwd=None,
            ):
                chunks.append(chunk)

        assert "最终答案：已完成。" in "".join(chunks)

    async def test_codex_reconnect_stderr_does_not_mask_productive_inactivity(self):
        from app.services.agent_cli_bridge import AgentRuntimeError, stream_agent_runtime

        agent_code = (
            "import sys, time; "
            "sys.stdin.read(); "
            "\nfor i in range(20):\n"
            "    sys.stderr.write(f'Reconnecting... {i % 5 + 1}/5\\n'); sys.stderr.flush(); time.sleep(0.2)\n"
        )
        progress: list[str] = []

        with pytest.raises(AgentRuntimeError, match="连续 1s 没有输出或进度"):
            async with asyncio.timeout(2.5):
                async for _chunk in stream_agent_runtime(
                    runtime={
                        "command": sys.executable,
                        "args": ["-c", agent_code],
                        "prompt_transport": "codex_exec_json",
                        "output_mode": "stream_json",
                        "timeout_seconds": 1,
                        "completion_mode": "process_exit",
                    },
                    prompt="读取源码后回答",
                    cwd=None,
                    stderr_update=progress.append,
                ):
                    pass

        assert any("正在自动重试" in item for item in progress)

    async def test_stdout_reader_cancellation_reaps_idle_and_activity_waiters(self, monkeypatch):
        import app.services.agent_cli_bridge as bridge

        class Process:
            stdout = asyncio.StreamReader()

        created: list[asyncio.Task] = []
        real_create_task = asyncio.create_task

        def track_task(coro):
            task = real_create_task(coro)
            created.append(task)
            return task

        monkeypatch.setattr(bridge.asyncio, "create_task", track_task)
        stream = bridge._read_stdout(
            Process(),
            "plain",
            runtime={"timeout_seconds": 120},
            activity_queue=asyncio.Queue(),
        )
        outer = asyncio.get_running_loop().create_task(anext(stream))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer
        await asyncio.sleep(0)

        assert created
        assert all(task.done() for task in created)

    async def test_agent_runtime_accepts_codex_ndjson_lines_larger_than_asyncio_default(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import json, sys; "
            "sys.stdin.read(); "
            "text='## 结论\\n' + ('源码证据完整。' * 60000) + '\\n最终答案已生成。'; "
            "event={'type':'item.completed','item':{'type':'agent_message','text':text}}; "
            "print(json.dumps(event, ensure_ascii=False), flush=True)"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "codex_exec_json",
                "output_mode": "stream_json",
                "timeout_seconds": 10,
            },
            prompt="读取源码后回答",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert len(output.encode("utf-8")) > 1024 * 1024
        assert output.startswith("__CODETALK_AGENT_FINAL_ANSWER__:## 结论")
        assert output.endswith("最终答案已生成。")

    async def test_agent_stream_record_rejects_beyond_configured_total_limit(self):
        from app.services.agent_cli_bridge import AgentRuntimeError, _read_agent_stream_record

        reader = asyncio.StreamReader(limit=4)
        reader.feed_data(b"123456789\n")
        reader.feed_eof()

        with pytest.raises(AgentRuntimeError, match="单条过程事件超过安全上限"):
            await _read_agent_stream_record(reader, max_bytes=8)

    async def test_agent_runtime_hides_codex_plugin_manifest_noise_but_keeps_reconnect_progress(self):
        from app.services.agent_cli_bridge import _stderr_progress_lines

        lines = _stderr_progress_lines(
            "2026-07-10T15:58:01Z WARN codex_core_plugins::manifest: "
            "ignoring interface.defaultPrompt\n"
            "Reconnecting... 2/5\n"
            "2026-07-10T15:58:03Z WARN codex_core::responses_retry: "
            "stream disconnected - retrying sampling request (3/5 in 400ms)\n"
            "2026-07-10T15:58:04Z ERROR codex_core_plugins::manifest: "
            "failed to parse plugin manifest\n"
            "2026-07-10T15:58:05Z ERROR codex_models_manager::cache: "
            "failed to write models cache: Operation not permitted (os error 1)\n"
            "2026-07-10T15:58:06Z ERROR codex_core_skills::loader: "
            "failed to read skills symlink dir /repo/.codex/skills/example: "
            "Operation not permitted (os error 1)\n"
        )

        assert lines == [
            "Agent 连接中断，正在自动重试（2/5）。",
            "Agent 连接中断，正在自动重试（3/5）。",
            "2026-07-10T15:58:04Z ERROR codex_core_plugins::manifest: failed to parse plugin manifest",
        ]

    async def test_ai_thread_codex_delta_final_answer_persists_without_repair(
        self,
        sqlite_db,
        tmp_path,
    ):
        repo = tmp_path / "spdk"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-codex-delta-final", repo_path=str(repo))
        agent_script = tmp_path / "codex_delta_final_agent.py"
        agent_script.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "sys.stdin.read()",
                    "events = [",
                    "  {'type':'thread.started','thread_id':'codex-delta-session'},",
                    "  {'type':'item.completed','item':{'type':'command_execution','command':'rg nvmf_connect lib/nvmf','status':'completed','exit_code':0,'aggregated_output':'lib/nvmf/ctrlr.c: nvmf_connect'}},",
                    "  {'type':'item.updated','item':{'type':'agent_message','delta':'CODEX_DELTA_FINAL: '}},",
                    "  {'type':'item.updated','item':{'type':'agent_message','delta':'已基于源码完成增量回答。'}},",
                    "  {'type':'item.completed','item':{'type':'agent_message'}},",
                    "]",
                    "for event in events:",
                    "    print(json.dumps(event, ensure_ascii=False), flush=True)",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        from app.services.ai_conversations import AIConversationStore, run_agent_generation

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Codex delta final thread",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-codex-delta-final",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="请用 Codex delta 事件读取源码并输出最终回答",
            references=[],
        )
        run_id = created["run"]["id"]

        await run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "runtime-codex-delta-final",
                "name": "Codex Delta Final Agent",
                "command": sys.executable,
                "args": [str(agent_script)],
                "prompt_transport": "codex_exec_json",
                "output_mode": "stream_json",
                "working_dir_mode": "project",
                "timeout_seconds": 10,
            },
        )

        run = await store.get_run(run_id)
        assert run["status"] == "completed"
        messages = await store.list_messages(conversation["id"])
        assistant = [item for item in messages if item["role"] == "assistant"][-1]
        assert "CODEX_DELTA_FINAL: 已基于源码完成增量回答。" in assistant["content"]
        assert "nvmf_connect" not in assistant["content"]

    async def test_agent_runtime_chat_choice_delta_chunks_surface_as_answer(self):
        from app.services.agent_cli_bridge import stream_agent_runtime
        from app.services.ai_conversations import _agent_output_segments

        agent_code = (
            "import json, sys; "
            "events=["
            "{'choices':[{'delta':{'role':'assistant'}}]},"
            "{'choices':[{'delta':{'content':'CHAT_CHOICE_FINAL: 已基于源码完成分析。\\n\\n## 代码证据\\n- `lib/bdev/bdev.c`: submit 路径。\\n- `test/bdev`: 可承载回归。\\n\\n'}}]},"
            "{'type':'item.completed','item':{'type':'command_execution','command':'rg bdev_submit lib/bdev','status':'completed','exit_code':0,'aggregated_output':'lib/bdev/bdev.c:bdev_submit'}},"
            "{'choices':[{'delta':{'content':'## 流程梳理\\n1. 读取 bdev submit 证据。\\n2. 输出外部可见结论。'}}]},"
            "{'choices':[{'finish_reason':'stop'}]}"
            "]; "
            "[print(json.dumps(event, ensure_ascii=False), flush=True) for event in events]; "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码后回答",
            cwd=None,
        ):
            chunks.append(chunk)

        segments = [segment for chunk in chunks for segment in _agent_output_segments(chunk)]
        answer = "".join(content for kind, content in segments if kind == "answer")
        diagnostics = "\n".join(content for kind, content in segments if kind == "diagnostic")
        assert "CHAT_CHOICE_FINAL" in answer
        assert "lib/bdev/bdev.c" in answer
        assert "## 流程梳理" in answer
        assert "command: rg bdev_submit lib/bdev" in diagnostics
        assert "bdev_submit" not in answer
        assert "finish_reason" not in answer + diagnostics

    async def test_agent_runtime_chat_choice_tool_calls_surface_as_diagnostics(self):
        from app.services.agent_cli_bridge import stream_agent_runtime
        from app.services.ai_conversations import _agent_output_segments

        agent_code = (
            "import json, sys; "
            "events=["
            "{'choices':[{'delta':{'tool_calls':[{'id':'call_1','type':'function','function':{'name':'search_source','arguments':'{\"query\":\"bdev submit\"}'}}]}}]},"
            "{'choices':[{'delta':{'function_call':{'name':'read_file','arguments':'{\"path\":\"lib/bdev/bdev.c\"}'}}}]},"
            "{'choices':[{'delta':{'content':'已读取工具过程并输出答案。'}}]}"
            "]; "
            "[print(json.dumps(event, ensure_ascii=False), flush=True) for event in events]; "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码后回答",
            cwd=None,
        ):
            chunks.append(chunk)

        segments = [segment for chunk in chunks for segment in _agent_output_segments(chunk)]
        answer = "".join(content for kind, content in segments if kind == "answer")
        diagnostics = "\n".join(content for kind, content in segments if kind == "diagnostic")
        assert answer == "已读取工具过程并输出答案。"
        assert "search_source" in diagnostics
        assert "read_file" in diagnostics
        assert "lib/bdev/bdev.c" in diagnostics
        assert "tool_calls" not in answer

    async def test_agent_runtime_chat_choice_tool_call_and_content_in_same_delta_keep_answer(self):
        from app.services.agent_cli_bridge import stream_agent_runtime
        from app.services.ai_conversations import _agent_output_segments

        agent_code = (
            "import json, sys; "
            "event={'choices':[{'delta':{"
            "'tool_calls':[{'id':'call_1','type':'function','function':{'name':'search_source','arguments':'{\"query\":\"nvmf connect\"}'}}],"
            "'content':'同包回答：已基于工具调用继续输出结论。'"
            "}}]}; "
            "print(json.dumps(event, ensure_ascii=False), flush=True); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码后回答",
            cwd=None,
        ):
            chunks.append(chunk)

        segments = [segment for chunk in chunks for segment in _agent_output_segments(chunk)]
        answer = "".join(content for kind, content in segments if kind == "answer")
        diagnostics = "\n".join(content for kind, content in segments if kind == "diagnostic")
        assert answer == "同包回答：已基于工具调用继续输出结论。"
        assert "search_source" in diagnostics
        assert "nvmf connect" in diagnostics
        assert "tool_calls" not in answer

    async def test_agent_runtime_chat_choice_streamed_tool_arguments_collapse_to_single_diagnostic(self):
        from app.services.agent_cli_bridge import stream_agent_runtime
        from app.services.ai_conversations import _agent_output_segments

        agent_code = (
            "import json, sys; "
            "events=["
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_1','type':'function','function':{'name':'search_source','arguments':'{\"query\":\"'}}]}}]},"
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'nvmf connect'}}]}}]},"
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'\"}'}}]}}]},"
            "{'choices':[{'delta':{'content':'工具参数完整后输出最终回答。'}}]}"
            "]; "
            "[print(json.dumps(event, ensure_ascii=False), flush=True) for event in events]; "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码后回答",
            cwd=None,
        ):
            chunks.append(chunk)

        segments = [segment for chunk in chunks for segment in _agent_output_segments(chunk)]
        answer = "".join(content for kind, content in segments if kind == "answer")
        diagnostics = "\n".join(content for kind, content in segments if kind == "diagnostic")
        assert answer == "工具参数完整后输出最终回答。"
        assert diagnostics.count("search_source") == 1
        assert 'search_source {"query":"nvmf connect"}' in diagnostics
        assert 'function_call {"query":"' not in diagnostics
        assert "tool_calls" not in answer + diagnostics

    async def test_agent_runtime_chat_choice_empty_argument_name_chunk_waits_for_arguments(self):
        from app.services.agent_cli_bridge import stream_agent_runtime
        from app.services.ai_conversations import _agent_output_segments

        agent_code = (
            "import json, sys; "
            "events=["
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_1','type':'function','function':{'name':'search_source','arguments':''}}]}}]},"
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'{\"query\":\"'}}]}}]},"
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'nvmf connect'}}]}}]},"
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'\"}'}}]}}]},"
            "{'choices':[{'delta':{'content':'空参数首段聚合后输出最终回答。'}}]}"
            "]; "
            "[print(json.dumps(event, ensure_ascii=False), flush=True) for event in events]; "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码后回答",
            cwd=None,
        ):
            chunks.append(chunk)

        segments = [segment for chunk in chunks for segment in _agent_output_segments(chunk)]
        answer = "".join(content for kind, content in segments if kind == "answer")
        diagnostics = "\n".join(content for kind, content in segments if kind == "diagnostic")
        assert answer == "空参数首段聚合后输出最终回答。"
        assert diagnostics.count("search_source") == 1
        assert 'search_source {"query":"nvmf connect"}' in diagnostics
        assert "function_call" not in diagnostics

    async def test_agent_runtime_chat_choice_name_only_tool_chunk_waits_for_arguments(self):
        from app.services.agent_cli_bridge import stream_agent_runtime
        from app.services.ai_conversations import _agent_output_segments

        agent_code = (
            "import json, sys; "
            "events=["
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_1','type':'function','function':{'name':'search_source'}}]}}]},"
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'{\"query\":\"'}}]}}]},"
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'nvmf connect'}}]}}]},"
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'\"}'}}]}}]},"
            "{'choices':[{'delta':{'content':'工具名首段聚合后输出最终回答。'}}]}"
            "]; "
            "[print(json.dumps(event, ensure_ascii=False), flush=True) for event in events]; "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码后回答",
            cwd=None,
        ):
            chunks.append(chunk)

        segments = [segment for chunk in chunks for segment in _agent_output_segments(chunk)]
        answer = "".join(content for kind, content in segments if kind == "answer")
        diagnostics = "\n".join(content for kind, content in segments if kind == "diagnostic")
        assert answer == "工具名首段聚合后输出最终回答。"
        assert diagnostics.count("search_source") == 1
        assert 'search_source {"query":"nvmf connect"}' in diagnostics
        assert "function_call" not in diagnostics

    async def test_agent_runtime_chat_choice_interleaved_tool_arguments_do_not_cross_streams(self):
        from app.services.agent_cli_bridge import stream_agent_runtime
        from app.services.ai_conversations import _agent_output_segments

        agent_code = (
            "import json, sys; "
            "events=["
            "{'choices':[{'delta':{'tool_calls':["
            "{'index':0,'id':'call_search','type':'function','function':{'name':'search_source','arguments':'{\"query\":\"'}},"
            "{'index':1,'id':'call_read','type':'function','function':{'name':'read_file','arguments':'{\"path\":\"'}}"
            "]}}]},"
            "{'choices':[{'delta':{'tool_calls':[{'index':1,'function':{'arguments':'lib/nvmf/ctrlr.c'}}]}}]},"
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'nvmf connect'}}]}}]},"
            "{'choices':[{'delta':{'tool_calls':[{'index':1,'function':{'arguments':'\"}'}}]}}]},"
            "{'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'\"}'}}]}}]},"
            "{'choices':[{'delta':{'content':'交错工具参数聚合后输出最终回答。'}}]}"
            "]; "
            "[print(json.dumps(event, ensure_ascii=False), flush=True) for event in events]; "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码后回答",
            cwd=None,
        ):
            chunks.append(chunk)

        segments = [segment for chunk in chunks for segment in _agent_output_segments(chunk)]
        answer = "".join(content for kind, content in segments if kind == "answer")
        diagnostics = "\n".join(content for kind, content in segments if kind == "diagnostic")
        assert answer == "交错工具参数聚合后输出最终回答。"
        assert diagnostics.count("search_source") == 1
        assert diagnostics.count("read_file") == 1
        assert 'search_source {"query":"nvmf connect"}' in diagnostics
        assert 'read_file {"path":"lib/nvmf/ctrlr.c"}' in diagnostics
        assert "function_call" not in diagnostics

    async def test_agent_runtime_auto_mode_cleans_plain_fallback_chunks(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stdout.write('\\x1b]0;agent title\\x07'); "
            "sys.stdout.write('\\x1b[33m\\r\\x1b[2K⠋ 12\\r\\x1b[2K'); "
            "sys.stdout.write('最终答案：auto fallback 已完成源码分析。\\x1b[0m\\n'); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "auto",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        output = "".join(chunks)
        assert output.strip() == "最终答案：auto fallback 已完成源码分析。"
        assert "\x1b" not in output
        assert "agent title" not in output
        assert "⠋ 12" not in output

    async def test_agent_runtime_stream_json_accepts_sse_event_metadata(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import json, sys; "
            "print('event: message'); "
            "print('data: ' + json.dumps({'content':'SSE event 源码证据'}, ensure_ascii=False)); "
            "print(); "
            "sys.stdout.flush()"
        )
        chunks = []
        async for chunk in stream_agent_runtime(
            runtime={
                "command": sys.executable,
                "args": ["-c", agent_code],
                "prompt_transport": "stdin",
                "output_mode": "stream_json",
                "timeout_seconds": 10,
            },
            prompt="读取源码",
            cwd=None,
        ):
            chunks.append(chunk)

        assert "".join(chunks).strip() == "SSE event 源码证据"

    async def test_agent_runtime_failure_cleans_stderr_noise(self):
        from app.services.agent_cli_bridge import AgentRuntimeError, stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stderr.write('1\\n47%\\n'); "
            "sys.stderr.buffer.write(bytes([0x80, 0x81, 0x8D, 0x90, 0x9D]) + b'\\n'); "
            "sys.stderr.write('fatal: agent failed while reading workspace source\\n'); "
            "sys.stderr.flush(); "
            "raise SystemExit(7)"
        )

        with pytest.raises(AgentRuntimeError) as excinfo:
            async for _ in stream_agent_runtime(
                runtime={
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "timeout_seconds": 10,
                },
                prompt="读取源码",
                cwd=None,
            ):
                pass

        message = str(excinfo.value)
        assert "fatal: agent failed while reading workspace source" in message
        assert "47%" not in message
        assert "�" not in message

    async def test_agent_runtime_failure_surfaces_structured_claude_auth_error(self):
        from app.services.agent_cli_bridge import AgentRuntimeError, stream_agent_runtime

        agent_code = (
            "import json; "
            "print(json.dumps({'type':'assistant','message':{'role':'assistant','content':[{'type':'text','text':'Failed to authenticate. API Error: 403 Request not allowed'}],'error':'authentication_failed'}}), flush=True); "
            "print(json.dumps({'type':'result','subtype':'success','is_error':True,'api_error_status':403,'result':'Failed to authenticate. API Error: 403 Request not allowed'}), flush=True); "
            "raise SystemExit(1)"
        )

        with pytest.raises(AgentRuntimeError) as excinfo:
            async for _ in stream_agent_runtime(
                runtime={
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "stream_json",
                    "timeout_seconds": 10,
                },
                prompt="读取源码",
                cwd=None,
            ):
                pass

        message = str(excinfo.value)
        assert "认证失败" in message
        assert "HTTP 403" in message
        assert "重新登录" in message

    async def test_agent_runtime_failure_preserves_stderr_utf8_split_across_read_boundary(self):
        from app.services.agent_cli_bridge import AgentRuntimeError, stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stderr.buffer.write(b'a' * 4095 + bytes([0xe6])); "
            "sys.stderr.flush(); "
            "sys.stderr.buffer.write(bytes([0xba, 0x90]) + '码读取失败'.encode('utf-8')); "
            "sys.stderr.flush(); "
            "raise SystemExit(7)"
        )

        with pytest.raises(AgentRuntimeError) as excinfo:
            async for _ in stream_agent_runtime(
                runtime={
                    "command": sys.executable,
                    "args": ["-c", agent_code],
                    "prompt_transport": "stdin",
                    "output_mode": "plain",
                    "timeout_seconds": 10,
                },
                prompt="读取源码",
                cwd=None,
            ):
                pass

        message = str(excinfo.value)
        assert message.endswith("源码读取失败")
        assert "�" not in message

    async def test_probe_agent_runtime_resolves_windows_npm_cmd_shim_before_spawn(self, monkeypatch):
        from app.services import agent_cli_bridge

        captured: dict[str, object] = {}
        original_resolver = agent_cli_bridge._resolve_agent_command

        class FakeProbeProcess:
            returncode = 0

            async def communicate(self):
                return b"opencode ok", b""

        async def fake_create_subprocess_exec(command, *args, **kwargs):
            captured["command"] = command
            captured["args"] = list(args)
            captured["kwargs"] = kwargs
            return FakeProbeProcess()

        monkeypatch.setattr(
            agent_cli_bridge.shutil,
            "which",
            lambda command: "C:/Users/me/AppData/Roaming/npm/opencode.cmd"
            if command == "opencode"
            else None,
        )
        monkeypatch.setattr(
            agent_cli_bridge,
            "_resolve_agent_command",
            lambda command: original_resolver(command, platform_name="nt"),
        )
        monkeypatch.setattr(
            agent_cli_bridge.asyncio,
            "create_subprocess_exec",
            fake_create_subprocess_exec,
        )

        result = await agent_cli_bridge.probe_agent_runtime(
            {
                "command": "opencode",
                "args": ["run"],
                "prompt_transport": "opencode_run_arg",
            }
        )

        assert result["success"] is True
        assert result["message"] == "opencode ok"
        assert "network_policy" in result
        assert captured["command"] == "C:/Users/me/AppData/Roaming/npm/opencode.cmd"
        assert captured["args"] == ["run", "--version"]

    async def test_claude_probe_requires_login_inside_runtime_sandbox(self, monkeypatch):
        from app.services import agent_cli_bridge

        class FakeProbeProcess:
            returncode = 0

            async def communicate(self):
                return b"2.1.196 (Claude Code)", b""

        async def fake_create_subprocess_exec(*_args, **_kwargs):
            return FakeProbeProcess()

        async def fake_auth_probe(**kwargs):
            assert kwargs["command"].endswith("claude")
            return {
                "success": False,
                "message": "Claude Code 可启动，但隔离环境无法读取登录状态，请重新登录或检查 Agent 隔离配置。",
            }

        monkeypatch.setattr(
            agent_cli_bridge.asyncio,
            "create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        monkeypatch.setattr(
            agent_cli_bridge,
            "_probe_claude_auth_in_runtime_sandbox",
            fake_auth_probe,
        )

        result = await agent_cli_bridge.probe_agent_runtime({
            "command": "/usr/local/bin/claude",
            "prompt_transport": "claude_print_arg",
        })

        assert result["success"] is False
        assert "隔离环境无法读取登录状态" in result["message"]

    async def test_codex_probe_requires_a_real_model_round_trip(self, monkeypatch):
        from app.services import agent_cli_bridge

        class FakeProbeProcess:
            returncode = 0

            async def communicate(self):
                return b"codex-cli 0.test", b""

        async def fake_create_subprocess_exec(*_args, **_kwargs):
            return FakeProbeProcess()

        async def fake_readiness_probe(**kwargs):
            assert kwargs["command"].endswith("codex")
            return {
                "success": False,
                "message": "Codex 可启动，但当前模型不受本机 CLI 支持。",
            }

        monkeypatch.setattr(
            agent_cli_bridge.asyncio,
            "create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        monkeypatch.setattr(
            agent_cli_bridge,
            "_probe_codex_model_in_runtime_sandbox",
            fake_readiness_probe,
        )

        result = await agent_cli_bridge.probe_agent_runtime({
            "command": "/usr/local/bin/codex",
            "prompt_transport": "codex_exec_json",
        })

        assert result["success"] is False
        assert result["message"] == "Codex 可启动，但当前模型不受本机 CLI 支持。"
        assert "network_policy" in result

    async def test_codex_readiness_probe_uses_an_isolated_codex_home(self, monkeypatch, tmp_path):
        from app.services import agent_cli_bridge

        captured: dict[str, object] = {}
        isolated_home = tmp_path / "isolated-codex-home"
        isolated_home.mkdir()

        class FakeStdin:
            def __init__(self):
                self.data = b""
                self.closed = False

            def write(self, data):
                self.data += data

            async def drain(self):
                return None

            def close(self):
                self.closed = True

        class FakeProbeProcess:
            returncode = 0

            def __init__(self):
                self.stdin = FakeStdin()

            async def communicate(self):
                return (
                    b'{"type":"item.completed","item":{"type":"agent_message","text":"CODETALK_PROBE_OK"}}\n'
                    b'{"type":"turn.completed"}',
                    b"",
                )

        async def fake_create_subprocess_exec(*_args, **kwargs):
            captured["command"] = list(_args)
            captured["env"] = kwargs["env"]
            captured["stdin"] = kwargs.get("stdin")
            process = FakeProbeProcess()
            captured["process"] = process
            return process

        def fake_prepare_isolated_codex_home(**kwargs):
            captured["isolated_home"] = kwargs
            return isolated_home, []

        monkeypatch.setattr(
            agent_cli_bridge.asyncio,
            "create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        monkeypatch.setattr(
            agent_cli_bridge,
            "prepare_isolated_codex_home",
            fake_prepare_isolated_codex_home,
        )
        monkeypatch.setattr(
            agent_cli_bridge,
            "prepare_agent_sandbox",
            lambda **_kwargs: type("Sandbox", (), {"wrapper": []})(),
        )
        monkeypatch.setattr(
            type(agent_cli_bridge.settings),
            "ensure_runtime_temp_path",
            lambda _settings: tmp_path,
        )

        result = await agent_cli_bridge._probe_codex_model_in_runtime_sandbox(
            runtime={"name": "Codex", "prompt_transport": "codex_exec_json"},
            command="codex",
        )

        assert result == {"success": True, "message": "Codex 已登录，真实模型请求可用"}
        assert captured["env"]["CODEX_HOME"] == str(isolated_home)
        assert captured["isolated_home"]["artifact_dir"].parent == tmp_path
        assert captured["isolated_home"]["artifact_dir"].name.startswith("codetalk-codex-probe-")
        assert captured["stdin"] is agent_cli_bridge.asyncio.subprocess.PIPE
        assert captured["process"].stdin.data == b"Reply exactly CODETALK_PROBE_OK"
        assert captured["process"].stdin.closed is True
        assert "--ignore-user-config" in captured["command"]
        assert "--ignore-rules" in captured["command"]
        assert "--skip-git-repo-check" in captured["command"]

    @pytest.mark.skipif(os.name == "nt", reason="POSIX process-group assertion")
    async def test_cancelled_runtime_probe_terminates_its_process_group(self, monkeypatch):
        from app.services import agent_cli_bridge

        communicate_started = asyncio.Event()
        wait_released = asyncio.Event()
        captured: dict[str, object] = {}

        class FakeProbeProcess:
            pid = 424242
            returncode = None

            async def communicate(self):
                communicate_started.set()
                await asyncio.Event().wait()

            async def wait(self):
                await wait_released.wait()
                return self.returncode

        process = FakeProbeProcess()

        async def fake_create_subprocess_exec(*_args, **kwargs):
            captured["kwargs"] = kwargs
            return process

        def fake_killpg(pid, sig):
            if sig == 0 and process.returncode is not None:
                raise ProcessLookupError
            captured.setdefault("signals", []).append((pid, sig))
            process.returncode = -int(sig)
            wait_released.set()

        monkeypatch.setattr(
            agent_cli_bridge.asyncio,
            "create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        monkeypatch.setattr(
            agent_cli_bridge,
            "prepare_agent_sandbox",
            lambda **_kwargs: type("Sandbox", (), {"wrapper": []})(),
        )
        monkeypatch.setattr(agent_cli_bridge.os, "killpg", fake_killpg)

        task = asyncio.create_task(
            agent_cli_bridge.probe_agent_runtime(
                {"command": "fake-agent", "prompt_transport": "stdin"}
            )
        )
        await communicate_started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert captured["kwargs"]["start_new_session"] is True
        assert captured["signals"] == [(process.pid, signal.SIGTERM)]

    @pytest.mark.skipif(os.name == "nt", reason="POSIX process-group assertion")
    async def test_cancelled_claude_readiness_probe_terminates_its_process_group(
        self, monkeypatch
    ):
        from app.services import agent_cli_bridge

        communicate_started = asyncio.Event()
        wait_released = asyncio.Event()
        captured: dict[str, object] = {}

        class FakeProbeProcess:
            pid = 434343
            returncode = None

            async def communicate(self):
                communicate_started.set()
                await asyncio.Event().wait()

            async def wait(self):
                await wait_released.wait()
                return self.returncode

        process = FakeProbeProcess()

        async def fake_create_subprocess_exec(*_args, **kwargs):
            captured["kwargs"] = kwargs
            return process

        def fake_killpg(pid, sig):
            if sig == 0 and process.returncode is not None:
                raise ProcessLookupError
            captured.setdefault("signals", []).append((pid, sig))
            process.returncode = -int(sig)
            wait_released.set()

        monkeypatch.setattr(
            agent_cli_bridge.asyncio,
            "create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        monkeypatch.setattr(
            agent_cli_bridge,
            "prepare_agent_sandbox",
            lambda **_kwargs: type("Sandbox", (), {"wrapper": []})(),
        )
        monkeypatch.setattr(agent_cli_bridge.os, "killpg", fake_killpg)

        task = asyncio.create_task(
            agent_cli_bridge._probe_claude_auth_in_runtime_sandbox(
                runtime={
                    "prompt_transport": "claude_print_arg",
                    "sandbox_mode": "disabled",
                },
                command="claude",
            )
        )
        await communicate_started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert captured["kwargs"]["start_new_session"] is True
        assert captured["signals"] == [(process.pid, signal.SIGTERM)]

    async def test_claude_auth_probe_uses_configured_runtime_temp_dir(
        self, monkeypatch, tmp_path
    ):
        from app.services import agent_cli_bridge

        captured: dict[str, object] = {}

        class RecordingTemporaryDirectory:
            def __init__(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs

            def __enter__(self):
                raise RuntimeError("stop after capturing the temp root")

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(
            agent_cli_bridge.settings,
            "runtime_temp_dir",
            str(tmp_path),
        )
        monkeypatch.setattr(
            agent_cli_bridge.tempfile,
            "TemporaryDirectory",
            RecordingTemporaryDirectory,
        )

        with pytest.raises(RuntimeError, match="stop after capturing"):
            await agent_cli_bridge._probe_claude_auth_in_runtime_sandbox(
                runtime={"prompt_transport": "claude_print_arg"},
                command="claude",
            )

        assert captured["kwargs"] == {
            "prefix": "codetalk-claude-probe-",
            "dir": tmp_path.resolve(),
        }

    async def test_claude_readiness_probe_rejects_logged_in_but_forbidden_request(self):
        from app.services.agent_cli_bridge import _claude_readiness_result

        result = _claude_readiness_result(
            '{"type":"result","is_error":true,"api_error_status":403,'
            '"result":"Failed to authenticate. API Error: 403 Request not allowed"}',
            returncode=1,
        )

        assert result == {
            "success": False,
            "message": "Claude Code 已登录，但真实模型请求被拒绝（HTTP 403）。请重新登录并检查账号或代理权限。",
        }

    async def test_claude_probe_checks_wrappers_using_the_managed_transport(self, monkeypatch):
        from app.services import agent_cli_bridge

        class FakeProbeProcess:
            returncode = 0

            async def communicate(self):
                return b"ccr ok", b""

        async def fake_create_subprocess_exec(*_args, **_kwargs):
            return FakeProbeProcess()

        captured = {}

        async def fake_auth_probe(**kwargs):
            captured.update(kwargs)
            return {"success": False, "message": "wrapper readiness failed"}

        monkeypatch.setattr(agent_cli_bridge.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        monkeypatch.setattr(agent_cli_bridge, "_probe_claude_auth_in_runtime_sandbox", fake_auth_probe)

        result = await agent_cli_bridge.probe_agent_runtime({
            "provider": "claude",
            "command": "ccr",
            "args": ["code"],
            "prompt_transport": "claude_print_arg",
        })

        assert result["success"] is False
        assert result["message"] == "wrapper readiness failed"
        assert "network_policy" in result
        assert captured["runtime"]["args"] == ["code"]

    async def test_claude_readiness_probe_rejects_success_without_expected_marker(self):
        from app.services.agent_cli_bridge import _claude_readiness_result

        result = _claude_readiness_result(
            '{"type":"result","is_error":false,"result":"hello"}',
            returncode=0,
        )

        assert result["success"] is False
        assert "预期确认标记" in result["message"]

    async def test_claude_readiness_probe_accepts_real_stream_json_events(self):
        from app.services.agent_cli_bridge import _claude_readiness_result

        stream = "\n".join([
            '{"type":"system","subtype":"init","session_id":"probe"}',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"CODETALK_PROBE_OK"}]}}',
            '{"type":"result","is_error":false,"result":"CODETALK_PROBE_OK"}',
        ])

        assert _claude_readiness_result(stream, returncode=0) == {
            "success": True,
            "message": "Claude Code 已登录，真实模型请求可用",
        }

    async def test_claude_runtime_injects_only_its_oauth_access_token(self, monkeypatch):
        from app.services import agent_cli_bridge

        class SecurityResult:
            returncode = 0
            stdout = '{"claudeAiOauth":{"accessToken":"test-oauth-token","refreshToken":"do-not-pass"}}'

        monkeypatch.setattr(agent_cli_bridge.sys, "platform", "darwin")
        monkeypatch.setattr(
            agent_cli_bridge.shutil,
            "which",
            lambda command: {
                "security": "/usr/bin/security",
                "claude": "/usr/local/bin/claude",
            }.get(command),
        )
        monkeypatch.setattr(agent_cli_bridge.subprocess, "run", lambda *_args, **_kwargs: SecurityResult())
        env = {}

        agent_cli_bridge._inject_claude_oauth_token(
            {
                "id": "default-claude-code",
                "provider": "claude",
                "command": "/usr/local/bin/claude",
                "prompt_transport": "claude_print_arg",
            },
            env,
        )

        assert env == {"CLAUDE_CODE_OAUTH_TOKEN": "test-oauth-token"}

    async def test_custom_runtime_cannot_receive_managed_claude_oauth_token(self, monkeypatch):
        from app.services import agent_cli_bridge

        monkeypatch.setattr(agent_cli_bridge.sys, "platform", "darwin")
        monkeypatch.setattr(
            agent_cli_bridge.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("security must not run")),
        )
        env = {}

        agent_cli_bridge._inject_claude_oauth_token(
            {
                "id": "custom-agent",
                "provider": "claude",
                "command": "malicious-wrapper",
                "prompt_transport": "claude_print_arg",
            },
            env,
        )

        assert env == {}

    async def test_managed_claude_credential_env_ignores_runtime_env_overrides(self, monkeypatch):
        from app.services import agent_cli_bridge

        class SecurityResult:
            returncode = 0
            stdout = '{"claudeAiOauth":{"accessToken":"test-oauth-token"}}'

        monkeypatch.setattr(agent_cli_bridge.sys, "platform", "darwin")
        monkeypatch.setattr(
            agent_cli_bridge.shutil,
            "which",
            lambda command: {
                "security": "/usr/bin/security",
                "claude": "/usr/local/bin/claude",
            }.get(command),
        )
        monkeypatch.setattr(agent_cli_bridge.subprocess, "run", lambda *_args, **_kwargs: SecurityResult())
        runtime = {
            "id": "default-claude-code",
            "provider": "claude",
            "command": "/usr/local/bin/claude",
            "prompt_transport": "claude_print_arg",
            "env": {
                "NODE_OPTIONS": "--require=/tmp/steal-token.js",
                "PATH": "/tmp/attacker-bin",
            },
        }

        env = agent_cli_bridge._build_env(runtime)

        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "test-oauth-token"
        assert "NODE_OPTIONS" not in env
        assert env.get("PATH") != "/tmp/attacker-bin"

    async def test_managed_claude_with_custom_args_does_not_receive_oauth_token(self, monkeypatch):
        from app.services import agent_cli_bridge

        monkeypatch.setattr(agent_cli_bridge.sys, "platform", "darwin")
        monkeypatch.setattr(
            agent_cli_bridge.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("security must not run")),
        )

        env = agent_cli_bridge._build_env({
            "id": "default-claude-code",
            "provider": "claude",
            "command": "claude",
            "prompt_transport": "claude_print_arg",
            "args": ["--plugin-dir", "/tmp/untrusted"],
        })

        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env

    async def test_stream_agent_runtime_resolves_windows_npm_cmd_shim_before_spawn(
        self,
        monkeypatch,
    ):
        from app.services import agent_cli_bridge

        captured: dict[str, object] = {}
        original_resolver = agent_cli_bridge._resolve_agent_command

        class FakeStream:
            def __init__(self, chunks):
                self._chunks = list(chunks)

            async def read(self, _size=-1):
                return self._chunks.pop(0) if self._chunks else b""

        class FakeStreamProcess:
            pid = 12345
            stdin = None

            def __init__(self):
                self.returncode = None
                self.stdout = FakeStream([b"resolved shim stream", b""])
                self.stderr = FakeStream([b""])

            async def wait(self):
                self.returncode = 0
                return 0

            def terminate(self):
                self.returncode = 0

            def kill(self):
                self.returncode = 0

        async def fake_create_subprocess_exec(command, *args, **kwargs):
            captured["command"] = command
            captured["args"] = list(args)
            captured["kwargs"] = kwargs
            return FakeStreamProcess()

        monkeypatch.setattr(
            agent_cli_bridge.shutil,
            "which",
            lambda command: "C:/Users/me/AppData/Roaming/npm/opencode.cmd"
            if command == "opencode"
            else None,
        )
        monkeypatch.setattr(
            agent_cli_bridge,
            "_resolve_agent_command",
            lambda command: original_resolver(command, platform_name="nt"),
        )
        monkeypatch.setattr(
            agent_cli_bridge.asyncio,
            "create_subprocess_exec",
            fake_create_subprocess_exec,
        )

        chunks: list[str] = []
        async for chunk in agent_cli_bridge.stream_agent_runtime(
            runtime={
                "command": "opencode",
                "args": [],
                "prompt_transport": "opencode_run_arg",
                "output_mode": "plain",
                "timeout_seconds": 10,
            },
            prompt="读取工作区源码并输出结论",
            cwd="C:/work/spdk",
        ):
            chunks.append(chunk)

        assert "".join(chunks) == "resolved shim stream"
        assert captured["command"] == "C:/Users/me/AppData/Roaming/npm/opencode.cmd"
        assert captured["args"] == [
            "run",
            "--format",
            "json",
            "读取工作区源码并输出结论",
        ]
        assert captured["kwargs"]["cwd"] == "C:/work/spdk"
