import asyncio
import json
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


async def _seed_workspace(db_path: str, ws_id: str = "ws-agent") -> str:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
            "VALUES (?, 'Agent 项目', ?, 1, ?, ?)",
            (ws_id, "/tmp/codetalk-agent-project", now, now),
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
