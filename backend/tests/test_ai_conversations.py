import asyncio
import json
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
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


async def test_retired_builtin_workflow_cannot_start_new_ai_conversation_runs(
    sqlite_db,
):
    from app.services.ai_conversations import AIConversationStore

    app = _test_app(sqlite_db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        rejected_create = await client.post(
            "/api/ai/conversations",
            json={
                "scope_type": "workspace",
                "scope_id": "ws-retired-workflow",
                "initial_context": {"selected_workflow_id": "module_analysis"},
            },
        )

        historical = await AIConversationStore(sqlite_db).create_conversation(
            scope_type="workspace",
            scope_id="ws-retired-workflow",
            title="历史旧工作流线程",
            initial_context={"selected_workflow_id": "module_analysis"},
        )
        rejected_message = await client.post(
            f"/api/ai/conversations/{historical['id']}/messages",
            json={"content": "请启动旧模块分析流程"},
        )

    assert rejected_create.status_code == 410
    assert rejected_message.status_code == 410
    assert "已下线" in str(rejected_create.json()["detail"])
    assert "已下线" in str(rejected_message.json()["detail"])


async def test_archived_custom_workflow_cannot_start_new_ai_conversation_runs(
    sqlite_db,
):
    from app.config import settings
    from app.services.ai_conversations import AIConversationStore
    from app.services.workflow_version_store import WorkflowVersionStore

    workflow_id = "archived-custom-ai"
    version_store = WorkflowVersionStore(settings.data_path / "workbench" / "workflows.db")
    version_store.create_workflow(
        workflow_id=workflow_id,
        name="Archived custom AI workflow",
        description="",
        authoring_graph={"schema_version": 2, "workflow_id": workflow_id},
    )
    version_store.archive_workflow(workflow_id)

    app = _test_app(sqlite_db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        rejected_create = await client.post(
            "/api/ai/conversations",
            json={
                "scope_type": "workspace",
                "scope_id": "ws-archived-custom",
                "initial_context": {"selected_workflow_id": workflow_id},
            },
        )

        historical = await AIConversationStore(sqlite_db).create_conversation(
            scope_type="workspace",
            scope_id="ws-archived-custom",
            title="历史归档自建工作流线程",
            initial_context={"selected_workflow_id": workflow_id},
        )
        rejected_message = await client.post(
            f"/api/ai/conversations/{historical['id']}/messages",
            json={"content": "请继续运行这个归档工作流"},
        )

    assert rejected_create.status_code == 409
    assert rejected_message.status_code == 409
    assert "已归档" in str(rejected_create.json()["detail"])
    assert "已归档" in str(rejected_message.json()["detail"])


class FakeLLM:
    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert "测试设计报告" in joined
        assert "登录失败边界条件" in joined
        yield "可以继续追问。"
        await asyncio.sleep(0)
        yield "建议补充异常路径和边界值。"


class TruncatedTestActivityLLM:
    def __init__(self) -> None:
        self.max_tokens = 0
        self.temperature = 0.0

    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        from app.llm.base import current_finish_reason

        self.max_tokens = max_tokens
        self.temperature = temperature
        yield "## 代码证据\n\n- `lib/iscsi/iscsi.c`: login 入口。\n\n## 流程步骤\n\n1. 建立连接"
        current_finish_reason.set("length")


class ShallowCompletedTestActivityLLM:
    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        from app.llm.base import current_finish_reason

        yield "## 结论\n\n已完成 iSCSI login 测试设计。"
        current_finish_reason.set("stop")


class StagedTestActivityLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, messages, max_tokens=4096, temperature=0.2):
        from app.llm.base import LLMResponse

        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        stage = next(
            line.split(":", 1)[1].strip()
            for line in prompt.splitlines()
            if line.startswith("STAGE_ID:")
        )
        if stage == "source_analysis":
            content = "# 代码证据\n\n- `lib/iscsi/iscsi.c:1262`\n"
        elif stage == "business_flow":
            content = "# Flow\n\n## 外部触发\nPDU\n## 流程步骤\nlogin\n## 异常分支\ntimeout\n## 观测点\nlog"
        elif stage == "sfmea":
            content = '[{"failure_mode":"timeout","cause":"peer silent"}]'
        elif stage == "black_box_cases":
            content = '[{"case_id":"TC-01","test_dimension":"normal_path"}]'
        else:
            content = "# Test design\n\n## 目标\nlogin\n## 输入\nPDU\n## 用例设计\nTC\n## 覆盖矩阵\nflow\n## 剩余风险\nlab"
        return LLMResponse(content=content, model="staged-test", usage={}, truncated=False)


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


class MultiConversationBlockingLLM:
    def __init__(self):
        self.release = asyncio.Event()
        self.started: dict[str, asyncio.Event] = {
            "thread-a": asyncio.Event(),
            "thread-b": asyncio.Event(),
        }

    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        label = "thread-a" if "线程 A" in joined else "thread-b"
        self.started[label].set()
        yield f"{label} 第一段。"
        await self.release.wait()
        yield f"{label} 完成。"


class LongArtifactLLM:
    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        rows = [
            "| failure_mode | cause | effect | detection | severity | occurrence | detection_score | RPN | score_explanation | mitigation | source_evidence | test_mapping |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        rows.extend(
            f"| SFMEA 风险 {index} | 资源不足 | IO 失败 | 日志和指标 | 8 | 3 | 4 | 96 | 严重度高且可探测 | 增加恢复验证 | `lib/iscsi/iscsi.c` | `test/iscsi_tgt/chap/chap.sh` |"
            for index in range(120)
        )
        dimensions = (
            "normal_path",
            "invalid_input",
            "resource_pressure",
            "timeout",
            "reconnect",
            "concurrency",
            "recovery",
            "performance",
        )
        yield "## SFMEA\n\n" + "\n".join(rows) + "\n\n## 黑盒测试用例\n\n"
        yield "\n".join(
            f"{index}. TC-{index:03d} {dimensions[index % len(dimensions)]}。前置条件：target 已启动。"
            "步骤：initiator 执行登录。预期结果：返回明确状态。观测点：登录响应和 SPDK 日志。"
            "失败诊断线索：关联 session 日志。证据：`test/iscsi_tgt/chap/chap.sh`。"
            for index in range(120)
        )


class MediumArtifactLLM:
    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        rows = [
            "| failure_mode | cause | effect | detection | severity | occurrence | detection_score | RPN | score_explanation | mitigation | source_evidence | test_mapping |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            "| SFMEA 风险 1：reconnect timeout | 网络中断 | I/O 暂停 | 日志和指标 | 8 | 3 | 4 | 96 | 恢复受阻 | 限制重试 | `lib/iscsi/iscsi.c` | `test/iscsi_tgt/chap/chap.sh` |",
            "| SFMEA 风险 2：reset race | 并发关闭 | session stale | RPC 状态 | 7 | 3 | 4 | 84 | 状态残留 | 增加并发回归 | `lib/iscsi/iscsi.c` | `test/iscsi_tgt/chap/chap.sh` |",
            "| SFMEA 风险 3：queue drain | 资源压力 | request lost | poller latency | 9 | 2 | 4 | 72 | 数据面影响 | 增加资源监控 | `lib/iscsi/iscsi.c` | `test/iscsi_tgt/chap/chap.sh` |",
        ]
        dimensions = (
            "normal_path",
            "invalid_input",
            "resource_pressure",
            "timeout",
            "reconnect",
            "concurrency",
            "recovery",
            "performance",
        )
        cases = "\n".join(
            f"{index}. TC-{index:02d} {dimension}。前置条件：target 已启动。步骤：initiator 执行登录。"
            "预期结果：返回明确状态。观测点：登录响应和 SPDK 日志。失败诊断线索：关联 session 日志。"
            "证据：`test/iscsi_tgt/chap/chap.sh`。"
            for index, dimension in enumerate((*dimensions, "normal_path"), start=1)
        )
        yield "## SFMEA\n\n" + "\n".join(rows) + "\n\n## 黑盒测试用例\n\n" + cases


class ShortSourceBlackBoxArtifactLLM:
    async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
        yield (
            "## 代码证据\n"
            "- `lib/iscsi/iscsi.c:1539`: CHAP AuthMethod 协商路径。\n"
            "- `test/iscsi_tgt`: 可承载登录黑盒回归。\n\n"
            "## 黑盒测试用例\n"
            "### TC-01 正常登录\n"
            "前置条件：target 已启动；步骤：initiator 发起 iSCSI Login；"
            "预期结果：进入 Full Feature Phase；观测点：Login Response、session 状态和日志。\n\n"
            "### TC-02 CHAP 失败\n"
            "前置条件：target 开启 CHAP；步骤：使用错误 secret 登录；"
            "预期结果：Login Response 拒绝；观测点：认证失败日志和连接状态。\n"
        )


async def test_agent_output_segments_strip_terminal_noise_before_diagnostic_detection():
    from app.services.ai_conversations import _agent_output_segments

    segments = _agent_output_segments(
        "\x1b[2K\r47%\r\x1b[2Kthinking: 正在读取 lib/nvmf/connect.c\n"
        "12/100\n"
        "\x1b[32m最终答案：已基于工作区源码回答。\x1b[0m\n"
    )

    assert segments == [
        ("diagnostic", "正在读取 lib/nvmf/connect.c"),
        ("answer", "已基于工作区源码回答。\n"),
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


async def test_agent_output_segments_keep_diagnostic_context_across_stream_chunks():
    from app.services.ai_conversations import _AgentOutputSegmentState, _agent_output_segments

    state = _AgentOutputSegmentState()

    first_segments = _agent_output_segments(
        "thinking: planning source read\n"
        "  internal step 1: inspect lib/nvmf/connect.c\n",
        state=state,
    )
    second_segments = _agent_output_segments(
        "  internal step 2: decide risk scoring\n"
        "FINAL_STREAM_DIAGNOSTIC_ANSWER: 已给出可见结论。\n",
        state=state,
    )

    assert first_segments == [
        (
            "diagnostic",
            "planning source read\ninternal step 1: inspect lib/nvmf/connect.c",
        ),
    ]
    assert second_segments == [
        ("diagnostic", "internal step 2: decide risk scoring"),
        ("answer", "FINAL_STREAM_DIAGNOSTIC_ANSWER: 已给出可见结论。\n"),
    ]
    assert state.diagnostic_active is False
    assert state.diagnostic_prefix == ""


async def test_agent_output_segments_keep_split_thinking_text_out_of_visible_answer():
    from app.services.ai_conversations import _AgentOutputSegmentState, _agent_output_segments

    state = _AgentOutputSegmentState()

    segments: list[tuple[str, str]] = []
    for chunk in [
        "THINKING: ",
        "我先核对工作区 iSCSI 登录相关源码，再",
        "据此设计黑盒用例。",
        "\n",
        "## 黑盒测试用例\n",
        "### TC-01 正常登录\n",
    ]:
        segments.extend(_agent_output_segments(chunk, state=state))

    assert segments == [
        ("diagnostic", "我先核对工作区 iSCSI 登录相关源码，再"),
        ("diagnostic", "据此设计黑盒用例。"),
        ("answer", "## 黑盒测试用例\n"),
        ("answer", "### TC-01 正常登录\n"),
    ]
    assert state.diagnostic_active is False
    assert state.diagnostic_prefix == ""


async def test_agent_output_segments_do_not_trust_answer_delta_prefix_for_process_lines():
    from app.services.agent_cli_bridge import AGENT_ANSWER_DELTA_PREFIX, AGENT_FINAL_ANSWER_PREFIX
    from app.services.ai_conversations import _AgentOutputSegmentState, _agent_output_segments

    state = _AgentOutputSegmentState()

    segments: list[tuple[str, str]] = []
    for chunk in [
        f"{AGENT_ANSWER_DELTA_PREFIX}THINKING: ",
        f"{AGENT_ANSWER_DELTA_PREFIX}我先核对工作区 iSCSI 登录相关源码，再",
        f"{AGENT_ANSWER_DELTA_PREFIX}据此设计黑盒用例。",
        f"{AGENT_ANSWER_DELTA_PREFIX}Bash {{\"command\": \"grep -n login lib/iscsi/iscsi.c | head -60\"}}",
        f"{AGENT_ANSWER_DELTA_PREFIX}1125:iscsi_conn_login_pdu_success_complete(void *arg)\n",
        (
            f"{AGENT_FINAL_ANSWER_PREFIX}我已掌握登录处理链的关键分支。"
            "下面基于 `lib/iscsi/iscsi.c` 的实际校验逻辑给出黑盒用例。\n"
            "## 黑盒测试用例\n"
            "### TC-01 正常登录\n"
        ),
    ]:
        segments.extend(_agent_output_segments(chunk, state=state))

    visible_answer = "".join(content for kind, content in segments if kind == "answer")
    diagnostics = "\n".join(content for kind, content in segments if kind == "diagnostic")

    assert "## 黑盒测试用例" in visible_answer
    assert "TC-01 正常登录" in visible_answer
    assert "THINKING" not in visible_answer
    assert "我先核对工作区" not in visible_answer
    assert "Bash" not in visible_answer
    assert "iscsi_conn_login_pdu_success_complete" not in visible_answer
    assert "我先核对工作区 iSCSI 登录相关源码" in diagnostics
    assert "Bash" in diagnostics
    assert "iscsi_conn_login_pdu_success_complete" in diagnostics


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


async def test_agent_output_segments_fold_bare_tool_result_source_lines_before_answer():
    from app.services.ai_conversations import _agent_output_segments

    segments = _agent_output_segments(
        "1125:iscsi_conn_login_pdu_success_complete(void *arg)\n"
        "lib/iscsi/iscsi.c:1539:\t\trc = iscsi_op_login_update_param(conn, \"AuthMethod\", \"CHAP\", \"CHAP\");\n"
        "AuthMethod=CHAP\n"
        "\n"
        "## 黑盒测试用例\n"
        "### TC-01 正常登录\n"
    )

    assert segments == [
        (
            "diagnostic",
            "1125:iscsi_conn_login_pdu_success_complete(void *arg)\n"
            "lib/iscsi/iscsi.c:1539:\t\trc = iscsi_op_login_update_param(conn, \"AuthMethod\", \"CHAP\", \"CHAP\");\n"
            "AuthMethod=CHAP",
        ),
        ("answer", "## 黑盒测试用例\n"),
        ("answer", "### TC-01 正常登录\n"),
    ]


async def test_agent_output_segments_fold_shell_prompt_transcript_before_answer():
    from app.services.ai_conversations import _agent_output_segments

    segments = _agent_output_segments(
        "$ rg nvmf_ctrlr_shell_probe lib/nvmf\n"
        "lib/nvmf/ctrlr.c:1:int nvmf_ctrlr_shell_probe(void) { return 0; }\n"
        "exit_code=0\n"
        "\n"
        "## 结论\n"
        "SHELL_TRANSCRIPT_FINAL: 已基于源码输出结论。\n"
    )

    assert segments == [
        (
            "diagnostic",
            "$ rg nvmf_ctrlr_shell_probe lib/nvmf\n"
            "lib/nvmf/ctrlr.c:1:int nvmf_ctrlr_shell_probe(void) { return 0; }\n"
            "exit_code=0",
        ),
        ("answer", "## 结论\n"),
        ("answer", "SHELL_TRANSCRIPT_FINAL: 已基于源码输出结论。\n"),
    ]


async def test_agent_output_segments_strip_final_answer_wrappers():
    from app.services.ai_conversations import _agent_output_segments

    segments = _agent_output_segments(
        "Final answer: 已完成源码分析。\n"
        "FINAL ANSWER:\n"
        "## 结论\n"
        "WRAPPED_FINAL_MARKER_CLEAN: 只显示回答正文。\n"
        "最终答案：中文包装也会被剥离。\n"
    )

    assert segments == [
        ("answer", "已完成源码分析。\n"),
        ("answer", "## 结论\n"),
        ("answer", "WRAPPED_FINAL_MARKER_CLEAN: 只显示回答正文。\n"),
        ("answer", "中文包装也会被剥离。\n"),
    ]


async def test_public_agent_run_error_translates_internal_stream_parser_failure():
    from app.services.ai_conversations import _public_agent_run_error

    message = _public_agent_run_error(
        "Separator is not found, and chunk exceed the limit"
    )

    assert message == (
        "执行器返回了过大的单条过程事件，CodeTalk 未能完成解析。"
        "请重试本轮；若仍失败，请切换执行器或减少单次输出。"
    )
    assert "Separator" not in message


async def test_public_agent_run_error_keeps_local_path_in_folded_diagnostics_only():
    from app.services.ai_conversations import _public_agent_run_error

    message = _public_agent_run_error(
        "启动执行器失败：[Errno 13] Permission denied: '/Users/test/private-agent'"
    )

    assert message == "执行器启动失败。请检查设置中的命令、工作目录和执行权限后重试。"
    assert "/Users/test" not in message


async def test_public_agent_run_error_only_allows_exact_controlled_timeout_text():
    from app.services.ai_conversations import _public_agent_run_error

    assert _public_agent_run_error("执行器超时（900s）") == "执行器超时（900s）"

    spoofed = _public_agent_run_error(
        "执行器超时：读取 /Users/alice/.config/codex/auth.json 失败"
    )
    assert spoofed == "执行器运行失败。请展开 Agent 过程查看内部诊断，然后重试或切换执行器。"
    assert "/Users/alice" not in spoofed


async def test_public_agent_run_error_explains_activity_timeout():
    from app.services.ai_conversations import _public_agent_run_error

    assert _public_agent_run_error("执行器连续 90s 没有输出或进度") == (
        "执行器已连续 90 秒没有输出或进度。请检查 Agent 过程；"
        "若执行器仍在工作，请确认它会持续输出状态事件，否则从本轮重试。"
    )


async def test_agent_run_failure_reaches_failed_state_when_diagnostic_event_write_fails():
    from app.services.ai_conversations import _record_agent_run_failure

    calls: list[tuple[str, str]] = []

    class FailingDiagnosticStore:
        async def fail_run(self, run_id: str, error: str) -> None:
            calls.append(("fail", f"{run_id}:{error}"))

        async def append_event(self, **_kwargs) -> None:
            calls.append(("diagnostic", "attempted"))
            raise RuntimeError("database is locked")

    await _record_agent_run_failure(
        store=FailingDiagnosticStore(),
        run_id="run-1",
        conversation_id="conv-1",
        technical_message="internal parser failed at /private/path",
        public_message="执行器运行失败。请重试。",
    )

    assert calls[0] == ("diagnostic", "attempted")
    assert calls[1] == ("fail", "run-1:执行器运行失败。请重试。")


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


async def test_context_status_message_discloses_graph_artifact_degrade_to_source():
    from app.services.ai_conversations import _context_status_message

    message = _context_status_message(
        [
            {
                "source_type": "workspace_source",
                "source_id": "lib/iscsi/iscsi.c",
                "title": "lib/iscsi/iscsi.c",
            }
        ]
    )

    assert "GitNexus/CGC 图谱产物未命中" in message
    assert "降级" in message
    assert "工作区源码" in message


async def test_context_status_message_names_graph_artifacts_when_available():
    from app.services.ai_conversations import _context_status_message

    message = _context_status_message(
        [
            {
                "source_type": "workspace_report",
                "source_id": "report-gitnexus",
                "title": "GitNexus 可信度评估",
                "metadata": {"report_type": "gitnexus_reliability"},
            },
            {
                "source_type": "workspace_source",
                "source_id": "lib/nvmf/ctrlr.c",
                "title": "lib/nvmf/ctrlr.c",
            },
        ]
    )

    assert "GitNexus/CGC 图谱产物" in message
    assert "工作区源码" in message
    assert "未命中" not in message


class TestAIConversationsAPI:
    async def test_agent_thread_invocation_manifest_derives_provider_from_runtime_id(self, tmp_path):
        from app.services.ai_conversations import _agent_thread_invocation_manifest

        manifest = _agent_thread_invocation_manifest(
            conversation={
                "id": "conv-provider",
                "workspace_id": "ws-provider",
                "agent_runtime_id": "default-codex",
            },
            run_id="run-provider",
            runtime={
                "id": "default-codex",
                "name": "Codex",
                "command": "codex",
                "prompt_transport": "codex_exec_json",
            },
            prompt="完整用户输入",
            cwd=str(tmp_path),
            repo_path=str(tmp_path),
            user_message="完整用户输入",
            references=[],
            artifact_dir=tmp_path / "artifacts",
            resume_session_id="",
        )

        assert manifest["runtime"]["provider"] == "agent-runtime:default-codex"

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

    async def test_list_conversations_hides_internal_e2e_threads_by_default(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db, "ws-internal-thread-filter")
        hidden_ws_id = "ws-hidden-e2e-owner"
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        stale = (now_dt - timedelta(hours=1)).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (
                    hidden_ws_id,
                    "ai-list-target-1783058208353",
                    "/private/var/folders/demo/T/codetalk-ai-list-target-7mnGty",
                    stale,
                    stale,
                ),
            )
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (
                    "ws-hidden-context-panel",
                    "ai_context_panel_1782987378405",
                    "/private/var/folders/demo/T/codetalk_ai_context_panel_PmJiko",
                    stale,
                    stale,
                ),
            )
            await db.execute(
                """
                INSERT INTO agent_runtimes
                    (id, name, command, args_json, prompt_transport, output_mode,
                     working_dir_mode, fixed_working_dir, env_json, health_command,
                     timeout_seconds, completion_mode, idle_complete_seconds, sentinel_text,
                     session_persistence, resume_args_json, enabled, created_at, updated_at)
                VALUES (
                    'agent-stale-e2e-runtime',
                    'Relevant Evidence E2E 1783169169',
                    '/usr/bin/python3',
                    '["/tmp/codetalk-relevant-evidence-agent.py"]',
                    'stdin',
                    'auto',
                    'project',
                    '',
                    '{}',
                    '',
                    10,
                    'process_exit',
                    5,
                    '',
                    'none',
                    '[]',
                    1,
                    ?,
                    ?
                )
                """,
                (stale, stale),
            )
            await db.execute(
                """
                INSERT INTO agent_runtimes
                    (id, name, command, args_json, prompt_transport, output_mode,
                     working_dir_mode, fixed_working_dir, env_json, health_command,
                     timeout_seconds, completion_mode, idle_complete_seconds, sentinel_text,
                     session_persistence, resume_args_json, enabled, created_at, updated_at)
                VALUES (
                    'agent-stale-temp-path-runtime',
                    'Plain escaped temp runtime',
                    '/usr/bin/python3',
                    '["/private/var/folders/demo/T/codetalk-agent-running-draft-abc/agent.py"]',
                    'stdin',
                    'auto',
                    'project',
                    '',
                    '{}',
                    '',
                    10,
                    'process_exit',
                    5,
                    '',
                    'none',
                    '[]',
                    1,
                    ?,
                    ?
                )
                """,
                (stale, stale),
            )
            await db.commit()
        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            visible_resp = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "memory_namespace": f"workspace:{ws_id}",
                    "title": "spdk · 用户真实调查线程",
                },
            )
            assert visible_resp.status_code == 201
            visible_id = visible_resp.json()["id"]

            internal_resp = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "memory_namespace": f"workspace:{ws_id}",
                    "title": "spdk · 内部回归线程",
                    "initial_context": {"codetalk_internal": True, "source": "playwright"},
                },
            )
            assert internal_resp.status_code == 201
            internal_id = internal_resp.json()["id"]

            legacy_resp = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "memory_namespace": f"workspace:{ws_id}",
                    "title": "spdk · E2E 裸工具输出验证 2",
                },
            )
            assert legacy_resp.status_code == 201
            legacy_id = legacy_resp.json()["id"]

            named_e2e_resp = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "memory_namespace": f"workspace:{ws_id}",
                    "title": "ai-thread-e2e-1783060000 NVMe-oF connect 调查",
                },
            )
            assert named_e2e_resp.status_code == 201
            named_e2e_id = named_e2e_resp.json()["id"]

            prefixed_e2e_resp = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "memory_namespace": f"workspace:{ws_id}",
                    "title": "E2E 源码全文折叠验证",
                },
            )
            assert prefixed_e2e_resp.status_code == 201
            prefixed_e2e_id = prefixed_e2e_resp.json()["id"]

            runtime_bound_resp = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "memory_namespace": f"workspace:{ws_id}",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": "agent-stale-e2e-runtime",
                    "title": "Relevant evidence line 验证",
                },
            )
            assert runtime_bound_resp.status_code == 201
            runtime_bound_id = runtime_bound_resp.json()["id"]

            temp_path_runtime_resp = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "workspace_id": ws_id,
                    "memory_namespace": f"workspace:{ws_id}",
                    "runtime_type": "agent_runtime",
                    "agent_runtime_id": "agent-stale-temp-path-runtime",
                    "title": "普通标题但绑定遗留临时 runtime",
                },
            )
            assert temp_path_runtime_resp.status_code == 201
            temp_path_runtime_id = temp_path_runtime_resp.json()["id"]

            hidden_workspace_thread_resp = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": hidden_ws_id,
                    "workspace_id": hidden_ws_id,
                    "memory_namespace": f"workspace:{hidden_ws_id}",
                    "title": "普通标题但归属内部测试 workspace",
                },
            )
            assert hidden_workspace_thread_resp.status_code == 201
            hidden_workspace_thread_id = hidden_workspace_thread_resp.json()["id"]

            hidden_context_panel_thread_resp = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": "ws-hidden-context-panel",
                    "workspace_id": "ws-hidden-context-panel",
                    "memory_namespace": "workspace:ws-hidden-context-panel",
                    "title": "上下文面板普通标题",
                },
            )
            assert hidden_context_panel_thread_resp.status_code == 201
            hidden_context_panel_thread_id = hidden_context_panel_thread_resp.json()["id"]

            orphan_workspace_thread_resp = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": "ws-orphan-internal-history",
                    "workspace_id": "ws-orphan-internal-history",
                    "memory_namespace": "workspace:ws-orphan-internal-history",
                    "title": "普通标题但归属已清理测试 workspace",
                },
            )
            assert orphan_workspace_thread_resp.status_code == 201
            orphan_workspace_thread_id = orphan_workspace_thread_resp.json()["id"]

            async with aiosqlite.connect(sqlite_db) as db:
                stale_ids = [legacy_id, named_e2e_id, prefixed_e2e_id, runtime_bound_id, temp_path_runtime_id]
                for offset, stale_id in enumerate(stale_ids, start=1):
                    timestamp = (now_dt - timedelta(hours=1, seconds=offset)).isoformat()
                    await db.execute(
                        "UPDATE ai_conversations SET created_at = ?, updated_at = ? WHERE id = ?",
                        (timestamp, timestamp, stale_id),
                    )
                await db.commit()

            listed = await client.get("/api/ai/conversations", params={"workspace_id": ws_id, "limit": 10})
            assert listed.status_code == 200
            ids = [item["id"] for item in listed.json()["items"]]
            assert ids == [visible_id]

            global_listed = await client.get("/api/ai/conversations", params={"limit": 10})
            assert global_listed.status_code == 200
            global_ids = [item["id"] for item in global_listed.json()["items"]]
            assert visible_id in global_ids
            assert hidden_workspace_thread_id not in global_ids
            assert hidden_context_panel_thread_id not in global_ids
            assert orphan_workspace_thread_id not in global_ids

            debug_listed = await client.get(
                "/api/ai/conversations",
                params={"workspace_id": ws_id, "limit": 10, "include_internal": "true"},
            )
            assert debug_listed.status_code == 200
            debug_ids = [item["id"] for item in debug_listed.json()["items"]]
            assert {
                prefixed_e2e_id,
                named_e2e_id,
                legacy_id,
                runtime_bound_id,
                temp_path_runtime_id,
                internal_id,
                visible_id,
            }.issubset(set(debug_ids))

            debug_global = await client.get("/api/ai/conversations", params={"limit": 10, "include_internal": "true"})
            assert debug_global.status_code == 200
            debug_global_ids = [item["id"] for item in debug_global.json()["items"]]
            assert hidden_workspace_thread_id in debug_global_ids
            assert hidden_context_panel_thread_id in debug_global_ids
            assert orphan_workspace_thread_id in debug_global_ids

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

        second = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="第二轮：进入队列等待 SessionChain 串行执行",
            references=[],
        )
        assert second["run"]["status"] == "queued"

        messages = await store.list_messages(conversation["id"])
        assert [item["content"] for item in messages] == [
            "第一轮：启动 agent 分析源码",
            "第二轮：进入队列等待 SessionChain 串行执行",
        ]

    async def test_get_message_hides_legacy_agent_process_leakage(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="旧版 Agent 消息清洗",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="针对 iSCSI 登录写几个黑盒用例",
            references=[],
        )
        run_id = created["run"]["id"]
        raw_answer = (
            "THINKING: 我先核对工作区 iSCSI 登录相关源码，再据此设计黑盒用例。"
            "1125:iscsi_conn_login_pdu_success_complete(void *arg)\n"
            "1149:iscsi_op_login_response(struct spdk_iscsi_conn *conn,\n"
            "1191:iscsi_op_login_rsp_init(struct spdk_iscsi_conn *conn,\n\n"
            "## 黑盒测试用例\n"
            "### TC-01 正常登录\n"
            "前置条件：target 已启动；步骤：initiator 发起 Login；预期结果：进入 Full Feature Phase。\n"
        )
        await store.complete_run(
            run_id=run_id,
            content=raw_answer,
            references=[],
            model="agent:legacy",
        )
        messages = await store.list_messages(conversation["id"])
        assistant = next(item for item in messages if item["role"] == "assistant")
        single = await store.get_message(assistant["id"])

        assert "## 黑盒测试用例" in single["content"]
        assert "TC-01 正常登录" in single["content"]
        assert "THINKING:" not in single["content"]
        assert "iscsi_conn_login_pdu_success_complete" not in single["content"]

    async def test_complete_run_promotes_answer_line_citations_to_precise_workspace_refs(
        self,
        sqlite_db,
        tmp_path,
    ):
        ws_id = await _seed_workspace(sqlite_db)
        repo = tmp_path / "repo"
        source_dir = repo / "lib" / "iscsi"
        source_dir.mkdir(parents=True)
        iscsi_lines = [f"int filler_{idx};" for idx in range(1, 90)]
        iscsi_lines[41] = 'SPDK_ERRLOG("unsupported AuthMethod %.64s\\n", method);'
        conn_lines = [f"int conn_filler_{idx};" for idx in range(1, 90)]
        conn_lines[54] = "conn->require_chap = portal->group->require_chap;"
        (source_dir / "iscsi.c").write_text("\n".join(iscsi_lines), encoding="utf-8")
        (source_dir / "conn.c").write_text("\n".join(conn_lines), encoding="utf-8")
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute("UPDATE workspaces SET repo_path = ? WHERE id = ?", (str(repo), ws_id))
            await db.commit()

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="精确证据行号线程",
        )
        coarse_refs = [
            {
                "source_type": "workspace_source",
                "source_id": f"{ws_id}:lib/iscsi/iscsi.c:1-20",
                "title": "lib/iscsi/iscsi.c:1",
                "excerpt": "1: file header",
                "metadata": {
                    "workspace_id": ws_id,
                    "path": "lib/iscsi/iscsi.c",
                    "start_line": 1,
                    "end_line": 20,
                },
            },
            {
                "source_type": "workspace_source",
                "source_id": f"{ws_id}:lib/iscsi/conn.c:1-20",
                "title": "lib/iscsi/conn.c:1",
                "excerpt": "1: file header",
                "metadata": {
                    "workspace_id": ws_id,
                    "path": "lib/iscsi/conn.c",
                    "start_line": 1,
                    "end_line": 20,
                },
            },
        ]
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="分析 iSCSI CHAP 登录",
            references=[],
        )
        run_id = created["run"]["id"]
        await store.complete_run(
            run_id=run_id,
            content=(
                "证据：`iscsi.c:42-45` 拒绝 unsupported AuthMethod；"
                "`lib/iscsi/conn.c:55` 从 portal group 继承 require_chap。"
            ),
            references=coarse_refs,
            model="agent:test",
        )

        messages = await store.list_messages(conversation["id"])
        assistant = next(item for item in messages if item["role"] == "assistant")
        refs = assistant["references"]

        assert refs[0]["title"] == "lib/iscsi/iscsi.c:42"
        assert refs[0]["metadata"]["path"] == "lib/iscsi/iscsi.c"
        assert refs[0]["metadata"]["start_line"] <= 42 <= refs[0]["metadata"]["end_line"]
        assert "unsupported AuthMethod" in refs[0]["excerpt"]
        assert refs[1]["title"] == "lib/iscsi/conn.c:55"
        assert refs[1]["metadata"]["path"] == "lib/iscsi/conn.c"
        assert refs[1]["metadata"]["start_line"] <= 55 <= refs[1]["metadata"]["end_line"]
        assert "require_chap" in refs[1]["excerpt"]

    async def test_complete_run_uses_full_artifact_body_for_precise_refs_when_visible_answer_is_compact(
        self,
        sqlite_db,
        tmp_path,
    ):
        ws_id = await _seed_workspace(sqlite_db)
        repo = tmp_path / "repo"
        source_dir = repo / "lib" / "iscsi"
        source_dir.mkdir(parents=True)
        iscsi_lines = [f"int filler_{idx};" for idx in range(1, 860)]
        iscsi_lines[793] = 'SPDK_ERRLOG("unsupported AuthMethod %.64s\\n", method);'
        conn_lines = [f"int conn_filler_{idx};" for idx in range(1, 240)]
        conn_lines[191] = "conn->disable_chap = portal->group->disable_chap;"
        (source_dir / "iscsi.c").write_text("\n".join(iscsi_lines), encoding="utf-8")
        (source_dir / "conn.c").write_text("\n".join(conn_lines), encoding="utf-8")
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute("UPDATE workspaces SET repo_path = ? WHERE id = ?", (str(repo), ws_id))
            await db.commit()

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="下载产物证据行号线程",
        )
        coarse_refs = [
            {
                "source_type": "workspace_source",
                "source_id": f"{ws_id}:lib/iscsi/iscsi.c:1-41",
                "title": "lib/iscsi/iscsi.c:1",
                "excerpt": "1: file header",
                "metadata": {
                    "workspace_id": ws_id,
                    "path": "lib/iscsi/iscsi.c",
                    "start_line": 1,
                    "end_line": 41,
                },
            },
            {
                "source_type": "workspace_source",
                "source_id": f"{ws_id}:lib/iscsi/conn.c:1-41",
                "title": "lib/iscsi/conn.c:1",
                "excerpt": "1: file header",
                "metadata": {
                    "workspace_id": ws_id,
                    "path": "lib/iscsi/conn.c",
                    "start_line": 1,
                    "end_line": 41,
                },
            },
        ]
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="生成完整 iSCSI login SFMEA 和黑盒测试用例产物",
            references=[],
        )
        run_id = created["run"]["id"]
        compact_visible_answer = (
            "## iSCSI Login — SFMEA + 黑盒测试用例产物\n\n"
            "完整测试设计/SFMEA/黑盒用例已保存为下载产物。"
        )
        full_artifact_body = (
            "## 代码证据\n"
            "- `lib/iscsi/iscsi.c:794`: 非 CHAP AuthMethod 被拒绝。\n"
            "- `lib/iscsi/conn.c:192`: 连接继承 portal group 的 disable_chap 策略。\n\n"
            "## SFMEA\n"
            "| failure mode | cause | effect | detection | RPN |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 非 CHAP 被放行 | AuthMethod 校验失效 | 认证绕过 | 登录响应和日志 | 30 |\n\n"
            "## 黑盒测试用例\n"
            "### TC-01 非 CHAP AuthMethod 拒绝\n"
        )
        await store.complete_run(
            run_id=run_id,
            content=compact_visible_answer,
            evidence_content=full_artifact_body,
            references=coarse_refs,
            model="agent:test",
            actions=[{"id": "download_run_artifact", "label": "下载完整产物", "kind": "download"}],
        )

        messages = await store.list_messages(conversation["id"])
        assistant = next(item for item in messages if item["role"] == "assistant")
        refs = assistant["references"]

        assert "lib/iscsi/iscsi.c:794" not in assistant["content"]
        assert refs[0]["title"] == "lib/iscsi/iscsi.c:794"
        assert refs[0]["metadata"]["start_line"] <= 794 <= refs[0]["metadata"]["end_line"]
        assert "unsupported AuthMethod" in refs[0]["excerpt"]
        assert refs[1]["title"] == "lib/iscsi/conn.c:192"
        assert refs[1]["metadata"]["start_line"] <= 192 <= refs[1]["metadata"]["end_line"]
        assert "disable_chap" in refs[1]["excerpt"]

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

    async def test_agent_prompt_source_artifact_contract_is_not_duplicated_without_refs(self):
        from app.services.ai_conversations import _build_agent_prompt

        prompt = _build_agent_prompt(
            {
                "id": "conv-source-artifact-no-refs",
                "title": "图谱优先无引用",
                "scope_type": "workspace",
                "scope_id": "ws-source-artifact-no-refs",
                "workspace_id": "ws-source-artifact-no-refs",
                "initial_context": {},
            },
            [],
            [],
            "分析 iSCSI login 并输出测试设计",
            {"id": "runtime-source-artifact-no-refs", "name": "Runtime"},
        )

        assert prompt.count("SOURCE_ARTIFACT_PRIORITY:") == 1
        assert "SOURCE_FIRST_CONTRACT:" in prompt
        assert "未找到直接源码或输入材料时，必须说明未验证" in prompt

    async def test_agent_prompt_routes_download_artifacts_to_isolated_artifact_dir(self):
        from app.services.ai_conversations import _build_agent_prompt

        prompt = _build_agent_prompt(
            {
                "id": "conv-agent-artifact-contract",
                "title": "产物契约",
                "scope_type": "workspace",
                "scope_id": "ws-agent-artifact-contract",
                "workspace_id": "ws-agent-artifact-contract",
                "initial_context": {},
            },
            [],
            [],
            "生成完整 iSCSI login SFMEA 和黑盒测试用例产物，保存为可下载文件",
            {"id": "runtime-agent-artifact-contract", "name": "Runtime"},
        )

        assert "ARTIFACT_DELIVERY_CONTRACT" in prompt
        assert "CodeTalk 负责把最终 Markdown 物化为“下载完整产物”" in prompt
        assert "不要调用 Write/Edit" in prompt
        assert "绝不要写入源码目录" in prompt
        assert "CODETALK_AGENT_ARTIFACT_DIR" in prompt
        assert "deliverable.md" in prompt
        assert "完整交付正文必须写入" in prompt
        assert "聊天最终回答只给简短摘要" in prompt

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

    async def test_agent_prompt_history_expands_previous_download_artifact(
        self,
        sqlite_db,
    ):
        ws_id = await _seed_workspace(sqlite_db)

        from app.services.ai_conversations import (
            AIConversationStore,
            _build_agent_prompt,
            ai_thread_artifact_path,
        )

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="历史产物连续性线程",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="生成完整 SFMEA 和黑盒测试用例",
            references=[],
        )
        run_id = created["run"]["id"]
        artifact_path = ai_thread_artifact_path(conversation["id"], run_id)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            "\n".join(
                [
                    "# 历史产物连续性线程",
                    "",
                    f"- conversation_id: {conversation['id']}",
                    f"- run_id: {run_id}",
                    "- exported_at: 2026-07-03T00:00:00+00:00",
                    "",
                    "## 黑盒测试用例",
                    "FULL_ARTIFACT_CONTEXT_MARKER：TC-99 CHAP 失败后重连恢复。",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        await store.complete_run(
            run_id=run_id,
            content=(
                "## Agent 产物\n\n"
                "已生成结构化产物（完整产物内容）。为避免长表格和完整用例挤占对话区，正文已收起到下载文件。"
            ),
            references=[],
            model="agent:test",
            actions=[
                {
                    "id": "download_run_artifact",
                    "label": "下载完整产物",
                    "href": f"/api/ai/conversations/{conversation['id']}/runs/{run_id}/artifact",
                    "kind": "download",
                }
            ],
        )
        messages = await store.list_messages(conversation["id"])

        prompt = _build_agent_prompt(
            conversation,
            [
                *messages,
                {"role": "user", "content": "基于上一轮继续细化 CHAP 失败场景"},
            ],
            [],
            "基于上一轮继续细化 CHAP 失败场景",
            {"id": "runtime-history", "name": "History Runtime", "session_persistence": "none"},
        )

        assert "历史助手完整下载产物" in prompt
        assert "FULL_ARTIFACT_CONTEXT_MARKER" in prompt
        assert "TC-99 CHAP 失败后重连恢复" in prompt

        resume_prompt = _build_agent_prompt(
            conversation,
            [
                *messages,
                {"role": "user", "content": "基于上一轮继续细化 CHAP 失败场景"},
            ],
            [],
            "基于上一轮继续细化 CHAP 失败场景",
            {
                "id": "runtime-history",
                "name": "History Runtime",
                "session_persistence": "resume_args",
            },
        )
        assert "历史助手完整下载产物" not in resume_prompt
        assert "FULL_ARTIFACT_CONTEXT_MARKER" not in resume_prompt

        fresh_fallback_prompt = _build_agent_prompt(
            conversation,
            [
                *messages,
                {"role": "user", "content": "基于上一轮继续细化 CHAP 失败场景"},
            ],
            [],
            "基于上一轮继续细化 CHAP 失败场景",
            {
                "id": "runtime-history",
                "name": "History Runtime",
                "session_persistence": "resume_args",
                "force_prompt_history": True,
            },
        )
        assert "历史助手完整下载产物" in fresh_fallback_prompt
        assert "FULL_ARTIFACT_CONTEXT_MARKER" in fresh_fallback_prompt

    async def test_resume_test_activity_context_keeps_original_and_current_only(self):
        from app.services.ai_conversations import _test_activity_request_context

        messages = [
            {"role": "user", "content": "请对 iSCSI Login 输出完整 SFMEA 和黑盒测试设计"},
            {"role": "user", "content": "中间修订：补充 CHAP 负向矩阵"},
            {"role": "user", "content": "中间修订：修复 CID oracle"},
            {"role": "user", "content": "当前修订：只修复 profile 执行闭环"},
        ]

        context = _test_activity_request_context(
            messages,
            "当前修订：只修复 profile 执行闭环",
            {"session_persistence": "resume_args"},
        )

        assert "完整 SFMEA 和黑盒测试设计" in context
        assert "当前修订：只修复 profile 执行闭环" in context
        assert "补充 CHAP 负向矩阵" not in context
        assert "修复 CID oracle" not in context

        fallback_context = _test_activity_request_context(
            messages,
            "当前修订：只修复 profile 执行闭环",
            {"session_persistence": "resume_args", "force_prompt_history": True},
        )
        assert "补充 CHAP 负向矩阵" in fallback_context
        assert "修复 CID oracle" in fallback_context

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

    async def test_source_symbol_anchor_prefers_definition_over_later_call(self, tmp_path: Path):
        from app.services.ai_conversations import _best_source_line_for_query

        source = tmp_path / "iscsi.c"
        source.write_text(
            "\n".join(
                [
                    "static int",
                    "iscsi_auth_params(void *conn)",
                    "{",
                    "    return 0;",
                    "}",
                    "",
                    "static void caller(void)",
                    "{",
                    "    iscsi_auth_params(0);",
                    "}",
                ]
            ),
            encoding="utf-8",
        )

        assert _best_source_line_for_query(source, "iscsi_auth_params") == 2

    async def test_workspace_source_refs_balance_multiple_explicit_directories(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        for relative, content in {
            "lib/iscsi/conn.c": "int iscsi_login_entry(void) { return 1; }\n",
            "lib/iscsi/iscsi.c": "int iscsi_login_auth(void) { return 2; }\n",
            "lib/iscsi/param.c": "int iscsi_login_params(void) { return 3; }\n",
            "lib/iscsi/tgt_node.c": "int iscsi_login_target(void) { return 4; }\n",
            "app/iscsi_tgt/iscsi_tgt.c": "int iscsi_tgt_main(void) { return 5; }\n",
            "test/iscsi_tgt/chap/chap.sh": "iscsi_login_chap_test\n",
            "test/iscsi_tgt/login_redirection/login_redirection.sh": "iscsi_login_redirect_test\n",
        }.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        ws_id = "ws-balanced-hints"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Balanced Hints WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-balanced-hints",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message=(
                "完整分析 iSCSI login；定向阅读 lib/iscsi、app/iscsi_tgt、test/iscsi_tgt，"
                "输出源码证据和现有测试映射"
            ),
            db_path=sqlite_db,
        )
        paths = [
            str(ref.metadata.get("path") or "")
            for ref in refs
            if ref.source_type == "workspace_source"
        ]

        assert any(path.startswith("lib/iscsi/") for path in paths)
        assert any(path.startswith("app/iscsi_tgt/") for path in paths)
        assert any(path.startswith("test/iscsi_tgt/") for path in paths)
        assert any(Path(path).suffix in {".sh", ".py"} for path in paths if path.startswith("test/"))

    async def test_workspace_source_refs_rank_login_and_auth_files_above_generic_scripts(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        for relative, content in {
            "lib/iscsi/conn.c": "int iscsi_tgt_generic(void) { return 1; }\n",
            "lib/iscsi/iscsi.c": "int iscsi_login_request(void) { return 2; }\n",
            "lib/iscsi/param.c": "int iscsi_auth_param_negotiate(void) { return 3; }\n",
            "app/iscsi_tgt/iscsi_tgt.c": "int iscsi_tgt_main(void) { return 4; }\n",
            "test/iscsi_tgt/bdev_io_wait/bdev_io_wait.sh": "iscsi_tgt generic login helper\n",
            "test/iscsi_tgt/calsoft/calsoft.sh": "iscsi_tgt generic login helper\n",
            "test/iscsi_tgt/chap/chap_discovery.sh": "iscsi CHAP authentication test\n",
            "test/iscsi_tgt/login_redirection/login_redirection.sh": "iscsi login redirect test\n",
        }.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        ws_id = "ws-relevant-login-hints"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Relevant Login Hints WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-relevant-login-hints",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message=(
                "完整分析 iSCSI Login 的认证、CHAP、参数协商和状态转换；定向阅读 "
                "lib/iscsi、app/iscsi_tgt、test/iscsi_tgt，输出 failure_mode、"
                "detection_score、source_evidence 和具体 test_mapping"
            ),
            db_path=sqlite_db,
        )
        paths = [
            str(ref.metadata.get("path") or "")
            for ref in refs
            if ref.source_type == "workspace_source"
        ]

        assert "lib/iscsi/iscsi.c" in paths
        assert "lib/iscsi/param.c" in paths
        assert "test/iscsi_tgt/login_redirection/login_redirection.sh" in paths
        assert "test/iscsi_tgt/chap/chap_discovery.sh" in paths
        assert paths.index("test/iscsi_tgt/login_redirection/login_redirection.sh") < paths.index(
            "test/iscsi_tgt/bdev_io_wait/bdev_io_wait.sh"
        )

    async def test_workspace_source_refs_for_directory_hint_start_near_matching_flow_line(
        self,
        sqlite_db,
        tmp_path: Path,
    ):
        repo = tmp_path / "repo"
        nvmf_dir = repo / "lib" / "nvmf"
        nvmf_dir.mkdir(parents=True)
        header = [f"/* copyright header {idx} */" for idx in range(1, 45)]
        (nvmf_dir / "auth.c").write_text(
            "\n".join(header + ["int nvmf_auth_disconnect_qpair(void) { return 0; }"]),
            encoding="utf-8",
        )
        (nvmf_dir / "ctrlr.c").write_text(
            "\n".join(
                header
                + [
                    "#define SPDK_NVMF_INVALID_CONNECT_CMD 1",
                    "static void",
                    "nvmf_ctrlr_send_connect_rsp(void *ctx)",
                    "{",
                    "    (void)ctx;",
                    "}",
                    *[f"int filler_{idx};" for idx in range(24)],
                    "static int",
                    "nvmf_ctrlr_connect_io_ready(void)",
                    "{",
                    '    SPDK_DEBUGLOG(nvmf, "Subsystem is not ready for connect, retrying...\\n");',
                    "    return 1;",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        ws_id = "ws-dir-hint-flow-line"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'Directory Hint Flow Line WS', ?, 1, ?, ?)",
                (ws_id, str(repo), now, now),
            )
            await db.commit()

        from app.services.ai_conversations import build_context_references

        refs = await build_context_references(
            conversation={
                "id": "conv-dir-hint-flow-line",
                "scope_type": "workspace",
                "scope_id": ws_id,
                "workspace_id": ws_id,
                "memory_namespace": f"workspace:{ws_id}",
                "initial_context": {},
            },
            user_message="请分析 SPDK NVMe-oF target connect 到 IO ready 的主链路",
            db_path=sqlite_db,
        )
        source_refs = [ref for ref in refs if ref.source_type == "workspace_source"]

        assert source_refs
        assert source_refs[0].metadata["path"] == "lib/nvmf/ctrlr.c"
        assert source_refs[0].metadata["start_line"] > 1
        assert "nvmf_ctrlr_connect_io_ready" in source_refs[0].excerpt

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

    async def test_workbench_task_thread_references_test_activity_contract_and_quality_audit(
        self,
        sqlite_db,
        tmp_path: Path,
        monkeypatch,
    ):
        from app.config import settings
        from app.services.ai_conversations import build_context_references

        data_root = tmp_path / "data"
        task_run_id = "task_run_test_activity_context"
        task_dir = data_root / "workbench" / "task_runs" / task_run_id
        task_dir.mkdir(parents=True)
        (task_dir / "task_run.json").write_text(
            json.dumps({"task_run_id": task_run_id, "status": "completed"}),
            encoding="utf-8",
        )
        (task_dir / "task_bundle.json").write_text(
            json.dumps({"workflow_id": "source_flow_sfmea_blackbox", "repo_path": "/repo/spdk"}),
            encoding="utf-8",
        )
        (task_dir / "test_activity_contract.json").write_text(
            json.dumps(
                {
                    "target": "iSCSI login 测试设计",
                    "domain_profiles": ["iscsi_login"],
                    "required_outputs": ["sfmea.json", "black_box_cases.json"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (task_dir / "test_activity_quality_audit.json").write_text(
            json.dumps(
                {
                    "status": "needs_rework",
                    "score": 62,
                    "issues": [{"code": "missing_source_evidence", "artifact": "sfmea.json"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (task_dir / "task_artifact_manifest.json").write_text(
            json.dumps({"task_run_id": task_run_id, "artifacts": []}),
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "data_dir", str(data_root))

        refs = await build_context_references(
            conversation={
                "id": "conv-workbench-test-activity",
                "scope_type": "workbench_task_run",
                "scope_id": task_run_id,
                "workspace_id": "ws-workbench",
                "memory_namespace": "workspace:ws-workbench",
                "initial_context": {"workspace_id": "ws-workbench"},
            },
            user_message="请继续复盘本次测试活动契约和质量审计问题",
            db_path=sqlite_db,
        )

        refs_by_title = {ref.title: ref for ref in refs if ref.source_type == "workbench_task_artifact"}
        assert "test_activity_contract.json" in refs_by_title
        assert "test_activity_quality_audit.json" in refs_by_title
        assert "iscsi_login" in refs_by_title["test_activity_contract.json"].excerpt
        assert "needs_rework" in refs_by_title["test_activity_quality_audit.json"].excerpt

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
                json={"content": "这个报告里的项目背景还缺什么？"},
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

    async def test_queues_second_message_while_generation_is_running_and_runs_next(
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
                    "title": "线程内队列",
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
            assert second.status_code == 202
            second_payload = second.json()
            assert second_payload["run"]["status"] == "queued"
            assert first.json()["run"]["sequence"] == 1
            assert first.json()["run"]["queue_position"] == 0
            assert second_payload["run"]["sequence"] == 2
            assert second_payload["run"]["queue_position"] == 1

            fake_llm.release.set()
            for _ in range(40):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                assistant_count = sum(1 for item in items if item["role"] == "assistant")
                if len(items) == 4 and assistant_count == 2:
                    break
                await asyncio.sleep(0.05)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            assert messages.status_code == 200
            body = messages.json()
            assert [m["role"] for m in body["items"]] == ["user", "user", "assistant", "assistant"]
            assert body["items"][0]["content"] == "先分析 SPDK nvmf connect 流程"
            assert body["items"][1]["content"] == "运行中再追问异常链路"
            assert [item["content"] for item in body["items"] if item["role"] == "assistant"] == [
                "第一段分析。最终结论。",
                "第一段分析。最终结论。",
            ]
            runs = [
                body["items"][0]["run_id"],
                body["items"][1]["run_id"],
                body["items"][2]["run_id"],
                body["items"][3]["run_id"],
            ]
            assert runs[0] != runs[1]
            assert runs[0] == runs[2]
            assert runs[1] == runs[3]

    async def test_different_conversations_run_without_blocking_each_other(
        self,
        sqlite_db,
        monkeypatch,
    ):
        ws_id = await _seed_workspace(sqlite_db)

        from app.api import ai_conversations

        fake_llm = MultiConversationBlockingLLM()
        monkeypatch.setattr(
            ai_conversations,
            "create_llm_client_from_active",
            lambda: fake_llm,
        )

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created_a = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "title": "线程 A",
                },
            )
            created_b = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "title": "线程 B",
                },
            )
            assert created_a.status_code == 201
            assert created_b.status_code == 201
            conversation_a = created_a.json()
            conversation_b = created_b.json()

            first = await client.post(
                f"/api/ai/conversations/{conversation_a['id']}/messages",
                json={"content": "线程 A 长任务：分析 nvmf connect"},
            )
            assert first.status_code == 202
            await asyncio.wait_for(fake_llm.started["thread-a"].wait(), timeout=1)

            second = await client.post(
                f"/api/ai/conversations/{conversation_b['id']}/messages",
                json={"content": "线程 B 长任务：分析 iscsi login"},
            )
            assert second.status_code == 202
            await asyncio.wait_for(fake_llm.started["thread-b"].wait(), timeout=1)

            assert first.json()["run"]["sequence"] == 1
            assert second.json()["run"]["sequence"] == 1
            assert first.json()["run"]["queue_position"] == 0
            assert second.json()["run"]["queue_position"] == 0

            fake_llm.release.set()
            for _ in range(40):
                messages_a = await client.get(f"/api/ai/conversations/{conversation_a['id']}/messages")
                messages_b = await client.get(f"/api/ai/conversations/{conversation_b['id']}/messages")
                items_a = messages_a.json()["items"]
                items_b = messages_b.json()["items"]
                if (
                    len(items_a) == 2
                    and items_a[-1]["role"] == "assistant"
                    and len(items_b) == 2
                    and items_b[-1]["role"] == "assistant"
                ):
                    break
                await asyncio.sleep(0.05)

            messages_a = await client.get(f"/api/ai/conversations/{conversation_a['id']}/messages")
            messages_b = await client.get(f"/api/ai/conversations/{conversation_b['id']}/messages")
            body_a = messages_a.json()
            body_b = messages_b.json()
            assert [item["role"] for item in body_a["items"]] == ["user", "assistant"]
            assert [item["role"] for item in body_b["items"]] == ["user", "assistant"]
            assert "thread-a 完成" in body_a["items"][1]["content"]
            assert "thread-b 完成" in body_b["items"][1]["content"]

    async def test_conversation_run_order_uses_sequence_when_timestamps_collide(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="线程序号排序",
        )
        first = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="第一轮：分析 nvmf connect",
            references=[],
        )
        second = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="第二轮：分析 iscsi login",
            references=[],
        )
        assert first["run"]["sequence"] == 1
        assert second["run"]["sequence"] == 2

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "UPDATE ai_conversation_runs SET created_at = ? WHERE id = ?",
                ("2026-06-28T00:00:20Z", first["run"]["id"]),
            )
            await db.execute(
                "UPDATE ai_conversation_runs SET created_at = ? WHERE id = ?",
                ("2026-06-28T00:00:10Z", second["run"]["id"]),
            )
            await db.commit()

        next_run = await store.next_queued_run(conversation["id"])
        latest_run = await store.latest_run(conversation["id"])

        assert next_run is not None
        assert next_run["id"] == first["run"]["id"]
        assert next_run["sequence"] == 1
        assert latest_run is not None
        assert latest_run["id"] == second["run"]["id"]
        assert latest_run["sequence"] == 2

    async def test_reconcile_interrupted_runs_marks_running_and_queued_idle(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="重启恢复线程",
        )
        first = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="第一轮长任务",
            references=[],
        )
        await store.mark_run_running(first["run"]["id"])
        second = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="第二轮排队任务",
            references=[],
        )

        result = await store.reconcile_interrupted_runs()

        assert result["interrupted_count"] == 2
        assert {
            item["run_id"] for item in result["runs"]
        } == {first["run"]["id"], second["run"]["id"]}
        assert (await store.get_run(first["run"]["id"]))["status"] == "interrupted"
        assert (await store.get_run(second["run"]["id"]))["status"] == "interrupted"
        assert (await store.get_conversation(conversation["id"]))["status"] == "idle"
        assert await store.next_queued_run(conversation["id"]) is None

        events = await store.list_events_after(conversation["id"], cursor=0, limit=20)
        interrupted_events = [
            item for item in events
            if item["event_type"] == "error"
            and item["payload"].get("kind") == "service_restart_interrupted"
        ]
        assert {item["run_id"] for item in interrupted_events} == {
            first["run"]["id"],
            second["run"]["id"],
        }
        assert all("后端服务重启" in item["payload"]["error"] for item in interrupted_events)

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

    async def test_cancelled_run_cannot_be_completed_by_late_agent_flush(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="取消竞态",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="执行一个长时间 Agent 分析",
            references=[],
        )
        run_id = created["run"]["id"]
        await store.mark_run_running(run_id)
        await store.cancel_run(conversation["id"])

        await store.complete_run(
            run_id=run_id,
            content="这是子进程在取消后才冲刷出的完整答案。",
            references=[],
            model="agent:codex",
        )

        assert (await store.get_run(run_id))["status"] == "cancelled"
        messages = await store.list_messages(conversation["id"])
        assert [message["role"] for message in messages] == ["user"]

    async def test_retry_failed_run_reuses_original_user_message(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="重试不重复消息",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="分析 iSCSI Login 并输出测试设计",
            references=[],
        )
        await store.fail_run(created["run"]["id"], "质量门禁未通过")

        retried = await store.retry_failed_run(
            conversation_id=conversation["id"],
            source_run_id=created["run"]["id"],
        )

        assert retried["run"]["status"] == "queued"
        assert retried["run"]["input_message_id"] == created["message"]["id"]
        assert retried["message"]["id"] == created["message"]["id"]
        retry_events = await store.list_events_for_run(
            conversation["id"], retried["run"]["id"]
        )
        assert retry_events[0]["payload"]["source_run_id"] == created["run"]["id"]
        messages = await store.list_messages(conversation["id"])
        assert [(message["role"], message["content"]) for message in messages] == [
            ("user", "分析 iSCSI Login 并输出测试设计"),
        ]

    async def test_quality_retry_feedback_is_injected_without_changing_user_input(self):
        from app.services.ai_conversations import (
            _build_agent_prompt,
            _build_prompt,
            _quality_retry_draft_text,
            _quality_retry_feedback_text,
        )

        feedback = _quality_retry_feedback_text(
            {
                "score": 70,
                "issues": [
                    {
                        "constraint_id": "iscsi_unknown_key_not_understood",
                        "message": "未知合法 key 必须返回 NotUnderstood，不能写成解析失败。",
                    }
                ],
                "recommendations": ["分开描述合法未知 key 与超长非法 key。"],
            }
        )
        repair_draft = _quality_retry_draft_text(
            "# 未通过质量门禁的模型输出\n\n旧草稿中 Protocol Error=0x05。"
        )
        assert repair_draft.startswith("REJECTED_DRAFT_TO_REPAIR")
        assert "只能修订" in repair_draft
        assert "Protocol Error=0x05" in repair_draft
        assert (
            _quality_retry_draft_text(
                "# Agent 输出文件包\n\n"
                "## .runtime-codex-home-abcd/plugins/shopify/skills/hydrogen/SKILL.md\n\n"
                "You are an assistant that helps Shopify developers."
            )
            == ""
        )
        original = "分析 iSCSI Login 并输出完整测试设计"
        stale_answer = "STALE_REJECTED_ANSWER: Protocol Error=0x05"
        prompt_history = [
            {"role": "user", "content": "上一轮完整任务"},
            {"role": "assistant", "content": stale_answer},
            {"role": "user", "content": original},
        ]
        prompt = _build_agent_prompt(
            {"title": "质量重试", "id": "conv-retry", "workspace_id": "ws-retry"},
            prompt_history,
            [],
            original,
            {"id": "opencode", "name": "OpenCode"},
            repo_path="/repo/spdk",
            quality_retry_feedback=feedback,
        )

        assert "QUALITY_RETRY_FEEDBACK" in prompt
        assert "iscsi_unknown_key_not_understood" in prompt
        assert prompt.rfind("QUALITY_RETRY_FEEDBACK") > prompt.rfind("FINAL_FACT_CHECK")
        assert prompt.rfind("QUALITY_RETRY_FEEDBACK") < prompt.rfind("用户问题：")
        assert stale_answer not in prompt
        assert prompt.endswith(original)

        llm_messages = _build_prompt(
            {"scope_type": "workspace", "scope_id": "ws-retry", "initial_context": {}},
            prompt_history,
            [],
            original,
            quality_retry_feedback=feedback,
        )
        assert len(llm_messages) == 3
        assert llm_messages[-2]["role"] == "system"
        assert llm_messages[-2]["content"].startswith("QUALITY_RETRY_FEEDBACK")
        assert "iscsi_unknown_key_not_understood" in llm_messages[-2]["content"]
        assert stale_answer not in json.dumps(llm_messages, ensure_ascii=False)
        assert llm_messages[-1] == {"role": "user", "content": original}

    async def test_quality_retry_keeps_original_test_activity_scope_for_prompt_and_gate(self):
        from app.services.ai_conversations import (
            _build_agent_prompt,
            _build_prompt,
            _test_activity_request_context,
        )
        from app.services.test_activity_contract import build_test_activity_contract

        original = (
            "基于 SPDK iSCSI Login 输出完整代码流程、SFMEA 和八维黑盒测试用例，"
            "每条映射到具体测试脚本。"
        )
        repair = (
            "请修订上一轮并重新输出完整可下载交付件，修正协议字段和测试映射；"
            "危险脚本仅允许使用 Null/Malloc bdev。"
        )
        messages = [
            {"role": "user", "content": original},
            {"role": "assistant", "content": "旧草稿"},
            {"role": "user", "content": repair},
        ]
        context = _test_activity_request_context(messages, repair)
        contract = build_test_activity_contract(
            target=context,
            repo_path="/repo/spdk",
        )

        assert original in context
        assert repair in context
        assert contract["domain_profiles"] == ["iscsi_login"]
        assert {
            "business_flow.md",
            "sfmea.json",
            "black_box_cases.json",
        }.issubset(set(contract["required_outputs"]))

        prompt = _build_agent_prompt(
            {"title": "质量重试", "id": "conv-retry", "workspace_id": "ws-retry"},
            messages,
            [],
            repair,
            {"id": "codex", "name": "Codex"},
            repo_path="/repo/spdk",
            quality_retry_feedback="QUALITY_RETRY_FEEDBACK:\n  previous_score: 66",
        )
        assert "ORIGINAL_TEST_ACTIVITY_REQUEST_CONTEXT" in prompt
        assert original in prompt
        assert '"iscsi_login"' in prompt
        assert prompt.endswith(repair)

        llm_messages = _build_prompt(
            {"scope_type": "workspace", "scope_id": "ws-retry", "initial_context": {}},
            messages,
            [],
            repair,
            quality_retry_feedback="QUALITY_RETRY_FEEDBACK:\n  previous_score: 66",
        )
        system_prompt = llm_messages[0]["content"]
        assert "ORIGINAL_TEST_ACTIVITY_REQUEST_CONTEXT" in system_prompt
        assert original in system_prompt
        assert '"iscsi_login"' in system_prompt

    async def test_retry_failed_run_api_schedules_without_duplicate_message(
        self,
        sqlite_db,
        monkeypatch,
    ):
        ws_id = await _seed_workspace(sqlite_db)
        from app.api import ai_conversations as ai_api
        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="API 重试",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="分析 iSCSI Login",
            references=[],
        )
        await store.fail_run(created["run"]["id"], "执行失败")
        kicked: list[str] = []
        monkeypatch.setattr(ai_api, "kick_conversation_queue", kicked.append)

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/ai/conversations/{conversation['id']}/runs/{created['run']['id']}/retry"
            )

        assert response.status_code == 202
        body = response.json()
        assert body["message"]["id"] == created["message"]["id"]
        assert kicked == [conversation["id"]]
        assert len(await store.list_messages(conversation["id"])) == 1

    async def test_cancel_while_followup_is_queued_stops_current_run_and_preserves_queue(
        self,
        sqlite_db,
        monkeypatch,
    ):
        ws_id = await _seed_workspace(sqlite_db)

        from app.api import ai_conversations
        from app.services.ai_conversations import AIConversationStore

        class TwoTurnLLM:
            def __init__(self):
                self.started = asyncio.Event()
                self.release = asyncio.Event()
                self.calls = 0

            async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
                self.calls += 1
                if self.calls == 1:
                    self.started.set()
                    yield "第一轮部分输出。"
                    await self.release.wait()
                    yield "第一轮不应落库。"
                    return
                yield "第二轮排队后继续执行。"

        fake_llm = TwoTurnLLM()
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
                    "title": "停止当前运行并保留队列",
                },
            )
            assert created.status_code == 201
            conversation = created.json()

            first = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "第一轮长任务"},
            )
            assert first.status_code == 202
            first_run_id = first.json()["run"]["id"]
            await asyncio.wait_for(fake_llm.started.wait(), timeout=1)

            second = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "第二轮排队追问"},
            )
            assert second.status_code == 202
            second_run_id = second.json()["run"]["id"]
            assert second.json()["run"]["status"] == "queued"

            cancelled = await client.post(f"/api/ai/conversations/{conversation['id']}/cancel")
            assert cancelled.status_code == 200
            assert cancelled.json()["run"]["id"] == first_run_id
            assert cancelled.json()["run"]["status"] == "cancelled"

            store = AIConversationStore(sqlite_db)
            assert (await store.get_run(second_run_id))["status"] == "queued"
            fake_llm.release.set()

            for _ in range(40):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                items = messages.json()["items"]
                if len(items) == 3 and items[-1]["role"] == "assistant":
                    break
                await asyncio.sleep(0.05)

            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            body = messages.json()
            assert [item["role"] for item in body["items"]] == ["user", "user", "assistant"]
            assert body["items"][2]["run_id"] == second_run_id
            assert body["items"][2]["content"] == "第二轮排队后继续执行。"
            assert (await store.get_run(first_run_id))["status"] == "cancelled"
            assert (await store.get_run(second_run_id))["status"] == "completed"

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

            for _ in range(40):
                messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
                if len(messages.json()["items"]) == 2:
                    break
                await asyncio.sleep(0.05)

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
            events = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": assistant["run_id"], "limit": 200},
            )
            assert events.status_code == 200
            live_answer = "\n".join(
                event["payload"].get("content", "")
                for event in events.json()["items"]
                if event["event_type"] == "delta"
                and event["payload"].get("kind") not in {"diagnostic", "thinking", "reasoning", "trace"}
            )
            assert "正在生成结构化产物" in live_answer
            assert "TC-09" not in live_answer
            assert "SFMEA 风险 3" not in live_answer
            download_action = next(
                action for action in assistant["actions"] if action["id"] == "download_run_artifact"
            )
            artifact = await client.get(download_action["href"])
            assert artifact.status_code == 200
            artifact_text = artifact.text
            assert "# 结构化产物线程" in artifact_text
            assert "SFMEA 风险 3" in artifact_text
            assert "TC-09" in artifact_text

    async def test_shallow_source_evidence_blackbox_request_is_rejected_by_quality_gate(
        self,
        sqlite_db,
        monkeypatch,
    ):
        ws_id = await _seed_workspace(sqlite_db)

        from app.api import ai_conversations

        monkeypatch.setattr(
            ai_conversations,
            "create_llm_client_from_active",
            lambda: ShortSourceBlackBoxArtifactLLM(),
        )

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/ai/conversations",
                json={
                    "scope_type": "workspace",
                    "scope_id": ws_id,
                    "title": "短黑盒产物线程",
                },
            )
            conversation = created.json()

            posted = await client.post(
                f"/api/ai/conversations/{conversation['id']}/messages",
                json={"content": "针对 iSCSI 登录写两个黑盒用例，先读源码证据"},
            )
            assert posted.status_code == 202
            for _ in range(60):
                current = await client.get(f"/api/ai/conversations/{conversation['id']}")
                current_body = current.json()
                if current_body.get("latest_run", {}).get("status") == "failed":
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("shallow black-box artifact was not rejected")

            assert "质量门禁" in current_body["latest_run"]["error"]
            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")
            message_items = messages.json()["items"]
            assert [item["role"] for item in message_items] == ["user", "assistant"]
            assert any(
                action.get("id") == "test_activity_task_card"
                for action in message_items[-1]["actions"]
            )

    async def test_legacy_source_blackbox_message_backfills_downloadable_artifact_on_read(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)

        from app.services.ai_conversations import (
            AIConversationStore,
            ai_thread_artifact_path,
        )

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="spdk · E2E 裸工具输出验证 2",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="针对 iSCSI 登录写两个黑盒用例，先读源码证据，但不要把工具输出放进最终答案",
            references=[],
        )
        run_id = created["run"]["id"]
        legacy_content = (
            "## 代码证据\n"
            "- `lib/iscsi/iscsi.c:1539`: CHAP AuthMethod 协商路径。\n\n"
            "## 黑盒测试用例\n"
            "### TC-01 正常登录\n"
            "前置条件：target 已启动；步骤：initiator 发起 iSCSI Login；预期结果：进入 Full Feature Phase。\n"
            "### TC-02 CHAP 失败\n"
            "前置条件：target 开启 CHAP；步骤：使用错误 secret 登录；预期结果：Login Response 拒绝。\n"
        )
        await store.complete_run(
            run_id=run_id,
            content=legacy_content,
            references=[],
            model="agent:legacy",
        )

        artifact_path = ai_thread_artifact_path(conversation["id"], run_id)
        assert not artifact_path.exists()

        messages = await store.list_messages(conversation["id"])
        assistant = messages[1]

        assert "已保存为下载产物" in assistant["content"]
        assert "Login Response 拒绝" not in assistant["content"]
        download_action = next(
            action for action in assistant["actions"] if action["id"] == "download_run_artifact"
        )
        assert download_action["href"] == f"/api/ai/conversations/{conversation['id']}/runs/{run_id}/artifact"
        assert artifact_path.exists()
        artifact_text = artifact_path.read_text(encoding="utf-8")
        assert "# spdk · E2E 裸工具输出验证 2" in artifact_text
        assert "TC-02 CHAP 失败" in artifact_text
        assert "THINKING:" not in artifact_text

        persisted = await store.list_messages(conversation["id"])
        persisted_assistant = persisted[1]
        assert any(action["id"] == "download_run_artifact" for action in persisted_assistant["actions"])

    async def test_compact_artifact_message_reads_existing_artifact_for_rich_preview(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)

        from app.services.ai_conversations import (
            AIConversationStore,
            ai_thread_artifact_path,
        )

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="spdk · AI 调查线程",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="针对 iscsi 登录写几个黑盒用例",
            references=[],
        )
        run_id = created["run"]["id"]
        await store.complete_run(
            run_id=run_id,
            content=(
                "## 结论\n\n"
                "已生成结构化产物（完整产物内容）。为避免长表格和完整用例挤占对话区，正文已收起到下载文件。"
            ),
            references=[],
            model="agent:Claude Code",
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
                    "# spdk · AI 调查线程",
                    "",
                    f"- conversation_id: {conversation['id']}",
                    f"- run_id: {run_id}",
                    "- exported_at: 2026-07-03T00:00:00+00:00",
                    "",
                    "## 结论",
                    "SPDK iSCSI 登录处理应覆盖正常登录、Discovery、缺 InitiatorName、CHAP 失败和异常 PDU。",
                    "",
                    "## 用例设计依据（源码锚点）",
                    "- `lib/iscsi/iscsi.c:1262` 覆盖版本校验。",
                    "- `lib/iscsi/iscsi.c:1332` 覆盖 InitiatorName 缺失。",
                    "",
                    "## 黑盒测试用例",
                    "### TC-01 正常会话登录成功",
                    "前置：target 已启动；步骤：initiator 发起 Normal 登录；预期：进入 Full Feature Phase。",
                    "### TC-02 Discovery 登录成功",
                    "前置：发现会话；步骤：SessionType=Discovery；预期：SendTargets 返回可访问 target。",
                    "### TC-09 超过 MaxConnections",
                    "前置：MaxConnectionsPerSession=N；步骤：发起第 N+1 条连接；预期：TOO_MANY_CONNECTIONS。",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            messages = await client.get(f"/api/ai/conversations/{conversation['id']}/messages")

        assert messages.status_code == 200
        assistant = messages.json()["items"][1]
        assert "已生成结构化产物" in assistant["content"]
        assert "摘要" in assistant["content"]
        assert "SPDK iSCSI 登录处理应覆盖正常登录" in assistant["content"]
        assert "lib/iscsi/iscsi.c:1262" in assistant["content"]
        assert "TC-01 正常会话登录成功" in assistant["content"]
        assert "TC-09" not in assistant["content"]
        assert "下载完整产物" in assistant["content"]
        assert len(assistant["content"]) < 2200

    async def test_agent_final_answer_does_not_truncate_richer_streaming_artifact(
        self,
        sqlite_db,
        monkeypatch,
    ):
        ws_id = await _seed_workspace(sqlite_db)

        from app.services import ai_conversations as ai_service

        async def fake_stream_agent_runtime(**_kwargs):
            yield "我已掌握登录处理链的关键分支。下面基于 `lib/iscsi/iscsi.c` 给出黑盒用例。\n"
            yield "## 结论\n"
            yield "SPDK iSCSI 登录处理覆盖版本、阶段位、InitiatorName、SessionType、target 访问、CHAP 和参数协商。\n"
            yield "## 用例设计依据（源码锚点）\n"
            yield "- 版本校验：`lib/iscsi/iscsi.c:1262` -> `ISCSI_LOGIN_UNSUPPORTED_VERSION`\n"
            yield "- 缺 InitiatorName：`lib/iscsi/iscsi.c:1332` -> `ISCSI_LOGIN_MISSING_PARMS`\n"
            yield "## 黑盒测试用例\n"
            for index in range(1, 10):
                yield (
                    f"{index}. 用例 TC-{index:02d}: 前置条件：target 已启动；"
                    "步骤：发起 iSCSI Login；预期结果：返回可观测 Login Response 状态。\n"
                )
            yield "失败诊断线索：如果 Login Response 未按预期返回，优先排查 target 配置、initiator 参数和 SPDK target 日志。\n"
            yield (
                ai_service.AGENT_FINAL_ANSWER_PREFIX
                + "## 黑盒测试用例\n"
                + "### TC-01 正常会话登录成功\n"
                + "前置条件：target 已启动；步骤：initiator 发起 Login；预期结果：进入 Full Feature Phase。\n"
            )

        monkeypatch.setattr(ai_service, "stream_agent_runtime", fake_stream_agent_runtime)
        monkeypatch.setattr(
            ai_service,
            "_requires_strict_test_activity_quality_gate",
            lambda _message: False,
        )

        store = ai_service.AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            runtime_type="agent_runtime",
            agent_runtime_id="fake-agent",
            title="Agent 完整产物保留",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="针对 iSCSI 登录生成完整黑盒测试用例",
            references=[],
        )
        run_id = created["run"]["id"]

        await ai_service.run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "fake-agent",
                "name": "Fake Agent",
                "provider": "fake",
                "command": "/bin/echo",
                "args": [],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "completion_mode": "process_exit",
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = next(item for item in messages if item["role"] == "assistant")
        assert "已保存为下载产物" in assistant["content"]
        assert "用例设计依据" not in assistant["content"]

        artifact_text = ai_service.ai_thread_artifact_path(conversation["id"], run_id).read_text(
            encoding="utf-8",
        )
        assert "## 结论" in artifact_text
        assert "## 用例设计依据（源码锚点）" in artifact_text
        assert "lib/iscsi/iscsi.c:1262" in artifact_text
        assert "TC-09" in artifact_text
        assert "进入 Full Feature Phase" not in artifact_text

    async def test_agent_run_process_discloses_public_milestones_without_terminal_dump(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        ws_id = await _seed_workspace(sqlite_db)
        repo = tmp_path / "repo"
        source_dir = repo / "lib" / "iscsi"
        source_dir.mkdir(parents=True)
        (source_dir / "iscsi.c").write_text(
            "\n".join(f"int iscsi_line_{index};" for index in range(1, 90)),
            encoding="utf-8",
        )
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute("UPDATE workspaces SET repo_path = ? WHERE id = ?", (str(repo), ws_id))
            await db.commit()

        from app.services import ai_conversations as ai_service

        async def fake_stream_agent_runtime(**_kwargs):
            yield (
                "## 代码证据\n"
                "- `lib/iscsi/iscsi.c:42`: 登录路径证据。\n\n"
                "## 流程梳理\n"
                "1. 接收 initiator login 请求并解析参数。\n"
                "2. 校验 CHAP 认证状态并建立 session。\n"
                "3. 返回 login 响应并进入可提交 IO 的阶段。\n\n"
                "## SFMEA\n"
                "| failure mode | cause | effect | detection | severity | occurrence | detection score | RPN | mitigation |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| FM-01 | auth bypass | unauthorized login | login 日志和认证失败计数 | 9 | 2 | 3 | 54 | 增加非法 CHAP 回归测试 |\n"
                "| FM-02 | digest mismatch | login rejected | RPC 状态、错误码、trace | 7 | 3 | 4 | 84 | 覆盖 header/data digest 组合 |\n"
                "| FM-03 | session reset race | IO hang | session state 与超时指标 | 8 | 2 | 5 | 80 | 增加 reset/reconnect 并发场景 |\n\n"
                "## 黑盒测试用例\n"
                "- 用例 TC-01 非 CHAP 登录失败\n"
                "  前置条件：target 已启动；步骤：发起非法登录；预期结果：认证失败；观测点：login 响应、日志、session 状态；失败诊断线索：若返回成功则检查认证配置。\n"
                "- 用例 TC-02 digest mismatch\n"
                "  前置条件：启用 digest；步骤：使用错误 digest 登录；预期结果：返回错误码；观测点：RPC 状态、错误日志；失败诊断线索：若无错误日志则检查错误路径上报。\n"
                "- 用例 TC-03 session reset 恢复\n"
                "  前置条件：已有连接；步骤：触发 reset 后重新登录；预期结果：旧 session 释放且新 session 可用；观测点：连接状态、超时指标；失败诊断线索：如果 IO 卡住则定位 session 清理。\n"
            )

        monkeypatch.setattr(ai_service, "stream_agent_runtime", fake_stream_agent_runtime)
        monkeypatch.setattr(
            ai_service,
            "_requires_strict_test_activity_quality_gate",
            lambda _message: False,
        )

        store = ai_service.AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            runtime_type="agent_runtime",
            agent_runtime_id="fake-agent",
            title="Agent 过程里程碑",
        )
        refs = await ai_service.build_context_references(
            conversation=conversation,
            user_message="请读取 lib/iscsi/iscsi.c 生成完整 SFMEA 和黑盒测试用例",
            db_path=sqlite_db,
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="请读取 lib/iscsi/iscsi.c 生成完整 SFMEA 和黑盒测试用例",
            references=refs,
        )
        run_id = created["run"]["id"]

        await ai_service.run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "fake-agent",
                "name": "Fake Agent",
                "provider": "fake",
                "command": "/bin/echo",
                "args": [],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "completion_mode": "process_exit",
            },
        )

        messages = await store.list_messages(conversation["id"])
        assistant = next(item for item in messages if item["role"] == "assistant")
        assert "已保存为下载产物" in assistant["content"]
        assert "FM-01" not in assistant["content"]

        app = _test_app(sqlite_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": run_id, "limit": 200, "process_only": True},
            )

        assert response.status_code == 200
        process = "\n".join(
            item["payload"].get("content", "") or item["payload"].get("message", "")
            for item in response.json()["items"]
        )
        assert "CodeTalk 已启动 Fake Agent" in process
        assert "工作区已绑定：repo" in process
        assert f"工作目录：{repo}" not in process
        assert str(repo) not in process
        assert "源码证据已准备：lib/iscsi/iscsi.c" in process
        assert "下载产物已准备" in process
        assert "/api/ai/conversations/" not in process
        assert "```" not in process
        assert "| FM-01 |" not in process
        invocation_events = [
            item
            for item in response.json()["items"]
            if item["payload"].get("artifact_kind") == "agent_invocation"
        ]
        assert invocation_events
        invocation_payload = invocation_events[0]["payload"]
        from app.services.agent_invocation_contract import agent_invocation_typed_events

        assert invocation_payload["execution_contract"]["typed_events"] == agent_invocation_typed_events()
        assert invocation_payload["execution_contract"]["source_first"] is True
        assert invocation_payload["execution_contract"]["outputs"]["user_requested_outputs"] == [
            {
                "source": "test_activity_contract",
                "items": ["sfmea.json", "black_box_cases.json"],
            }
        ]
        assert invocation_payload["artifact_contract"]["required_outputs"] == [
            "sfmea.json",
            "black_box_cases.json",
        ]
        assert invocation_payload["test_activity_contract"]["target"] == (
            "请读取 lib/iscsi/iscsi.c 生成完整 SFMEA 和黑盒测试用例"
        )
        capability_events = [
            item
            for item in response.json()["items"]
            if item["payload"].get("artifact_kind") == "capability_manifest"
        ]
        assert capability_events
        capability_payload = capability_events[0]["payload"]
        assert capability_payload["related_artifacts"] == ["agent_invocation.json"]
        assert capability_payload["runtime"]["provider"] == "fake"
        assert capability_payload["input_contract"]["must_receive_full_user_input"] is True
        assert capability_payload["typed_events"] == agent_invocation_typed_events()
        assert capability_payload["outputs"]["required_artifacts"] == [
            "sfmea.json",
            "black_box_cases.json",
        ]
        assert capability_payload["skills"]["ids"] == []

    async def test_agent_test_activity_uses_same_professional_quality_gate(
        self,
        sqlite_db,
        tmp_path,
        monkeypatch,
    ):
        ws_id = await _seed_workspace(sqlite_db)
        repo = tmp_path / "spdk"
        (repo / "lib" / "iscsi").mkdir(parents=True)
        (repo / "test" / "iscsi_tgt").mkdir(parents=True)
        (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login_probe;\n", encoding="utf-8")
        (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute("UPDATE workspaces SET repo_path = ? WHERE id = ?", (str(repo), ws_id))
            await db.commit()

        from app.services import ai_conversations as ai_service

        async def fake_stream_agent_runtime(**_kwargs):
            yield (
                "## 代码证据\n`lib/iscsi/iscsi.c` 和 `test/iscsi_tgt/login.sh`。\n"
                "## 流程\n1. 接收请求。\n2. 认证。\n3. 返回响应并恢复。\n"
                "## SFMEA\nfailure_mode cause effect detection severity occurrence detection_score RPN "
                "score_explanation mitigation source_evidence test_mapping\n"
                "| bad auth | bad key | reject | logs | 8 | 2 | 3 | 48 | score | retry | "
                "`lib/iscsi/iscsi.c` | `test/iscsi_tgt/login.sh` |\n"
                "## 黑盒测试用例\nnormal_path invalid_input resource_pressure timeout reconnect concurrency recovery performance\n"
                "前置条件：target 启动。步骤：发起登录。预期结果：返回状态。观测点：日志。"
                "失败诊断线索：检查响应。\n## 输入与覆盖\n覆盖八个维度。\n## 未确认项\n无。\n"
                "`iscsi_op_login_response` 是认证和参数协商的核心函数。"
            )

        monkeypatch.setattr(ai_service, "stream_agent_runtime", fake_stream_agent_runtime)
        monkeypatch.setattr(ai_service, "_agent_answer_requires_repair", lambda *_args, **_kwargs: False)

        store = ai_service.AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            runtime_type="agent_runtime",
            agent_runtime_id="fake-agent",
            title="Agent 专业门禁",
        )
        request = "完整生成 iSCSI Login 流程、SFMEA、黑盒测试用例和可下载测试设计"
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content=request,
            references=[],
        )
        run_id = created["run"]["id"]

        await ai_service.run_agent_generation(
            store=store,
            run_id=run_id,
            runtime={
                "id": "fake-agent",
                "name": "Fake Agent",
                "provider": "fake",
                "command": "/bin/echo",
                "args": [],
                "prompt_transport": "stdin",
                "output_mode": "plain",
                "completion_mode": "process_exit",
            },
        )

        run = await store.get_run(run_id)
        assert run["status"] == "failed"
        assert "质量门禁" in run["error"]
        messages = await store.list_messages(conversation["id"])
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert any(
            action.get("id") == "test_activity_task_card"
            for action in messages[-1]["actions"]
        )
        audit_path = (
            ai_service.ai_thread_artifact_path(conversation["id"], run_id).parent
            / "test_activity_quality_audit.json"
        )
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        assert any(issue["code"] == "professional_fact_conflict" for issue in audit["issues"])

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

    async def test_run_events_expose_typed_process_kinds_and_monotonic_seq(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="类型化 Agent 事件",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="分析 iSCSI login 并生成黑盒用例",
            references=[],
        )
        run_id = created["run"]["id"]
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="thinking",
            payload={"content": "正在规划源码检索。"},
        )
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="tool_use",
            payload={"tool": "rg", "input": {"query": "iscsi login", "path": "lib/iscsi"}},
        )
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="tool_result",
            payload={"tool": "rg", "status": "ok", "content": "lib/iscsi/iscsi.c:login"},
        )
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"content": "## 黑盒测试用例\n"},
        )
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"kind": "artifact_progress", "content": "完整内容已保存为下载产物。"},
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            normal = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": run_id, "limit": 20},
            )
            process = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": run_id, "limit": 20, "process_only": True},
            )

        assert normal.status_code == 200
        normal_items = normal.json()["items"]
        assert [item["seq"] for item in normal_items] == [1, 2, 3, 4, 5, 6]
        assert {item["event_kind"] for item in normal_items} >= {
            "status",
            "thinking",
            "tool_use",
            "tool_result",
            "answer",
            "artifact",
        }

        assert process.status_code == 200
        process_items = process.json()["items"]
        process_kinds = [item["event_kind"] for item in process_items]
        assert "answer" not in process_kinds
        assert process_kinds == ["status", "thinking", "tool_use", "tool_result", "artifact"]

        second_conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="第二线程事件序号",
        )
        second_created = await store.create_user_message_and_run(
            conversation_id=second_conversation["id"],
            content="第二个 run 的事件序号也应从 1 开始",
            references=[],
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            second_events = await client.get(
                f"/api/ai/conversations/{second_conversation['id']}/events",
                params={"run_id": second_created["run"]["id"], "limit": 20},
            )
        assert second_events.status_code == 200
        assert [item["seq"] for item in second_events.json()["items"]] == [1]

    async def test_run_event_replay_uses_seq_when_event_ids_are_not_authoritative(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="事件序号排序",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="验证事件按 seq 回放",
            references=[],
        )
        run_id = created["run"]["id"]
        first = await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"content": "event-id-first"},
        )
        second = await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"content": "event-id-second"},
        )
        third = await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"content": "event-id-third"},
        )

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "UPDATE ai_run_events SET seq = 4 WHERE event_id = ?",
                (first["event_id"],),
            )
            await db.execute(
                "UPDATE ai_run_events SET seq = 2 WHERE event_id = ?",
                (second["event_id"],),
            )
            await db.execute(
                "UPDATE ai_run_events SET seq = 3 WHERE event_id = ?",
                (third["event_id"],),
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": run_id, "limit": 20},
            )

        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["seq"] for item in items] == [1, 2, 3, 4]
        assert [item["payload"].get("content") for item in items if item["event_type"] == "delta"] == [
            "event-id-second",
            "event-id-third",
            "event-id-first",
        ]

    async def test_legacy_split_agent_process_events_are_publicly_diagnostic(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="旧版 Agent 事件恢复",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="针对 iSCSI 登录写几个黑盒用例",
            references=[],
        )
        run_id = created["run"]["id"]
        for payload in [
            "THINKING: ",
            "我先核对工作区 iSCSI 登录相关源码，再",
            "据此设计黑盒用例。",
            {"content": "Bash {\"command\":\"grep -n login lib/iscsi/iscsi.c\"}", "kind": "diagnostic"},
            "1125:iscsi_conn_login_pdu_success_complete(void *arg)\n",
            "lib/iscsi/iscsi.c:1539:\tAuthMethod=CHAP\n",
            "THINKING: ",
            "我已掌",
            "握登录处理链的关键分支。下面基于 `lib/iscsi/iscsi.c` 的实际校验逻辑给出黑盒用例",
            "。\n",
            "## 黑盒测试用例\n",
            "### TC-01 正常登录\n",
        ]:
            if isinstance(payload, str):
                payload = {"content": payload}
            await store.append_event(
                run_id=run_id,
                conversation_id=conversation["id"],
                event_type="delta",
                payload=payload,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": run_id, "limit": 50},
            )

        assert response.status_code == 200
        items = response.json()["items"]
        visible_answer = "".join(
            item["payload"].get("content", "")
            for item in items
            if item["event_type"] == "delta"
            and item["payload"].get("kind") not in {"diagnostic", "thinking", "reasoning", "trace"}
        )
        process = "\n".join(
            item["payload"].get("content", "")
            for item in items
            if item["event_type"] == "delta" and item["payload"].get("kind") == "diagnostic"
        )

        assert "## 黑盒测试用例" in visible_answer
        assert "我已掌握登录处理链的关键分支" in visible_answer
        assert "TC-01 正常登录" in visible_answer
        assert "THINKING" not in visible_answer
        assert "iscsi_conn_login_pdu_success_complete" not in visible_answer
        assert "AuthMethod=CHAP" not in visible_answer
        assert "我先核对工作区 iSCSI 登录相关源码" in process
        assert "我已掌握登录处理链的关键分支" not in process
        assert "grep -n login" in process
        assert "iscsi_conn_login_pdu_success_complete" in process
        assert "AuthMethod=CHAP" in process

    async def test_process_only_run_events_restore_legacy_agent_process_after_long_answer(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="旧版 Agent 过程恢复",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="生成完整测试设计",
            references=[],
        )
        run_id = created["run"]["id"]
        for content in [
            "THINKING: ",
            "我先读取 lib/iscsi/iscsi.c。",
            "1125:iscsi_conn_login_pdu_success_complete(void *arg)\n",
            "## 黑盒测试用例\n",
        ]:
            await store.append_event(
                run_id=run_id,
                conversation_id=conversation["id"],
                event_type="delta",
                payload={"content": content},
            )
        for index in range(260):
            await store.append_event(
                run_id=run_id,
                conversation_id=conversation["id"],
                event_type="delta",
                payload={"content": f"answer chunk {index}\n"},
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": run_id, "limit": 200, "process_only": True},
            )

        assert response.status_code == 200
        items = response.json()["items"]
        assert all(
            item["event_type"] in {"status", "error"}
            or item["payload"].get("kind") in {"diagnostic", "thinking", "reasoning", "trace"}
            for item in items
        )
        process = "\n".join(item["payload"].get("content", "") or item["payload"].get("message", "") for item in items)
        assert "我先读取 lib/iscsi/iscsi.c" in process
        assert "iscsi_conn_login_pdu_success_complete" in process
        assert "answer chunk 259" not in process

    async def test_process_only_run_events_do_not_lose_process_after_very_long_answer(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        app = _test_app(sqlite_db)

        from app.services.ai_conversations import AIConversationStore

        store = AIConversationStore(sqlite_db)
        conversation = await store.create_conversation(
            scope_type="workspace",
            scope_id=ws_id,
            workspace_id=ws_id,
            title="超长答案过程恢复",
        )
        created = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="生成完整 SFMEA 和黑盒测试用例",
            references=[],
        )
        run_id = created["run"]["id"]
        for content in [
            "THINKING: ",
            "正在读取 lib/nvmf/ctrlr.c 并规划测试设计。",
            "Bash {\"command\":\"rg nvmf_ctrlr_connect lib/nvmf/ctrlr.c\"}",
            "1032:nvmf_ctrlr_connect(struct spdk_nvmf_request *req)\n",
            "## 黑盒测试用例\n",
        ]:
            await store.append_event(
                run_id=run_id,
                conversation_id=conversation["id"],
                event_type="delta",
                payload={"content": content},
            )
        for index in range(900):
            await store.append_event(
                run_id=run_id,
                conversation_id=conversation["id"],
                event_type="delta",
                payload={"content": f"answer chunk {index}\n"},
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/ai/conversations/{conversation['id']}/events",
                params={"run_id": run_id, "limit": 200, "process_only": True},
            )

        assert response.status_code == 200
        items = response.json()["items"]
        process = "\n".join(item["payload"].get("content", "") or item["payload"].get("message", "") for item in items)
        assert "正在读取 lib/nvmf/ctrlr.c" in process
        assert "rg nvmf_ctrlr_connect" in process
        assert "nvmf_ctrlr_connect" in process
        assert "answer chunk 899" not in process

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
        async with aiosqlite.connect(sqlite_db) as db:
            async with db.execute(
                """
                SELECT content
                FROM ai_messages
                WHERE conversation_id = ? AND role = 'assistant'
                """,
                (conversation["id"],),
            ) as cur:
                raw_assistant_row = await cur.fetchone()
        assert raw_assistant_row is not None
        raw_assistant_content = raw_assistant_row[0]
        assert "THINKING:" not in raw_assistant_content
        assert "iscsi_conn_login_pdu_success_complete" not in raw_assistant_content
        assert "旧版流式残片" not in raw_assistant_content
        assert "TC-01 正常会话登录成功" in raw_assistant_content

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
        assert "已生成结构化产物" in assistant["content"]
        assert "下载完整产物" in assistant["content"]
        assert "旧版 Agent 过程输出" not in assistant["content"]
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

    async def test_legacy_agent_process_leak_does_not_pollute_next_agent_prompt(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)

        from app.services.ai_conversations import (
            AIConversationStore,
            _build_agent_prompt,
            ai_thread_artifact_path,
        )

        legacy_content = "\n".join(
            [
                "THINKING: 我先核对工作区 iSCSI 登录相关源码。",
                "Bash {\"command\": \"grep -n login lib/iscsi/iscsi.c | head -60\"}",
                "1125:iscsi_conn_login_pdu_success_complete(void *arg)",
                "1539:\t\trc = iscsi_op_login_update_param(conn, \"AuthMethod\", \"CHAP\", \"CHAP\");",
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
            title="旧版污染连续线程",
            runtime_type="agent_runtime",
            agent_runtime_id="runtime-history-clean",
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
                    "# 旧版污染连续线程",
                    "",
                    f"- conversation_id: {conversation['id']}",
                    f"- run_id: {run_id}",
                    "- exported_at: 2026-07-03T00:00:00+00:00",
                    "",
                    legacy_content,
                    "",
                ]
            ),
            encoding="utf-8",
        )

        created_next = await store.create_user_message_and_run(
            conversation_id=conversation["id"],
            content="继续细化 CHAP 失败和重连恢复用例",
            references=[],
        )
        messages = await store.list_messages(conversation["id"])
        prompt = _build_agent_prompt(
            conversation,
            messages,
            [],
            created_next["message"]["content"],
            {
                "id": "runtime-history-clean",
                "name": "History Clean Runtime",
                "session_persistence": "none",
            },
        )

        assert "历史助手回复" in prompt
        assert "历史助手完整下载产物" in prompt
        assert "TC-01 正常会话登录成功" in prompt
        assert "CHAP 失败" in prompt
        assert "THINKING:" not in prompt
        assert "Bash {" not in prompt
        assert "grep -n login" not in prompt
        assert "iscsi_conn_login_pdu_success_complete" not in prompt
        assert '1539:\t\trc = iscsi_op_login_update_param(conn, "AuthMethod"' not in prompt


@pytest.mark.asyncio
async def test_builtin_test_activity_rejects_truncated_provider_output(
    sqlite_db,
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services import ai_conversations
    from app.services.ai_conversations import AIConversationStore

    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_artifact_path",
        lambda conversation_id, run_id: tmp_path / conversation_id / f"{run_id}.md",
    )
    monkeypatch.setattr(settings, "ai_conversation_max_output_tokens", 1024)
    monkeypatch.setattr(settings, "llm_max_output_tokens", 8192)
    llm = TruncatedTestActivityLLM()
    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="freeform",
        scope_id="global",
        workspace_id="global",
        title="iSCSI Login 发布门禁",
        initial_context={"repo_path": str(tmp_path / "spdk")},
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content=(
            "详细分析 iSCSI login 流程并输出完整 SFMEA、黑盒测试用例和可下载测试设计文件"
        ),
        references=[],
    )

    await ai_conversations.run_generation(
        store=store,
        run_id=created["run"]["id"],
        llm=llm,
    )

    run = await store.get_run(created["run"]["id"])
    messages = await store.list_messages(conversation["id"])
    assert llm.max_tokens == 8192
    assert llm.temperature == 0.2
    assert run["status"] == "failed"
    assert "输出达到长度上限" in run["error"]
    assert [message["role"] for message in messages] == ["user"]
    assert not (tmp_path / conversation["id"] / f"{created['run']['id']}.md").exists()


@pytest.mark.asyncio
async def test_builtin_test_activity_rejects_shallow_completed_output(
    sqlite_db,
    tmp_path,
    monkeypatch,
):
    from app.services import ai_conversations
    from app.services.ai_conversations import AIConversationStore

    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_artifact_path",
        lambda conversation_id, run_id: tmp_path / conversation_id / f"{run_id}.md",
    )
    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="freeform",
        scope_id="global",
        workspace_id="global",
        title="iSCSI Login 质量门禁",
        initial_context={"repo_path": str(tmp_path / "spdk")},
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="详细输出 iSCSI login 完整流程、SFMEA、黑盒测试用例和测试设计文件",
        references=[],
    )

    await ai_conversations.run_generation(
        store=store,
        run_id=created["run"]["id"],
        llm=ShallowCompletedTestActivityLLM(),
    )

    run = await store.get_run(created["run"]["id"])
    messages = await store.list_messages(conversation["id"])
    assert run["status"] == "failed"
    assert "质量门禁" in run["error"]
    assert "缺少" in run["error"] or "不完整" in run["error"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[-1]["actions"][0]["id"] == "test_activity_task_card"
    rejected_path = (
        tmp_path
        / conversation["id"]
        / "rejected-assistant-output.md"
    )
    assert rejected_path.exists()
    assert "已完成 iSCSI login 测试设计" in rejected_path.read_text(encoding="utf-8")
    assert not (tmp_path / conversation["id"] / f"{created['run']['id']}.md").exists()


@pytest.mark.asyncio
async def test_builtin_comprehensive_test_activity_automatically_runs_stages(
    sqlite_db,
    tmp_path,
    monkeypatch,
):
    from app.services import ai_conversations
    from app.services.ai_conversations import AIConversationStore

    run_root = tmp_path / "runs"
    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_artifact_path",
        lambda conversation_id, run_id: run_root / conversation_id / run_id / "assistant-output.md",
    )
    monkeypatch.setattr(
        ai_conversations,
        "audit_test_activity_response",
        lambda **_kwargs: {
            "kind": "test_activity_quality_audit",
            "status": "accepted",
            "deliverable": True,
            "score": 100,
            "issues": [],
        },
    )
    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="freeform",
        scope_id="global",
        workspace_id="global",
        title="自动分阶段",
        initial_context={"repo_path": str(tmp_path / "spdk")},
    )
    original = "第一行：详细输出 iSCSI login 完整流程、SFMEA、黑盒测试用例和测试设计文件\n第二行：必须保留"
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content=original,
        references=[],
    )
    llm = StagedTestActivityLLM()

    await ai_conversations.run_generation(
        store=store,
        run_id=created["run"]["id"],
        llm=llm,
    )

    run_id = created["run"]["id"]
    run = await store.get_run(run_id)
    messages = await store.list_messages(conversation["id"])
    agent_dir = ai_conversations.ai_thread_agent_artifact_dir(conversation["id"], run_id)
    delivery_dir = ai_conversations.ai_thread_delivery_dir(conversation["id"], run_id)
    assert run["status"] == "completed"
    assert (agent_dir / "staged_execution_plan.json").exists()
    assert len(llm.prompts) == 5
    assert all(original in prompt for prompt in llm.prompts)
    manifest = json.loads((delivery_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert {item["relative_path"] for item in manifest["artifacts"]} == {
        "business_flow.md",
        "sfmea.json",
        "black_box_cases.json",
        "test_design.md",
    }
    assistant = messages[-1]
    assert "business_flow.md" in assistant["content"]
    assert any(action["id"] == "download_run_artifacts_zip" for action in assistant["actions"])


@pytest.mark.asyncio
async def test_downloadable_assistant_artifact_redacts_secrets_before_write(
    tmp_path,
    monkeypatch,
):
    from app.services import ai_conversations

    artifact_path = tmp_path / "assistant-output.md"
    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_artifact_path",
        lambda _conversation_id, _run_id: artifact_path,
    )
    secret = "sk-review-secret-1234567890"

    visible, actions = await ai_conversations._prepare_assistant_delivery(
        run_id="run-secret-download",
        conversation={"id": "conv-secret-download", "title": "安全导出"},
        content=f"# 报告\n\nAuthorization: Bearer bearer-review-secret\napi_key={secret}",
        user_message="请输出可下载测试设计",
        force_artifact=True,
    )

    downloaded = artifact_path.read_text(encoding="utf-8")
    assert secret not in downloaded
    assert "bearer-review-secret" not in downloaded
    assert "<redacted>" in downloaded
    assert secret not in visible
    assert any(action["id"] == "download_run_artifact" for action in actions)


@pytest.mark.asyncio
async def test_downloadable_assistant_delivery_writes_manifest_and_file_actions(
    tmp_path,
    monkeypatch,
):
    from app.services import ai_conversations

    run_root = tmp_path / "conv-manifest" / "run-manifest"
    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_artifact_path",
        lambda _conversation_id, _run_id: run_root / "assistant-output.md",
    )
    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_delivery_dir",
        lambda _conversation_id, _run_id: run_root / "deliverables",
    )

    visible, actions = await ai_conversations._prepare_assistant_delivery(
        run_id="run-manifest",
        conversation={"id": "conv-manifest", "title": "多文件交付"},
        content="# 报告\n\n已完成分析。",
        user_message="请生成可下载报告",
        force_artifact=True,
    )

    manifest = json.loads(
        (run_root / "deliverables" / "artifact_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "accepted"
    assert manifest["artifacts"][0]["relative_path"] == "assistant-output.md"
    action_ids = {action["id"] for action in actions}
    assert "download_run_artifacts_zip" in action_ids
    assert "download_run_artifact_manifest" in action_ids
    assert "交付文件" in visible


@pytest.mark.asyncio
async def test_ai_thread_multi_file_manifest_content_and_zip_endpoints(
    sqlite_db,
    tmp_path,
    monkeypatch,
):
    from app.api import ai_conversations as api_module
    from app.services.ai_conversations import AIConversationStore
    from app.services.ai_thread_artifacts import materialize_ai_thread_manifest

    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="freeform",
        scope_id="global",
        workspace_id="global",
        title="多文件接口",
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="生成报告",
        references=[],
    )
    run_id = created["run"]["id"]
    delivery_dir = tmp_path / conversation["id"] / run_id / "deliverables"
    delivery_dir.mkdir(parents=True)
    (delivery_dir / "report.md").write_text("# report", encoding="utf-8")
    materialize_ai_thread_manifest(
        delivery_dir,
        run_id=run_id,
        declared_artifacts=[
            {"artifact": "report.md", "type": "markdown", "required": True},
        ],
        producer="builtin_llm",
    )
    monkeypatch.setattr(api_module, "_store", lambda: AIConversationStore(sqlite_db))
    monkeypatch.setattr(
        api_module,
        "ai_thread_delivery_dir",
        lambda _conversation_id, _run_id: delivery_dir,
    )

    async with AsyncClient(
        transport=ASGITransport(app=_test_app(sqlite_db)),
        base_url="http://test",
    ) as client:
        manifest = await client.get(
            f"/api/ai/conversations/{conversation['id']}/runs/{run_id}/artifacts/manifest"
        )
        content = await client.get(
            f"/api/ai/conversations/{conversation['id']}/runs/{run_id}/artifacts/content/report.md"
        )
        archive = await client.get(
            f"/api/ai/conversations/{conversation['id']}/runs/{run_id}/artifacts.zip"
        )
        escaped = await client.get(
            f"/api/ai/conversations/{conversation['id']}/runs/{run_id}/artifacts/content/../secret.txt"
        )
        (delivery_dir / "report.md").write_text("# replaced", encoding="utf-8")
        tampered_content = await client.get(
            f"/api/ai/conversations/{conversation['id']}/runs/{run_id}/artifacts/content/report.md"
        )
        tampered_archive = await client.get(
            f"/api/ai/conversations/{conversation['id']}/runs/{run_id}/artifacts.zip"
        )

    assert manifest.status_code == 200
    assert manifest.json()["status"] == "accepted"
    assert content.status_code == 200
    assert content.text == "# report"
    assert archive.status_code == 200
    assert archive.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(BytesIO(archive.content)) as zipped:
        assert sorted(zipped.namelist()) == ["artifact_manifest.json", "report.md"]
    assert escaped.status_code in {400, 404}
    assert tampered_content.status_code == 409
    assert tampered_archive.status_code == 409


@pytest.mark.asyncio
async def test_structured_test_activity_without_completeness_adjective_still_runs_quality_gate(
    sqlite_db,
    tmp_path,
    monkeypatch,
):
    from app.services import ai_conversations
    from app.services.ai_conversations import AIConversationStore

    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_artifact_path",
        lambda conversation_id, run_id: tmp_path / conversation_id / f"{run_id}.md",
    )
    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="freeform",
        scope_id="global",
        workspace_id="global",
        title="普通措辞也必须验收",
        initial_context={"repo_path": str(tmp_path / "spdk")},
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="请为 iSCSI Login 生成 SFMEA 和黑盒测试用例",
        references=[],
    )

    await ai_conversations.run_generation(
        store=store,
        run_id=created["run"]["id"],
        llm=ShallowCompletedTestActivityLLM(),
    )

    run = await store.get_run(created["run"]["id"])
    assert run["status"] == "failed"
    assert "质量门禁" in run["error"]
    assert not (tmp_path / conversation["id"] / f"{created['run']['id']}.md").exists()


async def test_bound_workflow_contract_reaches_builtin_and_agent_prompts_without_losing_user_text(
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.ai_conversations import (
        _build_agent_prompt,
        _build_prompt,
        _runtime_with_bound_workflow,
    )
    from app.services.workflow_dsl import WorkflowStore

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    WorkflowStore(settings.data_path / "workbench" / "workflows.db").save_workflow({
        "id": "thread_bound_flow",
        "name": "线程绑定测试活动",
        "version": 1,
        "inputs": [
            {"id": "analysis_target", "type": "long_text", "required": True},
            {"id": "mr_link", "type": "mr_link", "required": False, "resolver": "agent_mcp"},
        ],
        "steps": [
            {
                "id": "source_analysis",
                "type": "agent_task",
                "provider": "agent-runtime:codex",
                "mcp_profile": "gitnexus+cgc",
                "skills": ["source-evidence-first", "storage-test-design"],
                "goal": "先读源码，再完成测试设计",
                "required_artifacts": ["test_design.md"],
            }
        ],
        "outputs": [
            {
                "id": "test_design",
                "type": "markdown",
                "from": "source_analysis",
                "artifact": "test_design.md",
            }
        ],
    })
    conversation = {
        "id": "conv-bound-flow",
        "title": "绑定工作流线程",
        "scope_type": "workspace",
        "scope_id": "ws-spdk",
        "workspace_id": "ws-spdk",
        "initial_context": {
            "selected_workflow_id": "thread_bound_flow",
            "selected_workflow_name": "线程绑定测试活动",
        },
    }
    user_text = "第一行：分析 iSCSI login\n第二行：MR https://example.test/mr/42\n第三行：输出必须含错误恢复。"

    builtin_prompt = "\n".join(
        item["content"]
        for item in _build_prompt(conversation, [], [], user_text)
    )
    agent_prompt = _build_agent_prompt(
        conversation,
        [],
        [],
        user_text,
        {"id": "codex", "name": "Codex", "completion_mode": "process_exit"},
    )
    merged_runtime = _runtime_with_bound_workflow(
        {"id": "codex", "mcp_profile": "existing", "skills": ["base-skill"]},
        conversation,
    )

    for prompt in (builtin_prompt, agent_prompt):
        assert user_text in prompt
        assert "BOUND_WORKFLOW_EXECUTION_CONTRACT" in prompt
        assert "source_analysis" in prompt
        assert "gitnexus+cgc" in prompt
        assert "source-evidence-first" in prompt
        assert "test_design.md" in prompt
        assert "按依赖顺序执行所有节点" in prompt
    assert merged_runtime["mcp_profile"] == "existing+gitnexus+cgc"
    assert merged_runtime["skills"] == [
        "base-skill",
        "source-evidence-first",
        "storage-test-design",
    ]
    assert merged_runtime["env"]["CODETALK_BOUND_WORKFLOW_ID"] == "thread_bound_flow"


async def test_bound_workflow_missing_builtin_artifact_fails_closed(
    sqlite_db,
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.ai_conversations import AIConversationStore, run_generation
    from app.services.workflow_dsl import WorkflowStore

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    WorkflowStore(settings.data_path / "workbench" / "workflows.db").save_workflow({
        "id": "builtin_required_artifact",
        "name": "内置模型必交付件",
        "version": 1,
        "inputs": [],
        "steps": [{
            "id": "analysis",
            "type": "agent_task",
            "required_artifacts": ["test_design.md", "evidence.md"],
        }],
        "outputs": [
            {
                "id": "test_design",
                "type": "markdown",
                "from": "analysis",
                "artifact": "test_design.md",
            },
            {
                "id": "evidence",
                "type": "markdown",
                "from": "analysis",
                "artifact": "evidence.md",
            },
        ],
    })
    await _seed_workspace(sqlite_db, "ws-bound-builtin-missing")
    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-bound-builtin-missing",
        workspace_id="ws-bound-builtin-missing",
        title="内置模型缺失交付件",
        initial_context={"selected_workflow_id": "builtin_required_artifact"},
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="总结模块入口",
        references=[],
    )

    await run_generation(
        store=store,
        run_id=created["run"]["id"],
        llm=ShallowCompletedTestActivityLLM(),
    )

    run = await store.get_run(created["run"]["id"])
    assert run["status"] == "failed"
    assert "绑定工作流交付件" in run["error"]
    assert "test_design.md" in run["error"]


async def test_bound_workflow_missing_agent_artifact_fails_closed(
    sqlite_db,
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services import ai_conversations
    from app.services.workflow_dsl import WorkflowStore

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    WorkflowStore(settings.data_path / "workbench" / "workflows.db").save_workflow({
        "id": "agent_required_artifact",
        "name": "Agent 必交付件",
        "version": 1,
        "inputs": [],
        "steps": [{
            "id": "analysis",
            "type": "agent_task",
            "required_artifacts": ["test_design.md"],
        }],
        "outputs": [{
            "id": "test_design",
            "type": "markdown",
            "from": "analysis",
            "artifact": "test_design.md",
        }],
    })
    await _seed_workspace(sqlite_db, "ws-bound-agent-missing")

    async def fake_stream_agent_runtime(**_kwargs):
        yield "模块分析完成：入口和异常返回已经识别。"

    monkeypatch.setattr(ai_conversations, "stream_agent_runtime", fake_stream_agent_runtime)
    monkeypatch.setattr(ai_conversations, "_agent_answer_requires_repair", lambda *_args, **_kwargs: False)
    store = ai_conversations.AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-bound-agent-missing",
        workspace_id="ws-bound-agent-missing",
        title="Agent 缺失交付件",
        runtime_type="agent_runtime",
        agent_runtime_id="fake-agent",
        initial_context={"selected_workflow_id": "agent_required_artifact"},
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="总结模块入口",
        references=[],
    )

    await ai_conversations.run_agent_generation(
        store=store,
        run_id=created["run"]["id"],
        runtime={
            "id": "fake-agent",
            "name": "Fake Agent",
            "command": "/bin/echo",
            "args": [],
            "prompt_transport": "stdin",
            "output_mode": "plain",
            "completion_mode": "process_exit",
        },
    )

    run = await store.get_run(created["run"]["id"])
    assert run["status"] == "failed"
    assert "绑定工作流交付件" in run["error"]
    assert "test_design.md" in run["error"]


async def test_test_activity_explanations_do_not_trigger_full_artifact_gate():
    from app.services.ai_conversations import (
        _agent_task_requests_downloadable_artifact,
        _requires_strict_test_activity_quality_gate,
    )

    discussion_requests = (
        "解释这个测试设计背后的风险判断",
        "这个测试用例为什么不合理？",
        "补充黑盒边界条件和异常路径",
    )
    for request in discussion_requests:
        assert _agent_task_requests_downloadable_artifact(request, request) is False
        assert _requires_strict_test_activity_quality_gate(request) is False

    assert _agent_task_requests_downloadable_artifact("请做 iSCSI SFMEA", "请做 iSCSI SFMEA") is True
    assert _requires_strict_test_activity_quality_gate("请做 iSCSI SFMEA") is True


async def test_bound_workflow_artifact_validation_checks_json_schema(
    sqlite_db,
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.ai_conversations import (
        AIConversationStore,
        _enforce_bound_workflow_artifacts,
        ai_thread_agent_artifact_dir,
    )
    from app.services.workflow_dsl import WorkflowStore

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    WorkflowStore(settings.data_path / "workbench" / "workflows.db").save_workflow({
        "id": "schema_required_artifact",
        "name": "Schema 必交付件",
        "version": 1,
        "inputs": [],
        "steps": [{
            "id": "analysis",
            "type": "agent_task",
            "required_artifacts": ["result.json"],
        }],
        "outputs": [{
            "id": "result",
            "type": "json",
            "from": "analysis",
            "artifact": "result.json",
            "schema": {
                "type": "object",
                "required": ["cases"],
                "properties": {"cases": {"type": "array"}},
            },
        }],
    })
    await _seed_workspace(sqlite_db, "ws-bound-schema")
    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-bound-schema",
        workspace_id="ws-bound-schema",
        title="Schema 验收",
        initial_context={"selected_workflow_id": "schema_required_artifact"},
    )
    first = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="第一次执行",
        references=[],
    )
    first_dir = ai_thread_agent_artifact_dir(conversation["id"], first["run"]["id"])
    first_dir.mkdir(parents=True, exist_ok=True)
    (first_dir / "result.json").write_text('{"wrong": []}', encoding="utf-8")

    assert await _enforce_bound_workflow_artifacts(
        store=store,
        run_id=first["run"]["id"],
        conversation=conversation,
    ) is False
    first_run = await store.get_run(first["run"]["id"])
    assert "缺少必填字段 cases" in first_run["error"]

    second = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="第二次执行",
        references=[],
    )
    second_dir = ai_thread_agent_artifact_dir(conversation["id"], second["run"]["id"])
    second_dir.mkdir(parents=True, exist_ok=True)
    (second_dir / "result.json").write_text('{"cases": []}', encoding="utf-8")
    assert await _enforce_bound_workflow_artifacts(
        store=store,
        run_id=second["run"]["id"],
        conversation=conversation,
    ) is True
    validation = json.loads(
        (second_dir / "bound_workflow_artifact_validation.json").read_text(encoding="utf-8")
    )
    assert validation["status"] == "ok"
    assert validation["accepted"] == [{"artifact": "result.json", "size": 13}]


async def test_bound_workflow_delivery_manifest_keeps_each_output_as_a_real_file(
    sqlite_db,
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services import ai_conversations
    from app.services.workflow_dsl import WorkflowStore

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    WorkflowStore(settings.data_path / "workbench" / "workflows.db").save_workflow({
        "id": "multi_file_bound_delivery",
        "name": "多文件交付",
        "version": 1,
        "inputs": [],
        "steps": [{
            "id": "analysis",
            "type": "agent_task",
            "required_artifacts": ["business_flow.md", "sfmea.json"],
        }],
        "outputs": [
            {
                "id": "flow",
                "type": "markdown",
                "from": "analysis",
                "artifact": "business_flow.md",
            },
            {
                "id": "sfmea",
                "type": "json",
                "from": "analysis",
                "artifact": "sfmea.json",
                "schema": {"type": "array", "minItems": 1},
            },
        ],
    })
    conversation = {
        "id": "conv-multi-bound",
        "title": "多文件绑定交付",
        "runtime_type": "builtin_llm",
        "initial_context": {"selected_workflow_id": "multi_file_bound_delivery"},
    }
    agent_dir = tmp_path / "agent-artifacts"
    agent_dir.mkdir()
    (agent_dir / "business_flow.md").write_text("# Flow", encoding="utf-8")
    (agent_dir / "sfmea.json").write_text(
        '[{"failure_mode":"timeout"}]', encoding="utf-8"
    )
    delivery_dir = tmp_path / "deliverables"
    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_agent_artifact_dir",
        lambda _conversation_id, _run_id: agent_dir,
    )
    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_delivery_dir",
        lambda _conversation_id, _run_id: delivery_dir,
    )
    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_artifact_path",
        lambda _conversation_id, _run_id: tmp_path / "assistant-output.md",
    )

    visible, actions = await ai_conversations._prepare_assistant_delivery(
        run_id="run-multi-bound",
        conversation=conversation,
        content="已按工作流生成两个交付件。",
        user_message="执行绑定工作流",
        force_artifact=True,
        artifact_only=True,
    )

    manifest = json.loads((delivery_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "accepted"
    assert {item["relative_path"] for item in manifest["artifacts"]} == {
        "business_flow.md",
        "sfmea.json",
    }
    assert (delivery_dir / "business_flow.md").read_text(encoding="utf-8") == "# Flow"
    assert "business_flow.md" in visible
    assert "sfmea.json" in visible
    assert any(action["id"] == "download_run_artifacts_zip" for action in actions)


async def test_bound_workflow_rejects_symlink_artifact_and_never_copies_target(
    sqlite_db,
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services import ai_conversations
    from app.services.ai_thread_artifacts import ArtifactContractError
    from app.services.workflow_dsl import WorkflowStore

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    WorkflowStore(settings.data_path / "workbench" / "workflows.db").save_workflow({
        "id": "symlink_artifact_rejection",
        "name": "符号链接交付拒绝",
        "version": 1,
        "inputs": [],
        "steps": [{
            "id": "analysis",
            "type": "agent_task",
            "required_artifacts": ["report.md"],
        }],
        "outputs": [{
            "id": "report",
            "type": "markdown",
            "from": "analysis",
            "artifact": "report.md",
        }],
    })
    await _seed_workspace(sqlite_db, "ws-bound-symlink")
    store = ai_conversations.AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-bound-symlink",
        workspace_id="ws-bound-symlink",
        title="符号链接交付拒绝",
        initial_context={"selected_workflow_id": "symlink_artifact_rejection"},
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="生成报告",
        references=[],
    )
    run_id = created["run"]["id"]
    artifact_dir = ai_conversations.ai_thread_agent_artifact_dir(conversation["id"], run_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    host_secret = tmp_path / "host-secret.txt"
    host_secret.write_text("HOST_SECRET_MUST_NOT_LEAK", encoding="utf-8")
    (artifact_dir / "report.md").symlink_to(host_secret)

    assert await ai_conversations._enforce_bound_workflow_artifacts(
        store=store,
        run_id=run_id,
        conversation=conversation,
    ) is False
    failed = await store.get_run(run_id)
    assert "符号链接" in failed["error"]

    delivery_dir = tmp_path / "delivery"
    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_delivery_dir",
        lambda _conversation_id, _run_id: delivery_dir,
    )
    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_artifact_path",
        lambda _conversation_id, _run_id: tmp_path / "assistant-output.md",
    )
    with pytest.raises(ArtifactContractError, match="必需交付文件未生成"):
        await ai_conversations._prepare_assistant_delivery(
            run_id=run_id,
            conversation=conversation,
            content="已生成报告。",
            user_message="生成报告",
            force_artifact=True,
            artifact_only=True,
        )
    assert not (delivery_dir / "report.md").exists()
    assert "HOST_SECRET_MUST_NOT_LEAK" not in "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in delivery_dir.rglob("*")
        if path.is_file()
    )


async def test_agent_thread_artifact_content_excludes_runtime_private_directories(tmp_path):
    from app.services.ai_conversations import (
        _agent_invocation_delivery_contracts,
        _agent_thread_artifact_content,
    )

    artifact_dir = tmp_path / "agent-artifacts"
    runtime_home = artifact_dir / ".runtime-codex-home-test"
    runtime_home.mkdir(parents=True)
    (runtime_home / "plugin.md").write_text(
        "PRIVATE_RUNTIME_PLUGIN_MUST_NOT_BE_DELIVERED",
        encoding="utf-8",
    )
    (runtime_home / "state.json").write_text(
        '{"private":"runtime"}',
        encoding="utf-8",
    )
    (artifact_dir / "deliverable.md").write_text(
        "# SPDK NVMe/TCP TLS report",
        encoding="utf-8",
    )
    (artifact_dir / "black_box_cases.json").write_text(
        '[{"case_id":"BB-001"}]',
        encoding="utf-8",
    )
    (artifact_dir / "test_write.txt").write_text(
        "UNDECLARED_PROBE_MUST_NOT_BE_DELIVERED",
        encoding="utf-8",
    )
    invocation = {
        "test_activity_contract": {
            "required_outputs": ["black_box_cases.json"],
            "artifact_contract": {
                "black_box_cases.json": {
                    "artifact": "black_box_cases.json",
                    "schema": {"type": "array"},
                }
            },
        }
    }
    (artifact_dir / "agent_invocation.json").write_text(
        json.dumps(invocation),
        encoding="utf-8",
    )

    content = await _agent_thread_artifact_content(artifact_dir)

    assert "# SPDK NVMe/TCP TLS report" in content
    assert "BB-001" in content
    assert "PRIVATE_RUNTIME_PLUGIN_MUST_NOT_BE_DELIVERED" not in content
    assert "UNDECLARED_PROBE_MUST_NOT_BE_DELIVERED" not in content
    assert [item["artifact"] for item in _agent_invocation_delivery_contracts(invocation)] == [
        "black_box_cases.json",
        "deliverable.md",
    ]


async def test_fail_run_does_not_overwrite_cancelled_terminal_state(sqlite_db):
    from app.services.ai_conversations import AIConversationStore

    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="freeform",
        scope_id="global",
        workspace_id="global",
        title="取消状态终态保护",
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="开始长任务",
        references=[],
    )
    await store.mark_run_running(created["run"]["id"])
    await store.cancel_run(conversation["id"])

    await store.fail_run(created["run"]["id"], "late provider failure")

    run = await store.get_run(created["run"]["id"])
    assert run["status"] == "cancelled"
    assert run["error"] in {None, ""}


async def test_builtin_staged_cancel_interrupts_provider_and_preserves_run_state(
    sqlite_db,
):
    from app.services.ai_conversations import AIConversationStore, run_generation

    started = asyncio.Event()
    provider_cancelled = asyncio.Event()

    class BlockingStagedLLM:
        async def complete(self, messages, max_tokens=4096, temperature=0.2):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                provider_cancelled.set()
                raise

    workspace_id = await _seed_workspace(sqlite_db, "ws-staged-cancel")
    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id=workspace_id,
        workspace_id=workspace_id,
        title="分阶段取消",
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="请完整输出项目结构、业务流程、SFMEA、黑盒测试用例和测试设计文件",
        references=[],
    )
    generation = asyncio.create_task(
        run_generation(
            store=store,
            run_id=created["run"]["id"],
            llm=BlockingStagedLLM(),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    await store.cancel_run(conversation["id"])

    await asyncio.wait_for(generation, timeout=1)

    run = await store.get_run(created["run"]["id"])
    assert run["status"] == "cancelled"
    assert provider_cancelled.is_set()


async def test_bound_workflow_builtin_materializes_markdown_and_json_envelope(
    sqlite_db,
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.ai_conversations import (
        AIConversationStore,
        ai_thread_agent_artifact_dir,
        run_generation,
    )
    from app.services.workflow_dsl import WorkflowStore

    class BuiltinArtifactEnvelopeLLM:
        async def stream_complete(self, messages, max_tokens=4096, temperature=0.3):
            joined = "\n".join(str(item.get("content") or "") for item in messages)
            assert "BUILTIN_WORKFLOW_ARTIFACT_PROTOCOL" in joined
            yield json.dumps({
                "answer": "已生成模块分析交付件。",
                "artifacts": {
                    "analysis.md": "# 模块分析\n\n入口与错误恢复路径已确认。\n",
                    "result.json": {"summary": "入口已确认", "cases": []},
                },
            }, ensure_ascii=False)

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    WorkflowStore(settings.data_path / "workbench" / "workflows.db").save_workflow({
        "id": "builtin_artifact_success",
        "name": "内置模型文件成功路径",
        "version": 1,
        "inputs": [],
        "steps": [{
            "id": "analysis",
            "type": "agent_task",
            "required_artifacts": ["analysis.md", "result.json"],
        }],
        "outputs": [
            {"id": "analysis", "type": "markdown", "from": "analysis", "artifact": "analysis.md"},
            {
                "id": "result",
                "type": "json",
                "from": "analysis",
                "artifact": "result.json",
                "schema": {
                    "type": "object",
                    "required": ["summary", "cases"],
                    "properties": {
                        "summary": {"type": "string", "minLength": 3},
                        "cases": {"type": "array"},
                    },
                },
            },
        ],
    })
    await _seed_workspace(sqlite_db, "ws-bound-builtin-success")
    store = AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-bound-builtin-success",
        workspace_id="ws-bound-builtin-success",
        title="内置模型文件成功路径",
        initial_context={"selected_workflow_id": "builtin_artifact_success"},
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="总结模块入口",
        references=[],
    )

    await run_generation(
        store=store,
        run_id=created["run"]["id"],
        llm=BuiltinArtifactEnvelopeLLM(),
    )

    run = await store.get_run(created["run"]["id"])
    assert run["status"] == "completed"
    artifact_dir = ai_thread_agent_artifact_dir(conversation["id"], created["run"]["id"])
    assert "入口与错误恢复路径已确认" in (artifact_dir / "analysis.md").read_text(encoding="utf-8")
    assert json.loads((artifact_dir / "result.json").read_text(encoding="utf-8")) == {
        "summary": "入口已确认",
        "cases": [],
    }
    validation = json.loads(
        (artifact_dir / "bound_workflow_artifact_validation.json").read_text(encoding="utf-8")
    )
    assert validation["status"] == "ok"
    messages = await store.list_messages(conversation["id"])
    assistant = [item for item in messages if item["role"] == "assistant"][-1]
    assert any(action["id"] == "download_run_artifact" for action in assistant["actions"])


async def test_bound_workflow_agent_rejects_min_length_schema_violation(
    sqlite_db,
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services import ai_conversations
    from app.services.workflow_dsl import WorkflowStore

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    WorkflowStore(settings.data_path / "workbench" / "workflows.db").save_workflow({
        "id": "agent_min_length_violation",
        "name": "Agent minLength 反例",
        "version": 1,
        "inputs": [],
        "steps": [{
            "id": "analysis",
            "type": "agent_task",
            "required_artifacts": ["result.json"],
        }],
        "outputs": [{
            "id": "result",
            "type": "json",
            "from": "analysis",
            "artifact": "result.json",
            "schema": {
                "type": "object",
                "required": ["summary"],
                "properties": {"summary": {"type": "string", "minLength": 3}},
            },
        }],
    })
    await _seed_workspace(sqlite_db, "ws-bound-agent-min-length")

    async def fake_stream_agent_runtime(**kwargs):
        artifact_dir = Path(kwargs["runtime"]["env"]["CODETALK_AGENT_ARTIFACT_DIR"])
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "result.json").write_text('{"summary": ""}', encoding="utf-8")
        yield "模块分析已完成。"

    monkeypatch.setattr(ai_conversations, "stream_agent_runtime", fake_stream_agent_runtime)
    monkeypatch.setattr(ai_conversations, "_agent_answer_requires_repair", lambda *_args, **_kwargs: False)
    store = ai_conversations.AIConversationStore(sqlite_db)
    conversation = await store.create_conversation(
        scope_type="workspace",
        scope_id="ws-bound-agent-min-length",
        workspace_id="ws-bound-agent-min-length",
        title="Agent minLength 反例",
        runtime_type="agent_runtime",
        agent_runtime_id="fake-agent",
        initial_context={"selected_workflow_id": "agent_min_length_violation"},
    )
    created = await store.create_user_message_and_run(
        conversation_id=conversation["id"],
        content="总结模块入口",
        references=[],
    )

    await ai_conversations.run_agent_generation(
        store=store,
        run_id=created["run"]["id"],
        runtime={
            "id": "fake-agent",
            "name": "Fake Agent",
            "command": "/bin/echo",
            "args": [],
            "prompt_transport": "stdin",
            "output_mode": "plain",
            "completion_mode": "process_exit",
        },
    )

    run = await store.get_run(created["run"]["id"])
    assert run["status"] == "failed"
    assert "长度不能小于 3" in run["error"]
