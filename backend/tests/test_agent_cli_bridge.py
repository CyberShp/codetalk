import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.services.agent_cli_bridge import (
    _codex_add_writable_artifact_dir,
    _looks_like_unattended_permission_request,
    _prompt_argument_or_file_bootstrap,
    _resolve_agent_command,
    _terminate_process,
    clean_agent_output_text,
    stream_agent_runtime,
)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group semantics")
async def test_terminate_process_kills_a_sigterm_ignoring_descendant(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    child_script = (
        "import os,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(child_pid_file)!r}, 'w').write(str(os.getpid())); "
        "time.sleep(60)"
    )
    parent_script = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL, close_fds=True); "
        "time.sleep(60)"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_script,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    child_pid = 0
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not child_pid_file.exists():
            await asyncio.sleep(0.02)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))

        await _terminate_process(proc, process_group=True)

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            state = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(child_pid)],
                check=False,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if not state or state.startswith("Z"):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("SIGTERM-ignoring descendant survived process-group cleanup")
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if child_pid:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group semantics")
async def test_terminate_process_cleans_descendant_after_group_leader_exits(tmp_path):
    child_pid_file = tmp_path / "orphan.pid"
    child_script = (
        "import os,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(child_pid_file)!r}, 'w').write(str(os.getpid())); "
        "time.sleep(60)"
    )
    parent_script = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL, close_fds=True)"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_script,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    child_pid = 0
    try:
        await proc.wait()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not child_pid_file.exists():
            await asyncio.sleep(0.02)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))

        await _terminate_process(proc, process_group=True)

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(child_pid)],
                check=False,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if not state or state.startswith("Z"):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("descendant survived cleanup after process-group leader exited")
    finally:
        if child_pid:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass


def test_build_env_does_not_leak_unrelated_parent_secrets(monkeypatch, tmp_path):
    from app.services.agent_cli_bridge import _build_env
    from app.config import settings

    monkeypatch.setenv("UNRELATED_PRIVATE_SECRET", "must-not-reach-agent")
    monkeypatch.setenv("PATH", "/usr/bin")
    runtime_temp_dir = tmp_path / "runtime-temp"
    monkeypatch.setattr(settings, "runtime_temp_dir", str(runtime_temp_dir))
    env = _build_env({
        "env": {
            "PROVIDER_API_KEY": "explicit-provider-secret",
        }
    })

    assert env["PATH"] == "/usr/bin"
    assert env["PROVIDER_API_KEY"] == "explicit-provider-secret"
    assert "UNRELATED_PRIVATE_SECRET" not in env
    assert "CODETALK_AGENT_ARTIFACT_DIR" not in env
    assert not list(runtime_temp_dir.glob("codetalk-agent-runtime-*"))


@pytest.mark.asyncio
async def test_stream_runtime_removes_internally_owned_artifact_directory(
    monkeypatch,
    tmp_path,
):
    from app.config import settings

    runtime_temp_dir = tmp_path / "runtime-temp"
    monkeypatch.setattr(settings, "runtime_temp_dir", str(runtime_temp_dir))
    output: list[str] = []

    async for chunk in stream_agent_runtime(
        runtime={
            "command": sys.executable,
            "args": ["-c", "import sys; print(sys.stdin.read(), end='')"],
            "prompt_transport": "stdin",
            "output_mode": "plain",
            "completion_mode": "process_exit",
            "sandbox_mode": "off",
        },
        prompt="temporary artifact cleanup",
        cwd=str(tmp_path),
    ):
        output.append(chunk)

    assert "temporary artifact cleanup" in "".join(output)
    assert not list(runtime_temp_dir.glob("codetalk-agent-runtime-*"))


@pytest.mark.asyncio
async def test_stream_runtime_enforces_real_workspace_readonly_sandbox(tmp_path):
    if sys.platform == "darwin" and not shutil.which("sandbox-exec"):
        pytest.skip("sandbox-exec unavailable")
    if sys.platform.startswith("linux") and not (shutil.which("bwrap") or shutil.which("bubblewrap")):
        pytest.skip("bubblewrap unavailable")
    if not (sys.platform == "darwin" or sys.platform.startswith("linux")):
        pytest.skip("macOS/Linux sandbox test")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "source.txt").write_text("source", encoding="utf-8")
    secret = tmp_path / "host-secret.txt"
    secret.write_text("must-not-leak", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    script = (
        'cat >/dev/null; cat "$CODETALK_REPO_PATH/source.txt" > "$CODETALK_AGENT_ARTIFACT_DIR/result.md"; '
        'printf "%s" "$TMPDIR" > "$CODETALK_AGENT_ARTIFACT_DIR/tmpdir.txt"; '
        'printf "%s" "$TMPPREFIX" > "$CODETALK_AGENT_ARTIFACT_DIR/tmpprefix.txt"; '
        'printf runtime > "$TMPDIR/runtime-state.txt"; '
        'if echo forbidden > "$CODETALK_REPO_PATH/blocked.txt"; then echo WRITE_ESCAPED; '
        'else echo SANDBOX_BLOCKED; fi; '
        'if secret_value=$(cat "$CODETALK_HOST_SECRET"); '
        'then printf "%s" "$secret_value" > "$CODETALK_AGENT_ARTIFACT_DIR/leak.txt"; '
        'echo READ_ESCAPED; else echo SANDBOX_READ_BLOCKED; fi'
    )
    output = []

    async for chunk in stream_agent_runtime(
        runtime={
            "command": "/bin/sh",
            "args": ["-c", script],
            "prompt_transport": "stdin",
            "output_mode": "plain",
            "completion_mode": "process_exit",
            "sandbox_mode": "required",
            "sandbox_allow_network": False,
            "env": {
                "CODETALK_AGENT_ARTIFACT_DIR": str(artifacts),
                "CODETALK_REPO_PATH": str(repo),
                "CODETALK_HOST_SECRET": str(secret),
            },
        },
        prompt="read only task",
        cwd=str(repo),
    ):
        output.append(chunk)

    assert "SANDBOX_BLOCKED" in "".join(output)
    assert "WRITE_ESCAPED" not in "".join(output)
    assert "SANDBOX_READ_BLOCKED" in "".join(output)
    assert "READ_ESCAPED" not in "".join(output)
    assert (artifacts / "result.md").read_text(encoding="utf-8") == "source"
    runtime_tmp = Path((artifacts / "tmpdir.txt").read_text(encoding="utf-8"))
    assert runtime_tmp.parent == artifacts.resolve()
    assert runtime_tmp.name.startswith(".runtime-tmp-")
    assert (artifacts / "tmpprefix.txt").read_text(encoding="utf-8") == str(runtime_tmp / "zsh")
    assert not runtime_tmp.exists()
    assert not list(artifacts.glob(".runtime-codex-home-*"))
    assert not (repo / "blocked.txt").exists()
    assert not (artifacts / "leak.txt").exists()
    policy = (artifacts / "sandbox_policy.json").read_text(encoding="utf-8")
    assert '"status": "active"' in policy


@pytest.mark.asyncio
async def test_stream_runtime_allows_configured_local_wrapper_script_readonly(tmp_path):
    if sys.platform == "darwin" and not shutil.which("sandbox-exec"):
        pytest.skip("sandbox-exec unavailable")
    if sys.platform.startswith("linux") and not (shutil.which("bwrap") or shutil.which("bubblewrap")):
        pytest.skip("bubblewrap unavailable")
    if not (sys.platform == "darwin" or sys.platform.startswith("linux")):
        pytest.skip("macOS/Linux sandbox test")

    repo = tmp_path / "repo"
    repo.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    wrapper = runtime_dir / "wrapper.py"
    wrapper.write_text(
        "import sys\nprint('WRAPPER_OK ' + sys.stdin.read().strip(), flush=True)\n",
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    output: list[str] = []

    async for chunk in stream_agent_runtime(
        runtime={
            "command": sys.executable,
            "args": [str(wrapper)],
            "prompt_transport": "stdin",
            "output_mode": "plain",
            "completion_mode": "process_exit",
            "sandbox_mode": "required",
            "env": {"CODETALK_AGENT_ARTIFACT_DIR": str(artifacts)},
        },
        prompt="source evidence",
        cwd=str(repo),
    ):
        output.append(chunk)

    assert "WRAPPER_OK source evidence" in "".join(output)


@pytest.mark.asyncio
async def test_stream_runtime_never_allows_an_absolute_prompt_as_a_read_path(
    tmp_path,
    monkeypatch,
):
    from app.services.agent_sandbox import AgentSandboxLaunch

    repo = tmp_path / "repo"
    repo.mkdir()
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text("import sys\nprint(sys.argv[-1], flush=True)\n", encoding="utf-8")
    private_prompt_path = tmp_path / "host-secret.txt"
    private_prompt_path.write_text("must remain outside sandbox", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    captured: dict[str, object] = {}

    def fake_prepare_agent_sandbox(*, runtime, cwd, artifact_dir):
        captured.update(runtime)
        return AgentSandboxLaunch(
            status="disabled",
            wrapper=[],
            message="test sandbox capture",
            audit={},
        )

    monkeypatch.setattr(
        "app.services.agent_cli_bridge.prepare_agent_sandbox",
        fake_prepare_agent_sandbox,
    )
    output: list[str] = []
    async for chunk in stream_agent_runtime(
        runtime={
            "command": sys.executable,
            "args": [str(wrapper)],
            "prompt_transport": "argv_last",
            "output_mode": "plain",
            "completion_mode": "process_exit",
            "env": {"CODETALK_AGENT_ARTIFACT_DIR": str(artifacts)},
        },
        prompt=str(private_prompt_path),
        cwd=str(repo),
    ):
        output.append(chunk)

    assert str(private_prompt_path) in "".join(output)
    assert str(wrapper.resolve()) in captured["sandbox_read_paths"]
    assert str(private_prompt_path.resolve()) not in captured["sandbox_read_paths"]


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


def test_ai_thread_codex_disables_inner_sandbox_only_with_active_outer_sandbox():
    from app.services.agent_sandbox import codex_command_for_outer_sandbox

    command = ["/Users/dev/.local/bin/codex", "exec", "--json"]

    assert codex_command_for_outer_sandbox(command, sandbox_active=True) == [
        "/Users/dev/.local/bin/codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
    ]
    assert codex_command_for_outer_sandbox(command, sandbox_active=False) == command


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
