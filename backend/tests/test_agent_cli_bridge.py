from app.services.agent_cli_bridge import (
    _codex_add_writable_artifact_dir,
    _looks_like_unattended_permission_request,
    _prompt_argument_or_file_bootstrap,
    _resolve_agent_command,
    clean_agent_output_text,
)


def test_codex_artifact_dir_is_added_as_writable_before_exec():
    args = _codex_add_writable_artifact_dir(
        ["exec", "--json"],
        {"env": {"CODETALK_AGENT_ARTIFACT_DIR": "/tmp/codetalk/run/agent-artifacts"}},
        command="/usr/local/bin/codex",
    )

    assert args == [
        "--add-dir",
        "/tmp/codetalk/run/agent-artifacts",
        "exec",
        "--json",
    ]


def test_codex_artifact_dir_is_not_duplicated():
    args = _codex_add_writable_artifact_dir(
        ["--add-dir", "/tmp/codetalk/run/agent-artifacts", "exec"],
        {"env": {"CODETALK_AGENT_ARTIFACT_DIR": "/tmp/codetalk/run/agent-artifacts"}},
        command="codex",
    )

    assert args.count("--add-dir") == 1


def test_codex_transport_shim_does_not_receive_codex_only_add_dir_flag():
    args = _codex_add_writable_artifact_dir(
        ["fake_codex_agent.py", "exec"],
        {"env": {"CODETALK_AGENT_ARTIFACT_DIR": "/tmp/codetalk/run/agent-artifacts"}},
        command="/usr/bin/python3",
    )

    assert "--add-dir" not in args


def test_large_cli_prompt_uses_prompt_file_bootstrap_without_copying_payload():
    prompt = "完整用户任务\n" + ("证据与约束" * 5000)

    transported = _prompt_argument_or_file_bootstrap(
        prompt,
        prompt_file_path="/tmp/codetalk-agent-prompt.md",
    )

    assert "CODETALK_AGENT_PROMPT_FILE" in transported
    assert "/tmp/codetalk-agent-prompt.md" not in transported
    assert prompt not in transported
    assert len(transported.encode("utf-8")) < 1000


def test_small_cli_prompt_remains_the_direct_argument():
    prompt = "读取工作区并回答"

    assert _prompt_argument_or_file_bootstrap(
        prompt,
        prompt_file_path="/tmp/codetalk-agent-prompt.md",
    ) == prompt


def test_agent_output_cleaning_repairs_local_cjk_mojibake_without_touching_normal_text():
    raw = (
        "已核对源码：`lib/iscsi/iscsi.c`锛氳繛鎺ュ湪寤虹珛鏃朵粠 portal group "
        "缁ф壙 CHAP 绛栫暐锛坄conn.c:192` 起。\n"
        "1. 在 initiator 上发现门户：`iscsiadm -m discovery`銆俓n  2. 灏濊瘯鐧诲綍锛歚iscsiadm --login`。"
    )

    cleaned = clean_agent_output_text(raw)

    assert "已核对源码" in cleaned
    assert "`lib/iscsi/iscsi.c`：连接在建立时从 portal group 继承 CHAP 策略（`conn.c:192` 起。" in cleaned
    assert "。\n  2. 尝试登录：`iscsiadm --login`。" in cleaned
    assert "锛" not in cleaned
    assert "銆" not in cleaned


def test_agent_output_cleaning_strips_cli_usage_banner_without_dropping_answer_options():
    raw = (
        "Usage: claude [options] [prompt]\n"
        "Options:\n"
        "  --print            Print response\n"
        "  --output-format    stream-json\n"
        "Model: claude-sonnet-4\n"
        "Context: /Volumes/Media/dpdk/spdk\n"
        "## 结论\n"
        "FINAL_HELP_NOISE_ANSWER: 已完成源码分析。\n\n"
        "## 黑盒测试用例\n"
        "- 步骤：运行 `spdk_tgt --wait-for-rpc` 后发起 connect。\n"
    )

    cleaned = clean_agent_output_text(raw)

    assert "FINAL_HELP_NOISE_ANSWER" in cleaned
    assert "`spdk_tgt --wait-for-rpc`" in cleaned
    assert "Usage: claude" not in cleaned
    assert "Options:" not in cleaned
    assert "--print" not in cleaned
    assert "Model: claude-sonnet-4" not in cleaned
    assert "Context: /Volumes/Media/dpdk/spdk" not in cleaned


def test_agent_output_cleaning_preserves_code_block_indentation():
    raw = (
        "```python\n"
        "def login_flags(transit=False):\n"
        "    flags = 0\n"
        "    if transit:\n"
        "        flags |= 0x80\n"
        "    return flags\n"
        "def unpack_dsl(bhs):\n"
        "    return (bhs[5] << 16) | (bhs[6] << 8) | bhs[7]\n"
        "```\n"
    )

    cleaned = clean_agent_output_text(raw)

    assert "\n    flags = 0\n" in cleaned
    assert "\n        flags |= 0x80\n" in cleaned
    assert "\n    return (bhs[5] << 16) | (bhs[6] << 8) | bhs[7]\n" in cleaned


def test_agent_output_cleaning_preserves_markdown_table_separator():
    raw = (
        "| Profile | Purpose | Discovery | Normal | Commands |\n"
        "|---|---|---:|---:|---|\n"
        "| P1 | CHAP | yes | no | run |\n"
    )

    cleaned = clean_agent_output_text(raw)

    assert cleaned.rstrip("\n") == raw.rstrip("\n")


def test_unattended_permission_request_is_detected_before_agent_hangs():
    assert _looks_like_unattended_permission_request(
        "Claude requested permissions to write to /repo/report.md, but you haven't granted it yet."
    )
    assert not _looks_like_unattended_permission_request("permission model: readonly; final answer ready")


def test_windows_agent_command_resolution_uses_pathext_cmd_shims(monkeypatch):
    import app.services.agent_cli_bridge as bridge

    seen: list[str] = []

    monkeypatch.setattr(
        bridge.shutil,
        "which",
        lambda command: seen.append(command) or "C:/Users/dev/AppData/Roaming/npm/opencode.cmd",
    )

    assert (
        _resolve_agent_command("opencode", platform_name="nt")
        == "C:/Users/dev/AppData/Roaming/npm/opencode.cmd"
    )
    assert seen == ["opencode"]


def test_windows_agent_command_resolution_keeps_explicit_paths(monkeypatch):
    import app.services.agent_cli_bridge as bridge

    monkeypatch.setattr(bridge.shutil, "which", lambda command: (_ for _ in ()).throw(AssertionError(command)))

    assert _resolve_agent_command("C:/tools/opencode.cmd", platform_name="nt") == "C:/tools/opencode.cmd"
