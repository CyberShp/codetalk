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

    async def test_agent_runtime_output_parser_cleans_terminal_noise_and_unwraps_json(self):
        from app.services.agent_cli_bridge import _decode, _parse_event_text

        assert _parse_event_text("\x1b[32m正文片段\x1b[0m\r\n", "plain") == "正文片段"
        assert _parse_event_text("\r\x1b[2K⠋ 12\r\x1b[2K⠙ 47\r\x1b[2K最终答案\n", "plain") == "最终答案"
        assert _parse_event_text("1\n2\n47%\n12/100\n最终答案\n", "plain") == "最终答案"
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
