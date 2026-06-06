import asyncio
import json
import platform
import shutil
import sys
from pathlib import Path

from app.schemas.workspace_analysis import AnalysisObject, LLMLimits
from app.services.workspace_scope_resolver import WorkspaceScopeResolver, _GraphIndex


def test_nvme_tcp_tls_query_expands_to_nvmf_transport_variants():
    from app.services.external_agent_discovery import expand_agent_query_terms

    terms = expand_agent_query_terms("nvme-tcp-tls")

    assert "nvme_tcp_tls" in terms
    assert "nvmf_tcp_tls" in terms
    assert "nvmf_tcp/transport/tls" in terms
    assert "transport/tls" in terms


def test_agent_json_output_is_parsed_and_validated(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "nof" / "nvmf_tcp" / "transport" / "tls"
    src.mkdir(parents=True)
    (src / "tls.c").write_text("int tls_handshake(void) { return 0; }\n", encoding="utf-8")
    raw = json.dumps({
        "candidate_files": [
            {
                "path": "nof/nvmf_tcp/transport/tls/tls.c",
                "reason": "path and content match",
                "confidence": "high",
                "evidence_excerpt": "tls_handshake",
            }
        ],
        "candidate_entries": [],
        "commands": ["rg --files"],
        "raw_summary": "found tls source",
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.status == "ok"
    assert result.candidate_files[0].validated is True
    assert result.candidate_files[0].path == "nof/nvmf_tcp/transport/tls/tls.c"


def test_invalid_json_does_not_enter_candidate_merge(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    result = parse_agent_output("opencode", "not json", tmp_path)

    assert result.status == "invalid_output"
    assert result.candidate_files == []
    assert "not json" in result.raw_summary


def test_agent_output_extracts_json_from_markdown_fence(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    raw = (
        "```json\n"
        + json.dumps({
            "candidate_files": [
                {
                    "path": "src/tls.c",
                    "reason": "real source path",
                    "confidence": "high",
                }
            ]
        })
        + "\n```"
    )

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.status == "ok"
    assert result.candidate_files[0].validated is True
    assert result.candidate_files[0].path == "src/tls.c"


def test_agent_output_unwraps_claude_print_json_result(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "entry.c").write_text("void rpc_entry(void) {}\n", encoding="utf-8")
    discovery_payload = json.dumps({
        "candidate_entries": [
            {
                "entry_kind": "rpc",
                "entry_symbol": "rpc_entry",
                "entry_file": "src/entry.c",
                "chain": ["rpc_entry", "target_fn"],
                "external_trigger": "RPC request",
            }
        ]
    })
    raw = json.dumps({
        "type": "result",
        "subtype": "success",
        "result": discovery_payload,
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.status == "ok"
    assert result.candidate_entries[0].validated is True
    assert result.candidate_entries[0].entry_file == "src/entry.c"


def test_agent_output_reports_success_wrapper_without_discovery_json(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    raw = json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "I do not see a task yet. Startup checks found unrelated files.",
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.status == "invalid_output"
    assert "did not contain discovery JSON" in result.warnings[0]
    assert "I do not see a task" in result.raw_summary


def test_agent_output_rejects_json_without_discovery_schema(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    result = parse_agent_output("claude-code", json.dumps({"foo": "bar"}), tmp_path)

    assert result.status == "invalid_output"
    assert "schema" in result.warnings[0]


def test_agent_output_marks_claude_error_wrapper_as_error(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    raw = json.dumps({
        "type": "result",
        "subtype": "error_max_turns",
        "is_error": True,
        "result": "permission denied while reading workspace",
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.status == "error"
    assert "permission denied" in result.raw_summary


def test_agent_output_parses_requested_source_slices(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    raw = json.dumps({
        "need_source_slices": [
            {
                "file_path": "src/tls.c",
                "symbol": "tls_entry",
                "reason": "need caller context",
            }
        ]
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.need_source_slices[0]["file_path"] == "src/tls.c"
    assert result.need_source_slices[0]["reason"] == "need caller context"


def test_prompt_uses_context_packet_when_present(tmp_path):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, build_agent_prompt

    request = AgentDiscoveryRequest(
        request_id="r1",
        repo_path=str(tmp_path),
        analysis_object_text="nvme-tcp-tls",
        goal="source_scope",
        context_packet={
            "validated_facts": {"files": [{"path": "src/tls.c"}]},
            "rejected_facts": {"files": [{"path": "missing.c", "reason": "file_not_found"}]},
            "raw_summary": "must not be treated as memory",
        },
    )

    prompt = build_agent_prompt(request)

    assert "validated_facts" in prompt
    assert "missing.c" in prompt
    assert "must not be treated as memory" not in prompt


def test_missing_cli_returns_unavailable(monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", lambda _cmd: None)

    health = check_provider_health("claude-code", "claude")

    assert health["status"] == "unavailable"
    assert "claude" in health["reason"]


def test_provider_command_supports_subcommand_style(monkeypatch):
    from app.services.external_agent_discovery import check_provider_health, split_agent_command

    monkeypatch.setattr(
        "app.services.external_agent_discovery.shutil.which",
        lambda cmd: "C:/tools/ccr.exe" if cmd == "ccr" else None,
    )

    assert split_agent_command("ccr code") == ["ccr", "code"]
    health = check_provider_health("claude-code", "ccr code")

    assert health["status"] == "available"
    assert health["argv"][0] == "C:/tools/ccr.exe"
    assert health["argv"][1] == "code"


def test_provider_health_appends_claude_readonly_cli_guard(monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    monkeypatch.setattr(
        "app.services.external_agent_discovery.shutil.which",
        lambda cmd: "C:/tools/claude.cmd" if cmd == "claude" else None,
    )

    health = check_provider_health("claude-code", "claude -p --output-format json")

    assert health["status"] == "available"
    assert "--allowedTools" in health["argv"]
    allowed = health["argv"][health["argv"].index("--allowedTools") + 1]
    assert "Bash(rg:*)" in allowed
    assert "Bash(git grep:*)" in allowed
    assert "--disallowedTools" in health["argv"]
    assert "Edit,Write,NotebookEdit" in health["argv"]


def test_provider_health_launches_resolved_windows_command_path(monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    monkeypatch.setattr(
        "app.services.external_agent_discovery.shutil.which",
        lambda cmd: "C:/Users/me/AppData/Roaming/npm/claude.CMD" if cmd == "claude" else None,
    )

    health = check_provider_health("claude-code", "claude -p --output-format json")

    assert health["status"] == "available"
    assert health["argv"][0] == "C:/Users/me/AppData/Roaming/npm/claude.CMD"
    assert health["attempts"][0]["argv"][0] == "C:/Users/me/AppData/Roaming/npm/claude.CMD"


def test_provider_health_does_not_duplicate_explicit_readonly_guard(monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    monkeypatch.setattr(
        "app.services.external_agent_discovery.shutil.which",
        lambda cmd: "C:/tools/claude.cmd" if cmd == "claude" else None,
    )

    health = check_provider_health(
        "claude-code",
        "claude -p --output-format json --disallowedTools Write",
    )

    assert health["status"] == "available"
    assert health["argv"].count("--disallowedTools") == 1
    assert "Write" in health["argv"]


def test_provider_health_uses_claude_fallback_when_ccr_missing(monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    monkeypatch.setattr(
        "app.services.external_agent_discovery.shutil.which",
        lambda cmd: "C:/tools/claude.cmd" if cmd in {"claude", "where.exe"} else None,
    )

    health = check_provider_health("claude-code", "ccr code -p", fallback_commands=["claude -p"])

    assert health["status"] == "available"
    assert health["argv"][0] == "C:/tools/claude.cmd"
    assert health["argv"][1] == "-p"
    assert health["used_fallback"] is True
    assert health["attempts"][0]["status"] == "unavailable"
    assert health["attempts"][0]["executable"] == "ccr"


def test_provider_health_uses_powershell_fallback_for_shell_only_ccr(monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    monkeypatch.setattr(
        "app.services.external_agent_discovery.shutil.which",
        lambda cmd: "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        if cmd.lower() == "powershell.exe"
        else None,
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.platform.system",
        lambda: "Windows",
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery._probe_windows_shell_command",
        lambda executable: "PowerShell function ccr",
        raising=False,
    )

    health = check_provider_health("claude-code", "ccr code -p --output-format json")

    assert health["status"] == "available"
    assert health["launch_kind"] == "powershell"
    assert health["argv"][0].endswith("powershell.exe")
    assert "& 'ccr' 'code' '-p' '--output-format' 'json'" in health["argv"][-1]
    assert "--allowedTools" in health["argv"][-1]


def test_external_agent_adapter_health_reports_launch_kind(monkeypatch):
    from app.adapters import external_agent as adapter_mod

    def fake_health(provider, command, fallback_commands=None):
        return {
            "provider": provider,
            "status": "available",
            "path": "PowerShell function ccr",
            "launch_kind": "powershell",
            "attempts": [
                {"command": "ccr code -p", "status": "available", "launch_kind": "powershell"},
            ],
        }

    monkeypatch.setattr(adapter_mod, "check_provider_health", fake_health)

    health = asyncio.run(
        adapter_mod.ExternalAgentAdapter("claude-code", "claude_code_command").health_check()
    )

    assert health.is_healthy is True
    assert "launch=powershell" in health.last_check


def test_provider_health_reports_all_attempted_commands_when_unavailable(monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", lambda _cmd: None)

    health = check_provider_health("claude-code", "ccr code -p", fallback_commands=["claude -p"])

    assert health["status"] == "unavailable"
    assert "ccr code -p" in health["reason"]
    assert "claude -p" in health["reason"]
    assert [attempt["executable"] for attempt in health["attempts"]] == ["ccr", "claude"]


def test_provider_health_includes_runtime_diagnostic_when_unavailable(monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", lambda _cmd: None)
    monkeypatch.setattr("app.services.external_agent_discovery.os.getcwd", lambda: "E:/svc/codetalk")
    monkeypatch.setenv("PATH", "C:/agent-bin;D:/tools")

    health = check_provider_health("claude-code", "ccr code -p", fallback_commands=["claude -p"])

    diagnostic = health["diagnostic"]
    assert diagnostic["cwd"] == "E:/svc/codetalk"
    assert diagnostic["path_entries"] == ["C:/agent-bin", "D:/tools"]
    assert "PATH entries: C:/agent-bin | D:/tools" in diagnostic["summary"]


def test_run_provider_unavailable_keeps_runtime_diagnostic_in_result(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    monkeypatch.setattr(
        "app.services.external_agent_discovery.check_provider_health",
        lambda provider, command, fallback_commands=None: {
            "status": "unavailable",
            "reason": "no agent command found; attempted: ccr code -p, claude -p",
            "diagnostic": {
                "summary": "cwd: E:/svc/codetalk; PATH entries: C:/agent-bin | D:/tools"
            },
        },
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="unavailable-diagnostic",
            repo_path=str(tmp_path),
            analysis_object_text="nvme-tcp-tls",
        ),
        providers=["claude-code"],
    ))

    result = results[0]
    assert result.status == "unavailable"
    assert "no agent command found" in result.raw_summary
    assert "PATH entries: C:/agent-bin | D:/tools" in result.raw_summary
    assert result.warnings == [result.raw_summary]


def test_run_provider_reports_nonzero_exit_with_stderr(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    agent = tmp_path / "agent_exit.py"
    agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('{\"candidate_files\": []}')\n"
        "print('auth failed', file=sys.stderr)\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.external_agent_discovery.settings.claude_code_command", f"{sys.executable} {agent}")
    monkeypatch.setattr("app.services.external_agent_discovery.settings.claude_code_fallback_commands", [])

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="nonzero",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
    ))

    assert results[0].status == "error"
    assert "exit code 7" in results[0].raw_summary
    assert "auth failed" in results[0].raw_summary


def test_run_provider_result_uses_session_turn_id(tmp_path, monkeypatch):
    from app.services.agent_discovery_session import create_agent_discovery_session
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    agent = tmp_path / "agent_ok.py"
    agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('{\"candidate_files\": []}')\n",
        encoding="utf-8",
    )
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
        coverage_analysis_id="cov-1",
    )
    monkeypatch.setattr("app.services.external_agent_discovery.settings.claude_code_command", f"{sys.executable} {agent}")
    monkeypatch.setattr("app.services.external_agent_discovery.settings.claude_code_fallback_commands", [])

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="turn-id",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
            goal="coverage_entry",
        ),
        providers=["claude-code"],
        session=session,
    ))

    assert results[0].turn_id == "turn_001_claude_code"
    assert session.turns[0].turn_id == results[0].turn_id


def test_run_provider_waits_for_process_after_timeout(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    class FakeProc:
        returncode = None

        def __init__(self):
            self.killed = False
            self.waited = False

        async def communicate(self, data):
            return b"", b""

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True
            self.returncode = -9
            return self.returncode

    fake_proc = FakeProc()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    async def fake_wait_for(awaitable, timeout):
        close = getattr(awaitable, "close", None)
        if close:
            close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(
        "app.services.external_agent_discovery.check_provider_health",
        lambda provider, command, fallback_commands=None: {
            "status": "available",
            "argv": ["fake-agent"],
            "path": "fake-agent",
        },
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("app.services.external_agent_discovery.asyncio.wait_for", fake_wait_for)

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="timeout-cleanup",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
    ))

    assert results[0].status == "timeout"
    assert fake_proc.killed is True
    assert fake_proc.waited is True


def test_run_provider_waits_for_process_after_successful_communicate(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    class FakeProc:
        returncode = 0

        def __init__(self):
            self.waited = False

        async def communicate(self, data):
            return b'{"candidate_files": []}', b""

        async def wait(self):
            self.waited = True
            return self.returncode

    fake_proc = FakeProc()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(
        "app.services.external_agent_discovery.check_provider_health",
        lambda provider, command, fallback_commands=None: {
            "status": "available",
            "argv": ["fake-agent"],
            "path": "fake-agent",
        },
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="success-cleanup",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
    ))

    assert results[0].status == "ok"
    assert fake_proc.waited is True


def test_run_provider_yields_after_process_wait_on_windows(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    class FakeProc:
        returncode = 0

        async def communicate(self, data):
            return b'{"candidate_files": []}', b""

        async def wait(self):
            return self.returncode

    sleeps: list[float] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProc()

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(
        "app.services.external_agent_discovery.check_provider_health",
        lambda provider, command, fallback_commands=None: {
            "status": "available",
            "argv": ["fake-agent"],
            "path": "fake-agent",
        },
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("app.services.external_agent_discovery.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.services.external_agent_discovery.asyncio.sleep", fake_sleep)

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="windows-cleanup-yield",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
    ))

    assert results[0].status == "ok"
    assert sleeps


def test_run_provider_kills_process_when_cancelled(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    started: asyncio.Event

    class FakeProc:
        returncode = None

        def __init__(self):
            self.killed = False
            self.waited = False

        async def communicate(self, data):
            started.set()
            await asyncio.Event().wait()
            return b"", b""

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True
            self.returncode = -9
            return self.returncode

    fake_proc = FakeProc()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(
        "app.services.external_agent_discovery.check_provider_health",
        lambda provider, command, fallback_commands=None: {
            "status": "available",
            "argv": ["fake-agent"],
            "path": "fake-agent",
        },
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    async def scenario():
        nonlocal started
        started = asyncio.Event()
        task = asyncio.create_task(run_external_agent_discovery(
            AgentDiscoveryRequest(
                request_id="cancel-cleanup",
                repo_path=str(tmp_path),
                analysis_object_text="tls",
            ),
            providers=["claude-code"],
        ))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())

    assert fake_proc.killed is True
    assert fake_proc.waited is True


def test_run_provider_uses_powershell_wrapper_stdin(tmp_path, monkeypatch):
    if not platform.system().lower().startswith("win") or not shutil.which("powershell.exe"):
        import pytest

        pytest.skip("PowerShell wrapper is Windows-specific")

    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    agent = tmp_path / "agent.ps1"
    agent.write_text(
        "$prompt = ($input | Out-String)\n"
        "$hasPrompt = $prompt.Contains('analysis_object_text')\n"
        "Write-Output ('{\"candidate_files\":[],\"warnings\":[\"stdin=' + $hasPrompt.ToString().ToLowerInvariant() + '\"]}')\n",
        encoding="utf-8",
    )
    powershell = shutil.which("powershell.exe")

    def fake_which(cmd):
        return powershell if cmd.lower() == "powershell.exe" else None

    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", fake_which)
    monkeypatch.setattr("app.services.external_agent_discovery.settings.claude_code_command", str(agent))
    monkeypatch.setattr("app.services.external_agent_discovery.settings.claude_code_fallback_commands", [])
    monkeypatch.setattr(
        "app.services.external_agent_discovery._probe_windows_shell_command",
        lambda executable: str(agent),
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="powershell-stdin",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
    ))

    assert results[0].status == "ok"
    assert results[0].warnings == ["stdin=true"]


def test_candidate_outside_repo_and_non_source_are_rejected(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    outside = tmp_path.parent / "outside.c"
    outside.write_text("int outside;\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("docs\n", encoding="utf-8")

    assert validate_agent_candidate_file(tmp_path, str(outside)).validated is False
    assert validate_agent_candidate_file(tmp_path, "README.md").validated is False


def test_agent_candidate_path_with_parent_repo_prefix_validates_from_nested_root(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    repo_root = tmp_path / "nof" / "nvmf_tcp"
    tls_dir = repo_root / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        repo_root,
        "nof/nvmf_tcp/transport/tls/tls.c",
    )

    assert validation.validated is True
    assert validation.path == "transport/tls/tls.c"


def test_agent_directory_candidate_validates_to_source_file(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "README.md").write_text("docs\n", encoding="utf-8")
    (tls_dir / "tls.h").write_text("int tls_h;\n", encoding="utf-8")
    (tls_dir / "tls.c").write_text("int tls_c;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(tmp_path, "nvmf_tcp/transport/tls")

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_duplicate_gitnexus_and_agent_candidate_merges_with_boost(tmp_path):
    from app.schemas.workspace_analysis import ScopeCandidate
    from app.services.external_agent_discovery import (
        AgentCandidateFile,
        AgentDiscoveryResult,
        merge_source_candidates,
    )

    src = tmp_path / "nof" / "nvmf_tcp" / "transport" / "tls"
    src.mkdir(parents=True)
    source = src / "tls.c"
    source.write_text("int tls;\n", encoding="utf-8")

    existing = [
        ScopeCandidate(
            path=str(source),
            source="gitnexus",
            confidence="medium",
            reason="graph match",
            role="related",
        )
    ]
    agent = AgentDiscoveryResult(
        provider="claude-code",
        status="ok",
        candidate_files=[
            AgentCandidateFile(
                path="nof/nvmf_tcp/transport/tls/tls.c",
                reason="agent verified module",
                confidence="high",
                validated=True,
            )
        ],
    )

    merged, warnings = merge_source_candidates(tmp_path, existing, [agent])

    assert warnings == []
    assert len(merged) == 1
    assert merged[0].source == "external_agent"
    assert merged[0].confidence == "high"
    assert "claude-code" in merged[0].reason


def test_workspace_resolver_cancels_agent_task_when_scope_resolution_is_cancelled(tmp_path, monkeypatch):
    import app.services.workspace_scope_resolver as scope_mod

    _write_tls_repo(tmp_path)
    agent_started: asyncio.Event
    agent_cancelled: asyncio.Event
    local_search_started: asyncio.Event

    async def fake_discovery(*args, **kwargs):
        agent_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            agent_cancelled.set()
            raise

    async def fake_path_keyword_repo_hits(*args, **kwargs):
        local_search_started.set()
        await asyncio.Event().wait()
        return []

    monkeypatch.setattr(scope_mod, "run_external_agent_discovery", fake_discovery)
    monkeypatch.setattr(scope_mod, "_path_keyword_repo_hits", fake_path_keyword_repo_hits)

    async def scenario():
        nonlocal agent_started, agent_cancelled, local_search_started
        agent_started = asyncio.Event()
        agent_cancelled = asyncio.Event()
        local_search_started = asyncio.Event()
        task = asyncio.create_task(
            WorkspaceScopeResolver()._resolve_object(
                obj=AnalysisObject(id="obj_tls", text="nvme-tcp-tls", kind="module"),
                ws_id="ws",
                repo_path=str(tmp_path),
                index=_GraphIndex(None),
                limits=LLMLimits(),
                gitnexus_available=False,
            )
        )
        await agent_started.wait()
        await local_search_started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return agent_cancelled.is_set()

    assert asyncio.run(scenario()) is True


def test_merge_source_candidates_includes_agent_warning_detail(tmp_path):
    from app.services.external_agent_discovery import AgentDiscoveryResult, merge_source_candidates

    result = AgentDiscoveryResult(
        provider="claude-code",
        status="invalid_output",
        warnings=["agent output did not contain discovery JSON"],
    )

    merged, warnings = merge_source_candidates(tmp_path, [], [result])

    assert merged == []
    assert warnings == ["claude-code: invalid_output - agent output did not contain discovery JSON"]


def _write_tls_repo(root: Path) -> Path:
    tls_dir = root / "nof" / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    source = tls_dir / "tls.c"
    source.write_text("int nvmf_tcp_tls_handshake(void) { return 0; }\n", encoding="utf-8")
    return source


def _write_tls_tree_at(root: Path, relative_tls_dir: str) -> Path:
    tls_dir = root.joinpath(*relative_tls_dir.split("/"))
    tls_dir.mkdir(parents=True)
    source = tls_dir / "tls.c"
    source.write_text("int nvmf_tcp_tls_handshake(void) { return 0; }\n", encoding="utf-8")
    return source


async def _resolve_nvme_tls(repo_root: Path):
    obj = AnalysisObject(id="obj_tls", text="nvme-tcp-tls", kind="module")
    return await WorkspaceScopeResolver()._resolve_object(
        obj=obj,
        ws_id="ws",
        repo_path=str(repo_root),
        index=_GraphIndex(None),
        limits=LLMLimits(max_files_per_object=8),
        gitnexus_available=False,
    )


def test_workspace_resolver_uses_external_agent_when_frontend_is_repo_root(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentCandidateFile, AgentDiscoveryResult

    source = _write_tls_repo(tmp_path)

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_files=[
                    AgentCandidateFile(
                        path=source.relative_to(tmp_path).as_posix(),
                        reason="agent path search found transport/tls",
                        confidence="high",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(
        "app.services.workspace_scope_resolver.run_external_agent_discovery",
        fake_discovery,
    )

    resolved = asyncio.run(_resolve_nvme_tls(tmp_path))

    assert resolved.candidate_files
    assert resolved.candidate_files[0].source == "external_agent"
    assert resolved.candidate_files[0].role == "primary"
    assert resolved.candidate_files[0].path.replace("\\", "/").endswith(
        "nof/nvmf_tcp/transport/tls/tls.c"
    )


def test_workspace_resolver_local_path_expansion_finds_nvme_tls_without_agent(tmp_path, monkeypatch):
    _write_tls_repo(tmp_path)

    async def fake_discovery(_request, **_kwargs):
        return []

    monkeypatch.setattr(
        "app.services.workspace_scope_resolver.run_external_agent_discovery",
        fake_discovery,
    )

    resolved = asyncio.run(_resolve_nvme_tls(tmp_path))
    paths = [c.path.replace("\\", "/") for c in resolved.candidate_files if c.path]

    assert any(path.endswith("nof/nvmf_tcp/transport/tls/tls.c") for path in paths)
    assert not resolved.warnings


def test_workspace_resolver_finds_nvme_tls_from_nof_repo_root(tmp_path, monkeypatch):
    _write_tls_tree_at(tmp_path, "nvmf_tcp/transport/tls")

    async def fake_discovery(_request, **_kwargs):
        return []

    monkeypatch.setattr(
        "app.services.workspace_scope_resolver.run_external_agent_discovery",
        fake_discovery,
    )

    resolved = asyncio.run(_resolve_nvme_tls(tmp_path))
    paths = [c.path.replace("\\", "/") for c in resolved.candidate_files if c.path]

    assert any(path.endswith("nvmf_tcp/transport/tls/tls.c") for path in paths)
    assert not resolved.warnings


def test_workspace_resolver_finds_nvme_tls_from_nvmf_tcp_repo_root(tmp_path, monkeypatch):
    _write_tls_tree_at(tmp_path, "transport/tls")

    async def fake_discovery(_request, **_kwargs):
        return []

    monkeypatch.setattr(
        "app.services.workspace_scope_resolver.run_external_agent_discovery",
        fake_discovery,
    )

    resolved = asyncio.run(_resolve_nvme_tls(tmp_path))
    paths = [c.path.replace("\\", "/") for c in resolved.candidate_files if c.path]

    assert any(path.endswith("transport/tls/tls.c") for path in paths)
    assert not resolved.warnings


def test_workspace_resolver_writes_agent_session_artifacts(tmp_path, monkeypatch):
    from app.schemas.workspace_analysis import AnalysisPlan
    from app.services.external_agent_discovery import AgentDiscoveryResult

    _write_tls_repo(tmp_path)

    async def fake_discovery(_request, **kwargs):
        session = kwargs.get("session")
        if session is not None:
            session.record_turn(
                provider="claude-code",
                goal="source_scope",
                prompt="prompt",
                raw_output="",
                parsed_result={},
                validation_result={},
                status="unavailable",
            )
        return [AgentDiscoveryResult(provider="claude-code", status="unavailable")]

    monkeypatch.setattr(
        "app.services.workspace_scope_resolver.run_external_agent_discovery",
        fake_discovery,
    )
    plan = AnalysisPlan(
        analysis_objects=[AnalysisObject(id="obj_tls", text="nvme-tcp-tls", kind="module")]
    )
    artifact_dir = tmp_path / "artifacts"

    preview = asyncio.run(
        WorkspaceScopeResolver().resolve(
            ws_id="ws",
            repo_path=str(tmp_path),
            plan=plan,
            task_id="task-1",
            artifact_dir=artifact_dir,
        )
    )

    assert preview.agent_discovery_session_id
    assert preview.external_agent_turn_count >= 1
    assert (artifact_dir / "agent_discovery_session.json").exists()
    assert (artifact_dir / "agent_discovery_ledger.json").exists()


def test_workspace_preview_summarizes_external_agent_warnings(tmp_path, monkeypatch):
    from app.schemas.workspace_analysis import AnalysisPlan
    from app.services.external_agent_discovery import AgentDiscoveryResult

    _write_tls_repo(tmp_path)

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="invalid_output",
                warnings=["agent output did not contain discovery JSON"],
            )
        ]

    monkeypatch.setattr(
        "app.services.workspace_scope_resolver.run_external_agent_discovery",
        fake_discovery,
    )
    plan = AnalysisPlan(
        analysis_objects=[AnalysisObject(id="obj_tls", text="nvme-tcp-tls", kind="module")]
    )

    preview = asyncio.run(
        WorkspaceScopeResolver().resolve(
            ws_id="ws",
            repo_path=str(tmp_path),
            plan=plan,
            task_id="task-1",
            artifact_dir=tmp_path / "artifacts",
        )
    )

    assert preview.external_agent_warnings == [
        "obj_tls: claude-code: invalid_output - agent output did not contain discovery JSON"
    ]


def test_workspace_resolver_keeps_detailed_agent_warning_without_duplicate(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryResult

    _write_tls_repo(tmp_path)

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="invalid_output",
                warnings=["agent output did not contain discovery JSON"],
            )
        ]

    monkeypatch.setattr(
        "app.services.workspace_scope_resolver.run_external_agent_discovery",
        fake_discovery,
    )

    resolved = asyncio.run(_resolve_nvme_tls(tmp_path))

    assert "claude-code: invalid_output - agent output did not contain discovery JSON" in resolved.warnings
    assert "external agent claude-code: invalid_output" not in resolved.warnings


def _coverage_modules(csv_text):
    from app.adapters.coverage import parse_internal_function_hits

    return parse_internal_function_hits(csv_text).modules


def test_coverage_agent_verified_entry_makes_gap_black_box_ready(tmp_path, monkeypatch):
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "session.c").write_text(
        "void internal_recover(void *ctx) {\n"
        "    if (ctx == 0) { return; }\n"
        "    cleanup(ctx);\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "rpc.c").write_text(
        "void rpc_recover_session(struct req *req) { enqueue_recovery(req); }\n",
        encoding="utf-8",
    )

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="rpc",
                        entry_symbol="rpc_recover_session",
                        entry_file="src/rpc.c",
                        chain=["rpc_recover_session", "internal_recover"],
                        external_trigger="RPC recover-session",
                        reason="public RPC handler reaches internal function",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "rec,session,src/session.c:1-4,internal_recover,false,0\n"
    )

    design = asyncio.run(
        build_coverage_test_design(modules, workspace_id="ws-1", repo_path=str(tmp_path))
    )

    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    assert gap["gray_box_required"] is False
    assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
    assert gap["entry_paths"][0]["tool"] == "claude-code"
    assert gap["entry_paths"][0]["entry_symbol"] == "rpc_recover_session"
    assert gap["black_box_cases"]
    case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
    assert "RPC recover-session" in case_text


def test_safe_external_label_preserves_trigger_but_rejects_internal_symbol():
    from app.services.coverage_analyzer import _safe_external_label

    assert _safe_external_label({
        "entry_kind": "rpc",
        "entry_label": "RPC recover-session",
    }) == "RPC recover-session"
    assert _safe_external_label({
        "entry_kind": "rpc",
        "entry_label": "rpc_recover_session",
    }) == "rpc entry"
    assert _safe_external_label({
        "entry_kind": "api",
        "entry_label": "src/rpc.c:12",
    }) == "api entry"


def test_coverage_verified_agent_entry_card_keeps_provider_turn_and_validation(tmp_path, monkeypatch):
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "session.c").write_text(
        "void internal_recover(void *ctx) {\n"
        "    if (ctx == 0) { return; }\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "rpc.c").write_text(
        "void rpc_recover_session(struct req *req) { enqueue_recovery(req); }\n",
        encoding="utf-8",
    )

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="rpc",
                        entry_symbol="rpc_recover_session",
                        entry_file="src/rpc.c",
                        chain=["rpc_recover_session", "internal_recover"],
                        external_trigger="RPC recover-session",
                        reason="public RPC handler reaches internal function",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "rec,session,src/session.c:1-4,internal_recover,false,0\n"
    )

    design = asyncio.run(
        build_coverage_test_design(modules, workspace_id="ws-1", repo_path=str(tmp_path))
    )

    card = design["entry_discovery"]["cards"][0]
    candidate = card["candidate_external_entries"][0]
    assert candidate["provider"] == "claude-code"
    assert candidate["turn_id"] == "coverage:src/session.c:internal_recover:1"
    assert candidate["source_verification"] == "source_backed"
    assert candidate["validation_error"] is None


def test_coverage_agent_entry_collect_prefers_result_turn_id(tmp_path):
    import app.services.coverage_analyzer as coverage_mod
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "rpc.c").write_text("void rpc_recover_session(void) {}\n", encoding="utf-8")
    result = AgentDiscoveryResult(
        provider="claude-code",
        status="ok",
        candidate_entries=[
            AgentCandidateEntry(
                entry_kind="rpc",
                entry_symbol="rpc_recover_session",
                entry_file="src/rpc.c",
                chain=["rpc_recover_session", "internal_recover"],
                external_trigger="RPC recover-session",
                reason="source backed",
                validated=True,
            )
        ],
    )
    result.turn_id = "turn_001_claude_code"
    validated: list[dict] = []
    unverified: list[dict] = []
    raw_results: list[dict] = []

    coverage_mod._collect_agent_entry_results(
        [result],
        repo_root=tmp_path,
        object_id="src/session.c:internal_recover:1",
        turn_id="coverage:src/session.c:internal_recover:1",
        agent_session=None,
        validated_entries=validated,
        unverified_entries=unverified,
        status_by_provider={},
        raw_results=raw_results,
    )

    assert validated[0]["turn_id"] == "turn_001_claude_code"
    assert raw_results[0]["turn_id"] == "turn_001_claude_code"


def test_coverage_scope_enrichment_does_not_start_source_scope_agent(tmp_path, monkeypatch):
    import app.services.coverage_analyzer as coverage_mod
    import app.services.workspace_scope_resolver as scope_mod

    src = tmp_path / "src"
    src.mkdir()
    (src / "util.c").write_text("void internal_helper(void) {}\n", encoding="utf-8")
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "h,util,src/util.c:1-1,internal_helper,false,0\n"
    )
    hit = modules[0].function_hits[0]
    called = False

    async def forbidden_source_scope_agent(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(scope_mod, "run_external_agent_discovery", forbidden_source_scope_agent)

    result = asyncio.run(coverage_mod._resolve_workspace_scope_for_hits(
        [(modules[0], hit)],
        workspace_id="ws-1",
        repo_path=str(tmp_path),
    ))

    assert result
    assert called is False


def test_coverage_agent_unverified_entry_stays_pending(tmp_path, monkeypatch):
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "util.c").write_text(
        "void internal_helper(void) {\n"
        "    if (1) { return; }\n"
        "}\n",
        encoding="utf-8",
    )

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="opencode",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="cli",
                        entry_symbol="maybe_cli",
                        entry_file="missing/cli.c",
                        chain=["maybe_cli", "internal_helper"],
                        external_trigger="CLI maybe",
                        reason="unverified guess",
                        validated=False,
                        validation_error="file_not_found",
                    )
                ],
            )
        ]

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "h,util,src/util.c:1-3,internal_helper,false,0\n"
    )

    design = asyncio.run(
        build_coverage_test_design(modules, workspace_id="ws-1", repo_path=str(tmp_path))
    )

    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    assert gap["entry_paths"] == []
    assert gap["black_box_readiness"]["case_type"] != "black_box_ready"
    card = design["entry_discovery"]["cards"][0]
    candidate = card["candidate_external_entries"][0]
    assert candidate["tool"] == "opencode"
    assert candidate["provider"] == "opencode"
    assert candidate["turn_id"] == "coverage:src/util.c:internal_helper:1"
    assert candidate["source_verification"] == "needs_source_verification"
    assert candidate["validation_error"] == "file_not_found"


def test_coverage_agent_invalid_output_is_visible_in_entry_discovery(tmp_path, monkeypatch):
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design
    from app.services.external_agent_discovery import AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "util.c").write_text(
        "void internal_helper(void) {\n"
        "    if (1) { return; }\n"
        "}\n",
        encoding="utf-8",
    )

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="invalid_output",
                raw_summary="I do not see a task yet",
                warnings=["agent output did not contain discovery JSON"],
            )
        ]

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "h,util,src/util.c:1-3,internal_helper,false,0\n"
    )

    design = asyncio.run(
        build_coverage_test_design(modules, workspace_id="ws-1", repo_path=str(tmp_path))
    )

    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    card = design["entry_discovery"]["cards"][0]

    assert gap["tool_status"]["external_agent"] == "invalid_output"
    assert card["external_agent"]["provider_status"]["claude-code"] == "invalid_output"
    assert card["external_agent"]["warnings"] == ["claude-code: agent output did not contain discovery JSON"]


def test_coverage_agent_source_slice_round2_finds_verified_entry(tmp_path, monkeypatch):
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "internal.c").write_text(
        "void internal_tls_gap(void) {\n"
        "    if (1) { return; }\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "rpc.c").write_text(
        "void rpc_tls_entry(void) { dispatch_tls(); }\n",
        encoding="utf-8",
    )
    calls = []

    async def fake_discovery(request, **kwargs):
        calls.append(request.request_id)
        session = kwargs.get("session")
        if session is not None:
            session.record_turn(
                provider="claude-code",
                goal="coverage_entry",
                prompt="prompt",
                raw_output="{}",
                parsed_result={},
                validation_result={},
                status="ok",
            )
        if "round2" not in request.request_id:
            return [
                AgentDiscoveryResult(
                    provider="claude-code",
                    status="ok",
                    need_source_slices=[
                        {
                            "file_path": "src/rpc.c",
                            "symbol": "rpc_tls_entry",
                            "reason": "need entry context",
                        }
                    ],
                )
            ]
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="rpc",
                        entry_symbol="rpc_tls_entry",
                        entry_file="src/rpc.c",
                        chain=["rpc_tls_entry", "internal_tls_gap"],
                        external_trigger="RPC tls-entry",
                        reason="source slice shows public entry candidate",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "tls,internal,src/internal.c:1-3,internal_tls_gap,false,0\n"
    )
    artifact_dir = tmp_path / "artifacts"

    design = asyncio.run(
        build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
            artifact_dir=artifact_dir,
            analysis_id="cov-1",
        )
    )

    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    assert any("round2" in call for call in calls)
    assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
    assert gap["entry_paths"][0]["tool"] == "claude-code"
    assert (artifact_dir / "agent_discovery_session.json").exists()
    assert (artifact_dir / "external_agent_source_slices" / "slice_001.json").exists()
