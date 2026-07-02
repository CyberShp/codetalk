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
        assert "SOURCE_FIRST_CONTRACT" in self.joined
        assert "workspace_sources" in self.joined
        assert "workspace_materials" in self.joined
        assert "workspace_material" in self.joined
        assert "requirements.md" in self.joined
        assert "必须覆盖 reconnect timeout" in self.joined
        assert "workspace_source" in self.joined
        assert "lib/nvmf/connect.c" in self.joined
        assert "spdk_nvmf_connect_probe" in self.joined
        assert self.joined.index("workspace_material") < self.joined.index("workspace_report")
        assert self.joined.index("workspace_source") < self.joined.index("workspace_report")
        yield "已基于源码和材料回答。"


class WorkspaceBoundSourceAssertingLLM:
    def __init__(self) -> None:
        self.joined = ""

    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        self.joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert "workspace_source" in self.joined
        assert "lib/nvmf/connect.c" in self.joined
        assert "spdk_nvmf_workflow_scope_probe" in self.joined
        yield "已读取绑定工作区源码。"


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


class LongArtifactLLM:
    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        rows = [
            "| failure mode | cause | effect | detection | RPN |",
            "| --- | --- | --- | --- | --- |",
        ]
        rows.extend(
            f"| SFMEA 风险 {index} | 资源不足 | IO 失败 | 日志和指标 | {200 + index} |"
            for index in range(120)
        )
        yield "## SFMEA\n\n" + "\n".join(rows) + "\n\n## 黑盒测试用例\n\n"
        yield "\n".join(
            f"{index}. 前置条件：target 已启动。步骤：执行异常输入。预期结果：返回明确错误并记录日志。"
            for index in range(120)
        )


class MediumArtifactLLM:
    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        rows = [
            "| failure mode | cause | effect | detection | RPN |",
            "| --- | --- | --- | --- | --- |",
            "| SFMEA 风险 1 | reconnect timeout | I/O 暂停 | 日志和指标 | 180 |",
            "| SFMEA 风险 2 | reset race | session stale | RPC 状态 | 160 |",
            "| SFMEA 风险 3 | queue drain | request lost | poller latency | 144 |",
        ]
        cases = "\n".join(
            f"{index}. TC-{index:02d} 前置条件：target 已启动。步骤：执行异常输入 {index}。预期结果：返回明确错误并记录日志。"
            for index in range(1, 10)
        )
        yield "## SFMEA\n\n" + "\n".join(rows) + "\n\n## 黑盒测试用例\n\n" + cases


async def test_agent_output_segments_strip_terminal_noise_before_diagnostic_detection():
    from app.services.ai_conversations import _agent_output_segments

    segments = _agent_output_segments(
        "\x1b[2K\r47%\r\x1b[2Kthinking: 正在读取 lib/nvmf/connect.c\n"
        "12/100\n"
        "\x1b[32m最终答案：已基于工作区源码回答。\x1b[0m\n"
    )

    assert segments == [
        ("diagnostic", "正在读取 lib/nvmf/connect.c"),
        ("answer", "最终答案：已基于工作区源码回答。\n"),
    ]


async def test_agent_output_segments_keep_chinese_answer_while_dropping_terminal_noise():
    from app.services.ai_conversations import _agent_output_segments

    segments = _agent_output_segments(
        "\x1b[32m47%\n"
        "12/100\n"
        "\ufffd\ufffd\ufffd\ufffd\n"
        "\r\x1b[2K⠋ 12\r\x1b[2K⠙ 47\r\x1b[2K"
        "\x1b(B"
        "diagnostic: provider emitted transient status\n"
        "源码证据：连接失败\n"
        "FINAL_NOISE_CLEAN_ANSWER: 已完成源码分析。\x1b[0m\n"
    )

    assert segments == [
        ("diagnostic", "provider emitted transient status"),
        ("answer", "源码证据：连接失败\n"),
        ("answer", "FINAL_NOISE_CLEAN_ANSWER: 已完成源码分析。\n"),
    ]


async def test_agent_output_segments_apply_backspace_repaints_before_filtering_progress_noise():
    from app.services.ai_conversations import _agent_output_segments

    segments = _agent_output_segments(
        "thinking: scanning workspace source\n"
        "progress 000\b\b\b47%\n"
        "progress \b47%\n"
        "读取中 000\b\b\b12/100\n"
        "读取中 \b12/100\n"
        "源码证据：lib/nvmf/connect.c\n"
        "FINAL_BACKSPACE_CLEAN_ANSWER: 已完成源码分析。\n"
    )

    assert segments == [
        ("diagnostic", "scanning workspace source"),
        ("answer", "源码证据：lib/nvmf/connect.c\n"),
        ("answer", "FINAL_BACKSPACE_CLEAN_ANSWER: 已完成源码分析。\n"),
    ]


async def test_plain_long_agent_answer_stays_inline_in_thread_reader():
    from app.services.ai_conversations import _should_materialize_thread_artifact

    plain_long_answer = "\n".join(
        f"HISTORY-LINE-{index:02d} earlier evidence and reasoning that remains readable during generation"
        for index in range(1, 140)
    )

    assert len(plain_long_answer) > 7200
    assert _should_materialize_thread_artifact(plain_long_answer) is False


async def test_agent_output_segments_fold_indented_diagnostic_continuations():
    from app.services.ai_conversations import _agent_output_segments

    segments = _agent_output_segments(
        "thinking: planning source read\n"
        "  internal step 1: inspect lib/nvmf/connect.c\n"
        "  internal step 2: decide risk scoring\n"
        "FINAL_MULTILINE_DIAGNOSTIC_ANSWER: 已给出可见结论。\n"
    )

    assert segments == [
        (
            "diagnostic",
            "planning source read\ninternal step 1: inspect lib/nvmf/connect.c\ninternal step 2: decide risk scoring",
        ),
        ("answer", "FINAL_MULTILINE_DIAGNOSTIC_ANSWER: 已给出可见结论。\n"),
    ]


async def test_agent_output_segments_fold_unindented_tool_result_source_lines():
    from app.services.ai_conversations import _agent_output_segments

    segments = _agent_output_segments(
        "TOOL: 1115:iscsi_conn_login_pdu_err_complete(void *arg)\n"
        "1125:iscsi_conn_login_pdu_success_complete(void *arg)\n"
        "lib/iscsi/iscsi.c:1539:\t\trc = iscsi_op_login_update_param(conn, \"AuthMethod\", \"CHAP\", \"CHAP\");\n"
        "\n"
        "## 黑盒测试用例\n"
        "### TC-01 正常登录\n"
    )

    assert segments == [
        (
            "diagnostic",
            "1115:iscsi_conn_login_pdu_err_complete(void *arg)\n"
            "1125:iscsi_conn_login_pdu_success_complete(void *arg)\n"
            "lib/iscsi/iscsi.c:1539:\t\trc = iscsi_op_login_update_param(conn, \"AuthMethod\", \"CHAP\", \"CHAP\");",
        ),
        ("answer", "## 黑盒测试用例\n"),
        ("answer", "### TC-01 正常登录\n"),
    ]


async def test_agent_output_segments_keep_final_answer_after_json_tool_parts():
    from app.services.agent_cli_bridge import AGENT_FINAL_ANSWER_PREFIX
    from app.services.ai_conversations import _agent_output_segments

    segments = _agent_output_segments(
        f"{AGENT_FINAL_ANSWER_PREFIX}"
        "THINKING: 内部推理：先列出工具计划\n"
        "TOOL: cat /secret/path returned internal-only trace\n"
        "FINAL_JSON_PARTS_ANSWER: 只展示源码分析结论。\n"
    )

    assert segments == [
        ("diagnostic", "内部推理：先列出工具计划"),
        ("diagnostic", "cat /secret/path returned internal-only trace"),
        ("answer", "FINAL_JSON_PARTS_ANSWER: 只展示源码分析结论。\n"),
    ]


async def test_agent_output_segments_fold_thinking_source_dump_without_hiding_answer_heading():
    from app.services.ai_conversations import _agent_output_segments

    segments = _agent_output_segments(
        "THINKING: 我先核对工作区 iSCSI 登录相关源码。\n"
        "1125:iscsi_conn_login_pdu_success_complete(void *arg)\n"
        "1149:iscsi_op_login_response(struct spdk_iscsi_conn *conn,\n"
        "\n"
        "## 结论\n"
        "已基于源码整理黑盒测试思路。\n"
    )

    assert segments == [
        (
            "diagnostic",
            "我先核对工作区 iSCSI 登录相关源码。\n"
            "1125:iscsi_conn_login_pdu_success_complete(void *arg)\n"
            "1149:iscsi_op_login_response(struct spdk_iscsi_conn *conn,",
        ),
        ("answer", "## 结论\n"),
        ("answer", "已基于源码整理黑盒测试思路。\n"),
    ]


async def test_context_status_message_names_workbench_task_artifacts():
    from app.services.ai_conversations import _context_status_message

    message = _context_status_message(
        [
            {
                "source_type": "workbench_task_artifact",
                "source_id": "task_run_1/task_artifact_manifest.json",
                "title": "task_artifact_manifest.json",
            }
        ]
    )

    assert "任务产物" in message
    assert "未找到直接匹配" not in message


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

    async def test_delete_conversation_removes_idle_thread_and_rejects_running_thread(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        store_path = sqlite_db
        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(store_path)
        idle = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="可删除线程",
        )
        running = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="运行中线程",
        )
        await store.create_user_message_and_run(
            conversation_id=running["id"],
            content="还在运行",
            references=[],
        )

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            blocked = await client.delete(f"/api/ai/conversations/{running['id']}")
            assert blocked.status_code == 409
            assert "仍在生成" in blocked.text

            deleted = await client.delete(f"/api/ai/conversations/{idle['id']}")
            assert deleted.status_code == 204

            missing = await client.get(f"/api/ai/conversations/{idle['id']}")
            assert missing.status_code == 404

    async def test_store_rejects_new_run_when_conversation_already_has_active_run(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Agent session chain guard",
        )
        first = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="第一轮：启动 agent 分析源码",
            references=[],
        )
        assert first["run"]["status"] == "queued"

        with pytest.raises(ValueError, match="当前线程仍在生成中"):
            await store.create_user_message_and_run(
                conversation_id=conversation["id"],
                content="第二轮：不应绕过 SessionChain 串行保护",
                references=[],
            )

        messages = await store.list_messages(conversation["id"])
        assert [item["content"] for item in messages] == ["第一轮：启动 agent 分析源码"]

    async def test_create_workbench_conversation_publicizes_artifact_context(self, sqlite_db):
        task_run_id = "task_run_public_context"
        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workbench_task_run",
                    "scope_id": task_run_id,
                    "workspace_id": "ws-workbench",
                    "memory_namespace": "workspace:ws-workbench",
                    "title": "Workbench AI 复盘",
                    "initial_context": {
                        "workspace_id": "ws-workbench",
                        "repo_path": "/Volumes/Media/dpdk/spdk",
                        "artifact_dir": (
                            f"/Volumes/Media/codetalk/data/workbench/task_runs/{task_run_id}"
                        ),
                        "agent_runs": [
                            {
                                "step_id": "discover",
                                "artifact_dir": (
                                    "/Volumes/Media/codetalk/data/workbench/task_runs/"
                                    f"{task_run_id}/agent_runs/discover"
                                ),
                            },
                            {
                                "step_id": "external",
                                "artifact_dir": "E:/data/workbench/task_runs/other/agent_runs/external",
                            },
                        ],
                    },
                },
            )

        assert created.status_code == 201
        context = created.json()["initial_context"]
        assert context["repo_path"] == "/Volumes/Media/dpdk/spdk"
        assert context["artifact_dir"] == "."
        assert context["agent_runs"][0]["artifact_dir"] == "agent_runs/discover"
        assert context["agent_runs"][1]["artifact_dir"] == ""

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
            assert str(repo) not in json.dumps(refs, ensure_ascii=False)
            assert str(material) not in json.dumps(refs, ensure_ascii=False)
            for ref in refs:
                metadata = ref.get("metadata") or {}
                assert "repo_path" not in metadata
                assert "file_path" not in metadata

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

    async def test_workspace_thread_prioritizes_gitnexus_and_cgc_report_artifacts(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        src = repo / "lib" / "nvmf"
        src.mkdir(parents=True)
        (src / "ctrlr.c").write_text(
            "int spdk_nvmf_cgc_priority_probe(void) { return 0; }\n",
            encoding="utf-8",
        )
        ws_id = "ws-gitnexus-cgc"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'GitNexus CGC WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            for report_id, report_type, title, content in [
                ("report-normal", "test_design", "普通测试设计", "普通报告不应排在图谱前"),
                ("report-gitnexus", "gitnexus_reliability", "GitNexus 可信度评估", "GitNexus community and ownership evidence"),
                ("report-cgc", "cgc_call_graph", "CGC 调用图产物", "CGC connect to io submit call chain"),
            ]:
                await db.execute(
                    "INSERT INTO workspace_reports "
                    "(id, workspace_id, report_type, title, content, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 'completed', ?)",
                    (report_id, ws_id, report_type, title, content, now),
                )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-gitnexus-cgc",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message="梳理 connect 到 IO submit 的测试风险",
            db_path=sqlite_db,
        )
        report_refs = [ref for ref in refs if ref.source_type == "workspace_report"]
        assert [ref.metadata["report_type"] for ref in report_refs[:2]] == [
            "gitnexus_reliability",
            "cgc_call_graph",
        ]
        assert "GitNexus" in report_refs[0].excerpt
        assert "CGC" in report_refs[1].excerpt

    async def test_agent_prompt_defaults_to_gitnexus_cgc_source_artifact_priority(self):
        from app.services.ai_conversations import _build_agent_prompt

        prompt = _build_agent_prompt(
            {
                "id": "conv-source-artifact-priority",
                "title": "图谱优先",
                "scope_type": "workspace",
                "scope_id": "ws-source-artifact-priority",
                "workspace_id": "ws-source-artifact-priority",
                "initial_context": {},
            },
            [{"role": "user", "content": "梳理 NVMe-oF connect 流程"}],
            [
                {
                    "source_type": "workspace_report",
                    "source_id": "report-gitnexus",
                    "title": "GitNexus 可信度评估",
                    "excerpt": "GitNexus community and ownership evidence",
                    "metadata": {"report_type": "gitnexus_reliability"},
                },
                {
                    "source_type": "workspace_report",
                    "source_id": "report-cgc",
                    "title": "CGC 调用图产物",
                    "excerpt": "CGC connect call chain",
                    "metadata": {"report_type": "cgc_call_graph"},
                },
            ],
            "梳理 NVMe-oF connect 流程",
            {"id": "runtime-source-artifact-priority", "name": "Runtime"},
        )

        assert "SOURCE_ARTIFACT_PRIORITY" in prompt
        assert "GitNexus" in prompt
        assert "CGC" in prompt
        assert "除非用户明确要求不要基于源码" in prompt

    async def test_agent_prompt_honors_explicit_no_source_analysis_request(self):
        from app.services.ai_conversations import _build_agent_prompt

        prompt = _build_agent_prompt(
            {
                "id": "conv-no-source",
                "title": "不基于源码",
                "scope_type": "workspace",
                "scope_id": "ws-no-source",
                "workspace_id": "ws-no-source",
                "initial_context": {},
            },
            [{"role": "user", "content": "不要基于源码，只根据我给的描述回答"}],
            [],
            "不要基于源码，只根据我给的描述回答",
            {"id": "runtime-no-source", "name": "Runtime"},
        )

        assert "source_analysis_declined: true" in prompt
        assert "不要强制查 GitNexus/CGC 或工作区源码" in prompt

    async def test_context_references_skip_source_and_graph_artifacts_when_source_declined(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        src = repo / "lib" / "nvmf"
        src.mkdir(parents=True)
        (src / "ctrlr.c").write_text(
            "int spdk_nvmf_declined_source_probe(void) { return 0; }\n",
            encoding="utf-8",
        )
        ws_id = "ws-decline-source"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Decline Source WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.execute(
                "INSERT INTO workspace_reports "
                "(id, workspace_id, report_type, title, content, status, created_at) "
                "VALUES ('report-decline-gitnexus', ?, 'gitnexus_reliability', 'GitNexus 可信度评估', "
                "'GitNexus evidence should not be injected', 'completed', ?)",
                (ws_id, now),
            )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-decline-source",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message="不要基于源码，只根据我给的描述回答",
            db_path=sqlite_db,
        )

        source_types = {ref.source_type for ref in refs}
        assert "workspace_source" not in source_types
        assert "workspace_report" not in source_types

    async def test_workspace_bound_non_workspace_thread_reads_workspace_source(
        self,
        sqlite_db,
        tmp_path: Path,
        monkeypatch,
    ):
        repo = tmp_path / "repo"
        source = repo / "lib" / "nvmf" / "connect.c"
        source.parent.mkdir(parents=True)
        source.write_text(
            "\n".join(
                [
                    "int spdk_nvmf_workflow_scope_probe(void) {",
                    "    return 42;",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        ws_id = "ws-workflow-source"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Workflow Source WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.commit()

        from app.api import ai_conversations

        fake_llm = WorkspaceBoundSourceAssertingLLM()
        monkeypatch.setattr(ai_conversations, "create_llm_client_from_active", lambda: fake_llm)
        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workflow",
                    "scope_id": "module_analysis",
                    "workspace_id": ws_id,
                    "memory_namespace": f"workspace:{ws_id}",
                    "title": "工作流范围源码优先",
                },
            )
            assert created.status_code == 201
            conversation = created.json()
            assert conversation["workspace_id"] == ws_id

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "请读取 lib/nvmf/connect.c 并分析 connect 流程"},
            )
            assert posted.status_code == 202
            refs = posted.json()["references"]
            source_refs = [ref for ref in refs if ref["source_type"] == "workspace_source"]
            assert source_refs
            assert source_refs[0]["metadata"]["workspace_id"] == ws_id
            assert source_refs[0]["metadata"]["path"] == "lib/nvmf/connect.c"
            assert "repo_path" not in source_refs[0]["metadata"]

            for _ in range(30):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 2:
                    break
                await asyncio.sleep(0.1)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "assistant"]
            assert "已读取绑定工作区源码" in body["items"][1]["content"]

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
        assert "repo_path" not in source_refs[0].metadata
        assert "nvmf_dir_target" in source_refs[0].excerpt
        assert all(not ref.metadata["path"].startswith("lib/iscsi/") for ref in source_refs[:2])

    async def test_agent_prompt_uses_public_workspace_label_without_absolute_repo_path(self):
        from app.services.ai_conversations import _build_agent_prompt

        prompt = _build_agent_prompt(
            {
                "id": "conv-public-prompt",
                "title": "公开路径 prompt",
                "scope_type": "workspace",
                "scope_id": "ws-public-prompt",
                "workspace_id": "ws-public-prompt",
                "initial_context": {"repo_path": "/Volumes/Media/dpdk/spdk"},
            },
            [
                {
                    "role": "user",
                    "content": "读取 lib/nvmf/connect.c",
                }
            ],
            [
                {
                    "source_type": "workspace_source",
                    "source_id": "ws-public-prompt:lib/nvmf/connect.c:1-3",
                    "title": "lib/nvmf/connect.c:1",
                    "excerpt": "1: int spdk_public_path_probe(void) { return 1; }",
                    "metadata": {
                        "workspace_id": "ws-public-prompt",
                        "path": "lib/nvmf/connect.c",
                        "start_line": 1,
                        "end_line": 3,
                    },
                }
            ],
            "读取 lib/nvmf/connect.c",
            {"id": "runtime-public", "name": "Runtime Public"},
            repo_path="/Volumes/Media/dpdk/spdk",
        )

        assert "workspace:ws-public-prompt" in prompt
        assert "lib/nvmf/connect.c" in prompt
        assert "spdk_public_path_probe" in prompt
        assert "/Volumes/Media/dpdk/spdk" not in prompt

    async def test_directory_path_hint_prefers_implementation_over_docs(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        nvmf_dir = repo / "lib" / "nvmf"
        nvmf_dir.mkdir(parents=True)
        (nvmf_dir / "README.md").write_text(
            "directory overview should not be the first source reference\n",
            encoding="utf-8",
        )
        (nvmf_dir / "ctrlr.c").write_text(
            "\n".join(
                [
                    "int nvmf_directory_impl_priority(void) {",
                    "    return 1;",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        ws_id = "ws-dir-impl-priority"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Directory Impl Priority WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-dir-impl-priority",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message="请读取 lib/nvmf 并梳理主流程",
            db_path=sqlite_db,
        )
        source_refs = [ref for ref in refs if ref.source_type == "workspace_source"]

        assert source_refs
        assert source_refs[0].metadata["path"] == "lib/nvmf/ctrlr.c"
        assert "nvmf_directory_impl_priority" in source_refs[0].excerpt
        assert all(not ref.metadata["path"].endswith(".md") for ref in source_refs[:1])

    async def test_module_thread_uses_scope_path_as_source_hint_when_prompt_is_vague(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        nvmf_dir = repo / "lib" / "nvmf"
        bdev_dir = repo / "lib" / "bdev"
        nvmf_dir.mkdir(parents=True)
        bdev_dir.mkdir(parents=True)
        (bdev_dir / "bdev.c").write_text(
            "int bdev_generic_entry(void) { return 0; }\n",
            encoding="utf-8",
        )
        (nvmf_dir / "ctrlr.c").write_text(
            "\n".join(
                [
                    "int nvmf_scope_path_entry(void) {",
                    "    return 1;",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        ws_id = "ws-module-scope-source"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Module Scope Source WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-module-scope-source",
                "scope_type": "module",
                "scope_id": f"{ws_id}:lib/nvmf",
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message="请分析这个模块的主流程和外部可观测行为",
            db_path=sqlite_db,
        )
        source_refs = [ref for ref in refs if ref.source_type == "workspace_source"]

        assert source_refs
        assert source_refs[0].metadata["path"].startswith("lib/nvmf/")
        assert "nvmf_scope_path_entry" in source_refs[0].excerpt
        assert all(not ref.metadata["path"].startswith("lib/bdev/") for ref in source_refs[:2])

    async def test_workbench_task_thread_references_task_artifact_manifest(
        self,
        sqlite_db,
        tmp_path: Path,
        monkeypatch,
    ):
        from app.config import settings
        from app.services.ai_conversations import build_context_references

        data_root = tmp_path / "data"
        task_run_id = "task_run_ai_manifest"
        task_dir = data_root / "workbench" / "task_runs" / task_run_id
        task_dir.mkdir(parents=True)
        (task_dir / "task_run.json").write_text(
            json.dumps({"task_run_id": task_run_id, "status": "prepared"}),
            encoding="utf-8",
        )
        (task_dir / "task_bundle.json").write_text(
            json.dumps({"workflow_id": "module_analysis", "repo_path": "/repo/spdk"}),
            encoding="utf-8",
        )
        (task_dir / "workflow_execution.json").write_text(
            json.dumps({"task_run_id": task_run_id, "status": "completed"}),
            encoding="utf-8",
        )
        (task_dir / "task_artifact_manifest.json").write_text(
            json.dumps(
                {
                    "task_run_id": task_run_id,
                    "artifacts": [
                        {
                            "relative_path": "task_bundle.json",
                            "kind": "task_bundle",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (task_dir / "artifact_manifest.json").write_text(
            json.dumps({"legacy": True, "task_run_id": task_run_id}),
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "data_dir", str(data_root))

        refs = await build_context_references(
            conversation={
                "id": "conv-workbench-manifest",
                "scope_type": "workbench_task_run",
                "scope_id": task_run_id,
                "workspace_id": "ws-workbench",
                "memory_namespace": "workspace:ws-workbench",
                "initial_context": {"workspace_id": "ws-workbench"},
            },
            user_message="请读取本次任务产物清单并复盘",
            db_path=sqlite_db,
        )
        manifest_refs = [
            ref
            for ref in refs
            if ref.source_type == "workbench_task_artifact"
            and ref.title == "task_artifact_manifest.json"
        ]

        assert manifest_refs
        assert manifest_refs[0].source_id == f"{task_run_id}/task_artifact_manifest.json"
        assert manifest_refs[0].metadata["path"] == "task_artifact_manifest.json"
        assert not Path(str(manifest_refs[0].metadata["path"])).is_absolute()
        assert "task_bundle.json" in manifest_refs[0].excerpt

    async def test_workbench_task_thread_uses_task_repo_for_source_refs_when_workspace_row_is_missing(
        self,
        sqlite_db,
        tmp_path: Path,
        monkeypatch,
    ):
        from app.config import settings
        from app.services.ai_conversations import build_context_references

        repo = tmp_path / "spdk"
        source = repo / "lib" / "nvmf" / "connect.c"
        source.parent.mkdir(parents=True)
        source.write_text(
            "int nvmf_workbench_review_source_probe(void) { return 7; }\n",
            encoding="utf-8",
        )
        data_root = tmp_path / "data"
        task_run_id = "task_run_source_fallback"
        task_dir = data_root / "workbench" / "task_runs" / task_run_id
        task_dir.mkdir(parents=True)
        (task_dir / "task_run.json").write_text(
            json.dumps(
                {
                    "task_run_id": task_run_id,
                    "workflow_id": "module_analysis",
                    "workspace_id": "ws-workbench-missing-row",
                    "repo_path": str(repo),
                    "artifact_dir": str(task_dir),
                    "agent_runs": [],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "data_dir", str(data_root))

        refs = await build_context_references(
            conversation={
                "id": "conv-workbench-source-fallback",
                "scope_type": "workbench_task_run",
                "scope_id": task_run_id,
                "workspace_id": "ws-workbench-missing-row",
                "memory_namespace": "workspace:ws-workbench-missing-row",
                "initial_context": {
                    "workspace_id": "ws-workbench-missing-row",
                    "repo_path": f"repo:{repo.name}",
                },
            },
            user_message="读取 lib/nvmf/connect.c 并复盘源码证据",
            db_path=sqlite_db,
        )

        source_refs = [ref for ref in refs if ref.source_type == "workspace_source"]
        assert source_refs
        assert source_refs[0].metadata["path"] == "lib/nvmf/connect.c"
        assert "nvmf_workbench_review_source_probe" in source_refs[0].excerpt
        assert "repo_path" not in source_refs[0].metadata

    async def test_workspace_source_refs_fallback_prefers_implementation_source(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        (repo / "docs").mkdir(parents=True)
        (repo / "lib" / "nvmf").mkdir(parents=True)
        (repo / "README.md").write_text(
            "overview document should not be the primary source snippet\n",
            encoding="utf-8",
        )
        (repo / "docs" / "guide.md").write_text(
            "documentation should not displace implementation source\n",
            encoding="utf-8",
        )
        (repo / "lib" / "nvmf" / "connect.c").write_text(
            "\n".join(
                [
                    "int nvmf_connect_primary_flow(void) {",
                    "    return 7;",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        (repo / "lib" / "nvmf" / "transport.c").write_text(
            "int nvmf_transport_secondary_flow(void) { return 8; }\n",
            encoding="utf-8",
        )
        ws_id = "ws-generic-source"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Generic Source WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-generic-source",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message="请先读取工作区源码，再分析主要连接流程",
            db_path=sqlite_db,
        )
        source_refs = [ref for ref in refs if ref.source_type == "workspace_source"]

        assert source_refs
        assert source_refs[0].metadata["path"] == "lib/nvmf/connect.c"
        assert "nvmf_connect_primary_flow" in source_refs[0].excerpt
        assert all(not ref.metadata["path"].endswith(".md") for ref in source_refs[:2])

    async def test_workspace_source_refs_chinese_generic_blackbox_query_prefers_storage_core(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        (repo / "doc").mkdir(parents=True)
        (repo / "go" / "rpc" / "client").mkdir(parents=True)
        (repo / "lib" / "nvmf").mkdir(parents=True)
        (repo / "lib" / "bdev").mkdir(parents=True)
        (repo / "doc" / "two.min.js").write_text(
            "function t(a){return a}/* minified doc helper */\n",
            encoding="utf-8",
        )
        (repo / "go" / "rpc" / "client" / "client.go").write_text(
            "package client\nfunc createRequest() {}\n",
            encoding="utf-8",
        )
        (repo / "lib" / "nvmf" / "ctrlr.c").write_text(
            "\n".join([
                "int nvmf_ctrlr_blackbox_boundary_probe(void) {",
                "    return 1;",
                "}",
            ]),
            encoding="utf-8",
        )
        (repo / "lib" / "bdev" / "bdev.c").write_text(
            "int bdev_boundary_probe(void) { return 2; }\n",
            encoding="utf-8",
        )
        ws_id = "ws-chinese-blackbox-source"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Chinese Blackbox WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-chinese-blackbox-source",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message="补充其中一个模块的黑盒边界条件和异常路径",
            db_path=sqlite_db,
        )
        source_refs = [ref for ref in refs if ref.source_type == "workspace_source"]

        assert source_refs
        assert source_refs[0].metadata["path"] == "lib/nvmf/ctrlr.c"
        assert "nvmf_ctrlr_blackbox_boundary_probe" in source_refs[0].excerpt
        assert all(not ref.metadata["path"].startswith("doc/") for ref in source_refs[:2])
        assert all(not ref.metadata["path"].startswith("go/rpc/") for ref in source_refs[:2])

    async def test_storage_domain_terms_prioritize_matching_workspace_module(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        (repo / "lib" / "misc").mkdir(parents=True)
        (repo / "lib" / "nvmf").mkdir(parents=True)
        (repo / "lib" / "misc" / "connect.c").write_text(
            "int unrelated_connect_helper(void) { return 0; }\n",
            encoding="utf-8",
        )
        (repo / "lib" / "nvmf" / "ctrlr.c").write_text(
            "\n".join(
                [
                    "int nvmf_io_path(void) {",
                    "    return 1;",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        ws_id = "ws-storage-domain-nvmf"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Storage Domain WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-storage-domain-nvmf",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message="分析 SPDK NVMe-oF target connect 到 IO 提交流程",
            db_path=sqlite_db,
        )
        source_refs = [ref for ref in refs if ref.source_type == "workspace_source"]

        assert source_refs
        assert source_refs[0].metadata["path"].startswith("lib/nvmf/")
        assert "nvmf_io_path" in source_refs[0].excerpt

    async def test_storage_domain_directory_hint_prefers_query_matching_source_file(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        nvmf_dir = repo / "lib" / "nvmf"
        nvmf_dir.mkdir(parents=True)
        (nvmf_dir / "admin.c").write_text(
            "int nvmf_admin_unrelated(void) { return 0; }\n",
            encoding="utf-8",
        )
        (nvmf_dir / "connect.c").write_text(
            "\n".join(
                [
                    "int nvmf_connect_target_flow(void) {",
                    "    return 1;",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        ws_id = "ws-storage-domain-connect"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Storage Connect WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-storage-domain-connect",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message="分析 SPDK NVMe-oF target connect 到 IO 提交流程",
            db_path=sqlite_db,
        )
        source_refs = [ref for ref in refs if ref.source_type == "workspace_source"]

        assert source_refs
        assert source_refs[0].metadata["path"] == "lib/nvmf/connect.c"
        assert "nvmf_connect_target_flow" in source_refs[0].excerpt

    async def test_storage_domain_directory_hint_prefers_exact_symbol_definition(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        nvmf_dir = repo / "lib" / "nvmf"
        nvmf_dir.mkdir(parents=True)
        (nvmf_dir / "admin.c").write_text(
            "\n".join(
                [
                    "int nvmf_admin_first_alphabetically(void) {",
                    "    return 0;",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        (nvmf_dir / "connect.c").write_text(
            "\n".join(
                [
                    "int spdk_nvmf_connect(void) {",
                    "    return 1;",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        ws_id = "ws-storage-domain-symbol"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Storage Symbol WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-storage-domain-symbol",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message="分析 SPDK NVMe-oF spdk_nvmf_connect 函数流程",
            db_path=sqlite_db,
        )
        source_refs = [ref for ref in refs if ref.source_type == "workspace_source"]

        assert source_refs
        assert source_refs[0].metadata["path"] == "lib/nvmf/connect.c"
        assert "spdk_nvmf_connect" in source_refs[0].excerpt

    async def test_storage_domain_path_hints_cover_spdk_workflow_modules(self):
        from app.services.ai_conversations import _storage_domain_path_hints

        cases = {
            "iSCSI login CHAP digest 异常链路": ["lib/iscsi"],
            "bdev IO submit complete 错误返回": ["lib/bdev"],
            "blobstore metadata 恢复和空间不足": ["lib/blob", "test/blobstore"],
            "FTL 异常关闭恢复": ["lib/ftl"],
            "vhost device lifecycle guest detach": ["lib/vhost"],
            "vfio-user queue 配置": ["lib/vfio-user"],
            "reactor poller 跨线程调度": ["lib/event"],
            "thread poller 阻塞": ["lib/thread"],
            "RPC config 非法参数": ["lib/rpc"],
        }

        for query, expected_paths in cases.items():
            hints = _storage_domain_path_hints(query)
            for expected_path in expected_paths:
                assert expected_path in hints

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

    async def test_legacy_workbench_conversation_read_publicizes_artifact_context(
        self,
        sqlite_db,
    ):
        task_run_id = "task_run_legacy_context"
        now = datetime.now(timezone.utc).isoformat()
        legacy_context = {
            "workspace_id": "legacy-workbench",
            "repo_path": "/Volumes/Media/dpdk/spdk",
            "artifact_dir": f"/Volumes/Media/codetalk/data/workbench/task_runs/{task_run_id}",
            "agent_runs": [
                {
                    "step_id": "discover",
                    "artifact_dir": (
                        "/Volumes/Media/codetalk/data/workbench/task_runs/"
                        f"{task_run_id}/agent_runs/discover"
                    ),
                }
            ],
        }
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                """
                INSERT INTO ai_conversations
                    (id, scope_type, scope_id, workspace_id, memory_namespace, title, status,
                     initial_context_json, created_at, updated_at)
                VALUES (?, 'workbench_task_run', ?, 'legacy-workbench',
                        'workspace:legacy-workbench', '旧 Workbench 线程', 'idle', ?, ?, ?)
                """,
                (
                    "conv-legacy-workbench",
                    task_run_id,
                    json.dumps(legacy_context),
                    now,
                    now,
                ),
            )
            await db.commit()

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            fetched = await client.get("/api/ai/conversations/conv-legacy-workbench")
            listed = await client.get(
                "/api/ai/conversations",
                params={"workspace_id": "legacy-workbench"},
            )

        assert fetched.status_code == 200
        context = fetched.json()["initial_context"]
        assert context["repo_path"] == "/Volumes/Media/dpdk/spdk"
        assert context["artifact_dir"] == "."
        assert context["agent_runs"][0]["artifact_dir"] == "agent_runs/discover"
        assert listed.status_code == 200
        listed_context = listed.json()["items"][0]["initial_context"]
        assert listed_context["artifact_dir"] == "."
        assert listed_context["agent_runs"][0]["artifact_dir"] == "agent_runs/discover"

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

    async def test_long_sfmea_and_blackbox_output_materializes_downloadable_artifact(
        self,
        sqlite_db,
        monkeypatch,
    ):
        ws_id = await _seed_workspace(sqlite_db)

        from app.api import ai_conversations

        monkeypatch.setattr(
            ai_conversations,
            "create_llm_client_from_active",
            lambda: LongArtifactLLM(),
        )

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "title": "长产物线程",
                },
            )
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "生成完整 SFMEA 和黑盒测试用例"},
            )
            assert posted.status_code == 202
            for _ in range(60):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                body = messages.json()
                if len(body["items"]) == 2:
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("assistant message was not generated")

            assistant = body["items"][1]
            assert "完整测试设计/SFMEA/黑盒用例已保存为下载产物" in assistant["content"]
            assert len(assistant["content"]) < 4500
            download_action = next(
                action for action in assistant["actions"] if action["id"] == "download_run_artifact"
            )
            artifact = await client.get(download_action["href"])
            assert artifact.status_code == 200
            artifact_text = artifact.text
            assert "# 长产物线程" in artifact_text
            assert "SFMEA 风险 119" in artifact_text
            assert "黑盒测试用例" in artifact_text

    async def test_structured_sfmea_and_blackbox_output_prefers_compact_download_delivery(
        self,
        sqlite_db,
        monkeypatch,
    ):
        ws_id = await _seed_workspace(sqlite_db)

        from app.api import ai_conversations

        monkeypatch.setattr(
            ai_conversations,
            "create_llm_client_from_active",
            lambda: MediumArtifactLLM(),
        )

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "title": "结构化产物线程",
                },
            )
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "生成完整 SFMEA 和黑盒测试用例"},
            )
            assert posted.status_code == 202
            for _ in range(60):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                body = messages.json()
                if len(body["items"]) == 2:
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("assistant message was not generated")

            assistant = body["items"][1]
            assert "已保存为下载产物" in assistant["content"]
            assert "TC-09" not in assistant["content"]
            assert "SFMEA 风险 3" not in assistant["content"]
            download_action = next(
                action for action in assistant["actions"] if action["id"] == "download_run_artifact"
            )
            artifact = await client.get(download_action["href"])
            assert artifact.status_code == 200
            artifact_text = artifact.text
            assert "# 结构化产物线程" in artifact_text
            assert "SFMEA 风险 3" in artifact_text
            assert "TC-09" in artifact_text

    async def test_list_run_events_returns_recent_redacted_agent_process(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="Agent 过程恢复线程",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="分析源码",
            references=[],
        )
        run_id = created["run"]["id"]
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"kind": "diagnostic", "content": "TOOL: rg login sk-test-secret-123456"},
        )
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"content": "最终回答。"},
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": run_id, "limit": 10},
            )

        assert response.status_code == 200
        body = response.json()
        assert [item["run_id"] for item in body["items"]]
        diagnostic = next(
            item for item in body["items"] if item["payload"].get("kind") == "diagnostic"
        )
        assert "TOOL: rg login" in diagnostic["payload"]["content"]
        assert "sk-test-secret-123456" not in diagnostic["payload"]["content"]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            missing = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": "run_missing"},
            )
        assert missing.status_code == 404

    async def test_process_only_run_events_keep_diagnostics_when_answer_stream_is_long(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="长线程过程恢复",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="分析源码并生成大量用例",
            references=[],
        )
        run_id = created["run"]["id"]
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="status",
            payload={"status": "running", "message": "正在读取工作区源码上下文。"},
        )
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"kind": "diagnostic", "content": "TOOL: rg iscsi_login lib/iscsi"},
        )
        for index in range(260):
            await store.append_event(
                run_id=run_id,
                conversation_id=conversation["id"],
                event_type="delta",
                payload={"content": f"answer chunk {index}\n"},
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            normal = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": run_id, "limit": 200},
            )
            process = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": run_id, "limit": 200, "process_only": True},
            )

        assert normal.status_code == 200
        assert not any(
            item["payload"].get("kind") == "diagnostic"
            for item in normal.json()["items"]
        )
        assert process.status_code == 200
        process_items = process.json()["items"]
        assert [item["event_type"] for item in process_items] == ["status", "status", "delta"]
        assert process_items[0]["payload"]["message"] == "已进入生成队列，正在准备上下文。"
        assert process_items[1]["payload"]["message"] == "正在读取工作区源码上下文。"
        assert process_items[2]["payload"]["content"] == "TOOL: rg iscsi_login lib/iscsi"

    async def test_legacy_agent_process_leak_is_hidden_from_messages_and_artifact(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)

        from app.services.ai_conversations import (
            AIConversationStore,
            ai_thread_artifact_path,
        )

        legacy_content = "\n".join(
            [
                "THINKING: 我先核对工作区 iSCSI 登录相关源码。",
                "1125:iscsi_conn_login_pdu_success_complete(void *arg)",
                "1149:iscsi_op_login_response(struct spdk_iscsi_conn *conn,",
                "1539:\t\trc = iscsi_op_login_update_param(conn, \"AuthMethod\", \"CHAP\", \"CHAP\");",
                "THINKING: 我已掌握登录处理链的关键分支。",
                "## 结论",
                "这是一段旧版流式残片，格式已被工具输出打断。",
                "我已掌握登录处理链的关键分支。下面基于 `lib/iscsi/iscsi.c` 给出黑盒用例。",
                "## 结论",
                "SPDK iSCSI 登录处理应覆盖正常登录、目标不存在、访问控制、CHAP 失败和异常 PDU。",
                "## 黑盒测试用例",
                "### TC-01 正常会话登录成功",
                "前置条件：target 已启动；步骤：initiator 发起 Normal 登录；预期：进入 Full Feature Phase。",
            ]
        )

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="旧版污染线程",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="针对 iscsi 登录写几个黑盒用例",
            references=[],
        )
        run_id = created["run"]["id"]
        await store.complete_run(
            run_id=run_id,
            content=legacy_content,
            references=[],
            model="agent:legacy",
            actions=[
                {
                    "id": "download_run_artifact",
                    "label": "下载完整产物",
                    "href": f"/api/ai/conversations/{conversation['id']}/runs/{run_id}/artifact",
                    "kind": "download",
                }
            ],
        )
        artifact_path = ai_thread_artifact_path(conversation["id"], run_id)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            "\n".join(
                [
                    "# 旧版污染线程",
                    "",
                    f"- conversation_id: {conversation['id']}",
                    f"- run_id: {run_id}",
                    "- exported_at: 2026-07-02T00:00:00+00:00",
                    "",
                    legacy_content,
                    "",
                ]
            ),
            encoding="utf-8",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            artifact = await client.get(
                f"/api/ai/conversations/{conversation['id']}/runs/{run_id}/artifact"
            )

        assert messages.status_code == 200
        assistant = messages.json()["items"][1]
        assert "## 结论" in assistant["content"]
        assert "TC-01 正常会话登录成功" in assistant["content"]
        assert "THINKING:" not in assistant["content"]
        assert "iscsi_conn_login_pdu_success_complete" not in assistant["content"]
        assert "旧版流式残片" not in assistant["content"]

        assert artifact.status_code == 200
        artifact_text = artifact.text
        assert "# 旧版污染线程" in artifact_text
        assert "## 黑盒测试用例" in artifact_text
        assert "TC-01 正常会话登录成功" in artifact_text
        assert "THINKING:" not in artifact_text
        assert "iscsi_conn_login_pdu_success_complete" not in artifact_text
        assert "旧版流式残片" not in artifact_text
        rewritten_text = artifact_path.read_text(encoding="utf-8")
        assert rewritten_text == artifact_text
        assert "THINKING:" not in rewritten_text
        assert "iscsi_conn_login_pdu_success_complete" not in rewritten_text

    async def test_truncated_legacy_agent_preview_falls_back_to_safe_placeholder(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)

        from app.services.ai_conversations import AIConversationStore

        truncated_preview = "\n".join(
            [
                "THINKING: 我先核对工作区 iSCSI 登录相关源码。",
                "1125:iscsi_conn_login_pdu_success_complete(void *arg)",
                "1149:iscsi_op_login_response(struct spdk_iscsi_conn *conn,",
                "1153:\tstruct iscsi_bhs_login_rsp *rsph;",
                "1539:\t\trc = iscsi_op_login_update_param(conn, \"AuthMethod\", \"CHAP\", \"CHAP\");",
                "lib/iscsi/iscsi.c:1455:iscsi_op_login_check_session(struct spdk_iscsi_conn *conn,",
                "",
                "---",
                "内容较长，已折叠为下载产物。",
            ]
        )

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="旧版截断预览线程",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="针对 iscsi 登录写几个黑盒用例",
            references=[],
        )
        await store.complete_run(
            run_id=created["run"]["id"],
            content=truncated_preview,
            references=[],
            model="agent:legacy",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")

        assert messages.status_code == 200
        assistant = messages.json()["items"][1]
        assert "CodeTalk 已折叠旧版 Agent 过程输出" in assistant["content"]
        assert "THINKING:" not in assistant["content"]
        assert "iscsi_conn_login_pdu_success_complete" not in assistant["content"]
        assert "AuthMethod" not in assistant["content"]
