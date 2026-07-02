import asyncio
import json
import pathlib
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
            "print('AGENT_CWD_SOURCE_OK:' + os.getcwd())\n"
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
            "print('WORKBENCH_CWD_SOURCE_OK:' + os.getcwd())\n"
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
            "print('AGENT_SOURCE_FIRST_CONTRACT_OK')\n"
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
            assert "<redacted>" in serialized_run

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
            "print('最终答案：已经基于源码生成黑盒测试建议。')"
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
                json={"content": "分析 connect 黑盒测试"},
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
            assert "最终答案：已经基于源码生成黑盒测试建议。" in body["items"][1]["content"]
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
            assert "最终答案：NGA 已输出完整内容。" in assistant["content"]
            final_conversation = await client.get(f"/api/ai/conversations/{conversation.json()['id']}")
            assert final_conversation.json()["status"] == "idle"

    async def test_ai_thread_agent_runtime_resumes_saved_cli_session(self, sqlite_db):
        app = _test_app(sqlite_db)
        repo = pathlib.Path(sqlite_db).parent / "resume-repo"
        repo.mkdir()
        ws_id = await _seed_workspace(sqlite_db, "ws-agent-resume", repo_path=str(repo))
        agent_code = (
            "import json, sys; "
            "resume=''; "
            "args=sys.argv[1:]; "
            "resume=args[args.index('--resume') + 1] if '--resume' in args else ''; "
            "sys.stdin.read(); "
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

    async def test_ai_thread_agent_runtime_keeps_json_status_events_out_of_final_answer(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)
        agent_code = (
            "import json; "
            "print(json.dumps({'type':'status','message':'正在调用外部 agent 读取源码'}, ensure_ascii=False)); "
            "print(json.dumps({'content':'最终答案：外部 agent 已完成源码证据分析。'}, ensure_ascii=False))"
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
            assert "最终答案：外部 agent 已完成源码证据分析。" in body["items"][1]["content"]
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
            assert "最终答案：外部 agent 已恢复并完成源码证据分析。" in body["items"][1]["content"]
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
            "print(json.dumps({'type':'response.output_text.delta','delta':'最终答案：已完成可交付分析。'}, ensure_ascii=False)); "
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
            assert body["items"][1]["content"] == "最终答案：已完成可交付分析。"
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
                json={"content": "基于 nvmf auth 源码总结黑盒边界，不要输出源码全文"},
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
            assert "已折叠" in visible_stream
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
            assert "最终答案：已根据源码证据完成分析。" in body["items"][1]["content"]
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
            content="针对 iscsi 登录写几个黑盒用例",
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
        assert "TC-02 CHAP 失败" not in content
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
                    "answer = '## 黑盒测试用例\\n' + ''.join([f'{index}. TC-{index:02d} 正常登录变体：前置条件 target 已启动，步骤执行 iSCSI Login 场景 {index}，预期结果进入 Full Feature Phase 或返回明确 Login Response。\\n' for index in range(1, 9)])",
                    "events = [",
                    "  {'type':'system','subtype':'init','session_id':'claude-session'},",
                    "  {'type':'stream_event','event':{'type':'content_block_start','index':0,'content_block':{'type':'tool_result','tool_use_id':'toolu_1'}}},",
                    "  {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'1115:iscsi_conn_login_pdu_success_complete(void *arg)\\n'}}},",
                    "  {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'lib/iscsi/iscsi.c:1539:\\tAuthMethod=CHAP\\n'}}},",
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
            content="针对 iscsi 登录写几个黑盒用例",
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
        assert "TC-01 正常登录变体" not in assistant["content"]
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
            content="针对 iscsi 登录写几个黑盒用例",
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
            content="针对 iscsi 登录写几个黑盒用例",
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

    async def test_agent_runtime_output_parser_cleans_terminal_noise_and_unwraps_json(self):
        from app.services.agent_cli_bridge import _decode, _parse_event_text

        assert _parse_event_text("\x1b[32m正文片段\x1b[0m\r\n", "plain") == "正文片段"
        assert _parse_event_text("\r\x1b[2K⠋ 12\r\x1b[2K⠙ 47\r\x1b[2K最终答案\n", "plain") == "最终答案"
        assert _parse_event_text("\x1b(B最终答案：字符集切换噪声已清理\n", "plain") == "最终答案：字符集切换噪声已清理"
        assert _parse_event_text("1\n2\n47%\n12/100\n最终答案\n", "plain") == "最终答案"
        assert _parse_event_text("■■■■⬝⬝⬝⬝■■■■■⬝⬝⬝兼容\n", "plain") == "兼容"
        assert _decode("源码证据：连接失败".encode("gbk")) == "源码证据：连接失败"
        assert (
            _parse_event_text(
                json.dumps({"choices": [{"delta": {"content": "源码证据"}}]}, ensure_ascii=False),
                "stream_json",
            )
            == "源码证据"
        )
        assert (
            _parse_event_text(
                f"data: {json.dumps({'choices': [{'delta': {'content': 'SSE 源码证据'}}]}, ensure_ascii=False)}\n",
                "stream_json",
            )
            == "SSE 源码证据"
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

    async def test_agent_runtime_stream_uses_isolated_artifact_dir_by_default(self, tmp_path):
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
        assert pathlib.Path(artifact_dir, "result.json").exists()

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
            if transport == "claude_print_arg":
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

        assert answer == "最终答案：只展示源码分析结论。"
        assert "内部推理" not in answer
        assert "tool_result" not in answer
        assert "secret/path" not in answer
        assert "内部推理：先列出工具计划" in diagnostics
        assert "cat /secret/path returned internal-only trace" in diagnostics

    async def test_agent_runtime_plain_mode_drops_cli_banner_without_hiding_answer(self):
        from app.services.agent_cli_bridge import stream_agent_runtime

        agent_code = (
            "import sys; "
            "sys.stdout.write('Claude Code v1.2.3\\n'); "
            "sys.stdout.write('cwd: /tmp/project\\n'); "
            "sys.stdout.write('╭──────────────────────────────╮\\n'); "
            "sys.stdout.write('│ Thinking…                    │\\n'); "
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
        assert answer.strip() == "最终答案：只展示可交付正文。"
        assert "内部推理" not in answer
        assert "拒绝诊断" not in answer
        assert any("内部推理：先搜索源码。" in item for item in diagnostics)
        assert any("拒绝诊断：策略提示。" in item for item in diagnostics)

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
