import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.database import get_db

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _seed_workspace(db_path: str, ws_id: str = "ws-ai") -> str:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
            "VALUES (?, 'AI 工作区', '/repo/project', 1, ?, ?)",
            (ws_id, now, now),
        )
        await db.execute(
            "INSERT INTO workspace_reports "
            "(id, workspace_id, report_type, title, content, status, created_at) "
            "VALUES (?, ?, 'test_design', '测试设计报告', '这里是报告正文：登录失败边界条件', 'completed', ?)",
            (f"report-{ws_id}", ws_id, now),
        )
        await db.commit()
    return ws_id


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield


def _test_app(sqlite_db: str) -> FastAPI:
    from app.api import ai_conversations

    app = FastAPI(lifespan=_lifespan)
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


class FakeLLM:
    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert "测试设计报告" in joined
        assert "登录失败边界条件" in joined
        yield "可以继续追问。"
        await asyncio.sleep(0)
        yield "建议补充异常路径和边界值。"


class SourceMaterialAssertingLLM:
    def __init__(self) -> None:
        self.joined = ""

    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        self.joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert "workspace_material" in self.joined
        assert "requirements.md" in self.joined
        assert "必须覆盖 reconnect timeout" in self.joined
        assert "workspace_source" in self.joined
        assert "lib/nvmf/connect.c" in self.joined
        assert "spdk_nvmf_connect_probe" in self.joined
        assert self.joined.index("workspace_material") < self.joined.index("workspace_report")
        assert self.joined.index("workspace_source") < self.joined.index("workspace_report")
        yield "已基于源码和材料回答。"


class HangingStreamLLM:
    def __init__(self):
        self.complete_called = False
        self.stream_called = False

    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        self.stream_called = True
        await asyncio.sleep(10)
        yield "unreachable"

    async def complete(self, messages, max_tokens=4096, temperature=0.3):
        from app.llm.base import LLMResponse

        self.complete_called = True
        return LLMResponse(content="非流式 fallback 已完成。", usage={"total_tokens": 3}, model="fake")


class BlockingStreamLLM:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        self.started.set()
        yield "第一段分析。"
        await self.release.wait()
        yield "最终结论。"


class TestAIConversationsAPI:
    async def test_create_and_list_project_scoped_conversations(self, sqlite_db):
        ws_a = await _seed_workspace(sqlite_db, "ws-a")
        ws_b = await _seed_workspace(sqlite_db, "ws-b")

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created_a = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_a,
                    "workspace_id": ws_a,
                    "memory_namespace": f"workspace:{ws_a}",
                    "title": "项目 A 线程",
                },
            )
            assert created_a.status_code == 201
            body_a = created_a.json()
            assert body_a["workspace_id"] == ws_a
            assert body_a["memory_namespace"] == f"workspace:{ws_a}"

            created_b = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_b,
                    "workspace_id": ws_b,
                    "memory_namespace": f"workspace:{ws_b}",
                    "title": "项目 B 线程",
                },
            )
            assert created_b.status_code == 201

            listed = await client.get("/api/ai/conversations", params={"workspace_id": ws_a})
            assert listed.status_code == 200
            items = listed.json()["items"]
            assert [item["id"] for item in items] == [body_a["id"]]

    async def test_workspace_thread_prioritizes_active_materials_and_source_refs(
        self,
        sqlite_db,
        tmp_path: Path,
        monkeypatch,
    ):
        repo = tmp_path / "repo"
        src = repo / "lib" / "nvmf"
        src.mkdir(parents=True)
        (src / "connect.c").write_text(
            "\n".join(
                [
                    "int spdk_nvmf_connect_probe(void) {",
                    "    return 42;",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        material = repo / "requirements.md"
        material.write_text("# 输入材料\n\n必须覆盖 reconnect timeout。\n", encoding="utf-8")
        ws_id = "ws-source-material"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Source Material WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.execute(
                "INSERT INTO workspace_materials (id, workspace_id, filename, content_type, file_path, is_active, created_at) "
                "VALUES ('mat-source', ?, 'requirements.md', 'requirements', ?, 1, ?)",
                (ws_id, str(material), now),
            )
            await db.execute(
                "INSERT INTO workspace_reports "
                "(id, workspace_id, report_type, title, content, status, created_at) "
                "VALUES ('report-source', ?, 'analysis', '旧报告', 'workspace_report should be lower priority', 'completed', ?)",
                (ws_id, now),
            )
            await db.commit()

        from app.api import ai_conversations

        fake_llm = SourceMaterialAssertingLLM()
        monkeypatch.setattr(ai_conversations, "create_llm_client_from_active", lambda: fake_llm)
        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/ai/conversations",
                json={"scope_type": "workspace", "scope_id": ws_id, "title": "源码材料优先"},
            )
            assert created.status_code == 201
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "请读取 lib/nvmf connect 并分析 reconnect timeout 测试"},
            )
            assert posted.status_code == 202
            refs = posted.json()["references"]
            assert [ref["source_type"] for ref in refs[:2]] == ["workspace_material", "workspace_source"]

            for _ in range(30):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assert "已基于源码和材料回答" in body["items"][1]["content"]

            stream = await client.get(
                f"/api/ai/conversations/{conversation['id']}/stream",
                params={"cursor": 0},
            )
            assert stream.status_code == 200
            events = [
                json.loads(line.removeprefix("data: "))
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
            status_messages = [
                event["payload"].get("message", "")
                for event in events
                if event["event_type"] == "status"
            ]
            assert any("工作区源码" in message and "输入材料" in message for message in status_messages)

    async def test_workspace_source_refs_follow_directory_path_hint(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        nvmf_dir = repo / "lib" / "nvmf"
        iscsi_dir = repo / "lib" / "iscsi"
        nvmf_dir.mkdir(parents=True)
        iscsi_dir.mkdir(parents=True)
        (repo / "README.md").write_text("top level overview should not win a directory-targeted query\n", encoding="utf-8")
        (nvmf_dir / "ctrlr.c").write_text(
            "\n".join(
                [
                    "int nvmf_dir_target(void) {",
                    "    return 1;",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        (iscsi_dir / "conn.c").write_text("int iscsi_unrelated(void) { return 0; }\n", encoding="utf-8")
        ws_id = "ws-dir-hint"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Directory Hint WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-dir-hint",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message="请分析 lib/nvmf 模块",
            db_path=sqlite_db,
        )
        source_refs = [ref for ref in refs if ref.source_type == "workspace_source"]

        assert source_refs
        assert source_refs[0].metadata["path"].startswith("lib/nvmf/")
        assert "nvmf_dir_target" in source_refs[0].excerpt
        assert all(not ref.metadata["path"].startswith("lib/iscsi/") for ref in source_refs[:2])

    async def test_legacy_conversation_backfills_workspace_namespace(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db, "legacy-ws")
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                """
                INSERT INTO ai_conversations
                    (id, scope_type, scope_id, title, status, initial_context_json, created_at, updated_at)
                VALUES (?, 'workspace', ?, '旧线程', 'idle', ?, ?, ?)
                """,
                (
                    "conv-legacy",
                    ws_id,
                    json.dumps({"workspace_id": ws_id}),
                    now,
                    now,
                ),
            )
            await db.commit()

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            fetched = await client.get("/api/ai/conversations/conv-legacy")
            assert fetched.status_code == 200
            body = fetched.json()
            assert body["workspace_id"] == ws_id
            assert body["memory_namespace"] == f"workspace:{ws_id}"

    async def test_context_recall_filters_evidence_memory_by_workspace(self, sqlite_db, monkeypatch):
        ws_id = await _seed_workspace(sqlite_db)
        calls: list[str | None] = []

        from app.services import evidence_memory
        from app.services.ai_conversations import build_context_references

        def fake_search(self, query, *, workspace_id=None, limit=3):
            calls.append(workspace_id)
            return []

        monkeypatch.setattr(evidence_memory.EvidenceMemoryStore, "search_analysis_memory", fake_search)

        refs = await build_context_references(
            conversation={
                "id": "conv-test",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message="登录失败边界",
            db_path=sqlite_db,
        )
        assert refs
        assert calls == [ws_id]

    async def test_create_message_stream_reconnect_and_context_refs(self, sqlite_db, monkeypatch):
        ws_id = await _seed_workspace(sqlite_db)

        from app.api import ai_conversations

        monkeypatch.setattr(
            ai_conversations,
            "create_llm_client_from_active",
            lambda: FakeLLM(),
        )

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "title": "登录问题分析",
                    "initial_context": {"source": "test"},
                },
            )
            assert created.status_code == 201
            conversation = created.json()
            assert conversation["scope_type"] == "workspace"
            assert conversation["status"] == "idle"

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "这个报告里的测试设计还缺什么？"},
            )
            assert posted.status_code == 202
            payload = posted.json()
            assert payload["run"]["status"] in {"queued", "running"}
            assert payload["references"][0]["source_type"] == "workspace_report"

            await asyncio.sleep(0.2)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            assert messages.status_code == 200
            body = messages.json()
            assert [m["role"] for m in body["items"]] == ["user", "assistant"]
            assert "异常路径" in body["items"][1]["content"]

            stream = await client.get(
                f"/api/ai/conversations/{conversation['id']}/stream",
                params={"cursor": 0},
            )
            assert stream.status_code == 200
            events = [
                json.loads(line.removeprefix("data: "))
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
            assert any(evt["event_type"] == "delta" for evt in events)
            last_id = max(evt["event_id"] for evt in events)

            reconnect = await client.get(
                f"/api/ai/conversations/{conversation['id']}/stream",
                params={"cursor": last_id},
            )
            assert reconnect.status_code == 200
            assert "data:" not in reconnect.text

    async def test_rejects_second_message_while_generation_is_running_without_duplication(
        self,
        sqlite_db,
        monkeypatch,
    ):
        ws_id = await _seed_workspace(sqlite_db)

        from app.api import ai_conversations

        fake_llm = BlockingStreamLLM()
        monkeypatch.setattr(
            ai_conversations,
            "create_llm_client_from_active",
            lambda: fake_llm,
        )

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "title": "并发提交保护",
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            first = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "先分析 SPDK nvmf connect 流程"},
            )
            assert first.status_code == 202
            await asyncio.wait_for(fake_llm.started.wait(), timeout=1)

            second = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "运行中再追问异常链路"},
            )
            assert second.status_code == 409
            assert second.json()["detail"] == "当前线程仍在生成中"

            fake_llm.release.set()
            for _ in range(20):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2 and items[-1]["role"] == "assistant":
                    break
                await asyncio.sleep(0.05)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            assert messages.status_code == 200
            body = messages.json()
            assert [m["role"] for m in body["items"]] == ["user", "assistant"]
            assert body["items"][0]["content"] == "先分析 SPDK nvmf connect 流程"
            assert "运行中再追问" not in json.dumps(body["items"], ensure_ascii=False)
            assert body["items"][1]["content"] == "第一段分析。最终结论。"

    async def test_cancel_running_generation_prevents_assistant_message_and_allows_retry(
        self,
        sqlite_db,
        monkeypatch,
    ):
        ws_id = await _seed_workspace(sqlite_db)

        from app.api import ai_conversations

        fake_llm = BlockingStreamLLM()
        monkeypatch.setattr(
            ai_conversations,
            "create_llm_client_from_active",
            lambda: fake_llm,
        )

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "title": "取消后重试",
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            first = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "先开始一个长分析"},
            )
            assert first.status_code == 202
            await asyncio.wait_for(fake_llm.started.wait(), timeout=1)

            cancelled = await client.post(f"/api/ai/conversations/{conversation['id']}/cancel")
            assert cancelled.status_code == 200
            assert cancelled.json()["run"]["status"] == "cancelled"
            fake_llm.release.set()
            await asyncio.sleep(0.05)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            assert messages.status_code == 200
            assert [m["role"] for m in messages.json()["items"]] == ["user"]

            second_llm = BlockingStreamLLM()
            monkeypatch.setattr(
                ai_conversations,
                "create_llm_client_from_active",
                lambda: second_llm,
            )
            retry = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "取消后重新分析异常恢复路径"},
            )
            assert retry.status_code == 202
            await asyncio.wait_for(second_llm.started.wait(), timeout=1)
            second_llm.release.set()

            for _ in range(20):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 3 and items[-1]["role"] == "assistant":
                    break
                await asyncio.sleep(0.05)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [m["role"] for m in body["items"]] == ["user", "user", "assistant"]
            assert body["items"][0]["content"] == "先开始一个长分析"
            assert body["items"][1]["content"] == "取消后重新分析异常恢复路径"
            assert body["items"][2]["content"] == "第一段分析。最终结论。"

    async def test_message_stream_timeout_falls_back_to_non_stream_completion(self, sqlite_db, monkeypatch):
        ws_id = await _seed_workspace(sqlite_db)

        from app.api import ai_conversations
        from app.services import ai_conversations as ai_service

        fake_llm = HangingStreamLLM()
        monkeypatch.setattr(ai_service.settings, "ai_conversation_stream_timeout_sec", 0.01)
        monkeypatch.setattr(
            ai_conversations,
            "create_llm_client_from_active",
            lambda: fake_llm,
        )

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "title": "fallback stream",
                },
            )
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "触发流式超时"},
            )
            assert posted.status_code == 202
            await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            assert messages.status_code == 200
            body = messages.json()
            assert fake_llm.complete_called is True
            assert [m["role"] for m in body["items"]] == ["user", "assistant"]
            assert "fallback 已完成" in body["items"][1]["content"]

    async def test_message_generation_can_disable_streaming_for_provider_compatibility(
        self,
        sqlite_db,
        monkeypatch,
    ):
        ws_id = await _seed_workspace(sqlite_db)

        from app.api import ai_conversations
        from app.services import ai_conversations as ai_service

        fake_llm = HangingStreamLLM()
        monkeypatch.setattr(ai_service.settings, "ai_conversation_streaming_enabled", False)
        monkeypatch.setattr(
            ai_conversations,
            "create_llm_client_from_active",
            lambda: fake_llm,
        )

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "title": "non-stream provider",
                },
            )
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "禁用流式生成"},
            )
            assert posted.status_code == 202
            await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            assert messages.status_code == 200
            body = messages.json()
            assert fake_llm.stream_called is False
            assert fake_llm.complete_called is True
            assert [m["role"] for m in body["items"]] == ["user", "assistant"]
            assert "fallback 已完成" in body["items"][1]["content"]
