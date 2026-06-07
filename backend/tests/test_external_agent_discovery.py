import asyncio
import json
import platform
import shutil
import sys
from pathlib import Path

from app.schemas.workspace_analysis import AnalysisObject, LLMLimits
from app.services.workspace_scope_resolver import WorkspaceScopeResolver, _GraphIndex


def _set_existing_ccr_config(tmp_path, monkeypatch) -> Path:
    config = tmp_path / "router" / "config-router.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('{"server":{"host":"127.0.0.1","port":3456}}\n', encoding="utf-8")
    monkeypatch.setenv("CCR_CONFIG_PATH", str(config))
    return config


def test_nvme_tcp_tls_query_expands_to_nvmf_transport_variants():
    from app.services.external_agent_discovery import expand_agent_query_terms

    terms = expand_agent_query_terms("nvme-tcp-tls")

    assert "nvme_tcp_tls" in terms
    assert "nvmf_tcp_tls" in terms
    assert "nvmf_tcp/transport/tls" in terms
    assert "transport/tls" in terms


def test_nvme_tcp_tls_query_expands_inside_chinese_text_without_spaces():
    from app.services.external_agent_discovery import expand_agent_query_terms

    text = (
        "\u8bf7\u5206\u6790nvme-tcp-tls\u6a21\u5757"
        "\uff0c\u6e90\u7801\u76ee\u5f55\u53ef\u80fd\u5728frontend\u6216nof"
    )
    terms = expand_agent_query_terms(text)

    assert "nvme" in terms
    assert "tls" in terms
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


def test_agent_output_unwraps_openai_choices_message_content(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    discovery_payload = json.dumps({
        "candidate_files": [
            {
                "path": "src/tls.c",
                "reason": "openai-compatible wrapper returned source path",
                "confidence": "high",
            }
        ],
        "commands": ["rg --files"],
    })
    raw = json.dumps({
        "id": "chatcmpl-agent",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": discovery_payload,
                },
                "finish_reason": "stop",
            }
        ],
    })

    result = parse_agent_output("opencode", raw, tmp_path)

    assert result.status == "ok"
    assert result.candidate_files[0].validated is True
    assert result.candidate_files[0].path == "src/tls.c"


def test_agent_output_unwraps_responses_output_text(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "rpc.c").write_text("void rpc_entry(void) {}\n", encoding="utf-8")
    discovery_payload = json.dumps({
        "candidate_entries": [
            {
                "entry_kind": "rpc",
                "entry_symbol": "rpc_entry",
                "entry_file": "src/rpc.c",
                "chain": ["rpc_entry", "target_fn"],
                "external_trigger": "RPC request",
            }
        ],
        "commands": ["rg rpc_entry"],
    })
    raw = json.dumps({
        "id": "resp_agent",
        "output_text": discovery_payload,
    })

    result = parse_agent_output("opencode", raw, tmp_path)

    assert result.status == "ok"
    assert result.candidate_entries[0].validated is True
    assert result.candidate_entries[0].entry_file == "src/rpc.c"


def test_agent_output_unwraps_root_content_blocks(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    discovery_payload = json.dumps({
        "candidate_files": [
            {
                "path": "src/tls.c",
                "reason": "root content block returned discovery JSON",
                "confidence": "high",
            }
        ],
        "raw_summary": "root content parsed",
    })
    raw = json.dumps({
        "type": "message",
        "content": [
            {
                "type": "text",
                "text": discovery_payload,
            }
        ],
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.status == "ok"
    assert result.raw_summary == "root content parsed"
    assert result.candidate_files[0].validated is True
    assert result.candidate_files[0].path == "src/tls.c"


def test_agent_output_prefers_discovery_json_after_stream_metadata(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    discovery_payload = json.dumps({
        "candidate_files": [
            {
                "path": "src/tls.c",
                "reason": "valid source path after stream metadata",
                "confidence": "high",
            }
        ]
    })
    raw = "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": []}}),
        json.dumps({
            "type": "result",
            "subtype": "success",
            "result": discovery_payload,
        }),
    ])

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.status == "ok"
    assert result.candidate_files[0].validated is True
    assert result.candidate_files[0].path == "src/tls.c"


def test_agent_entry_input_hints_are_parsed_for_black_box_cases(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "rpc.c").write_text("void rpc_entry(void) {}\n", encoding="utf-8")
    raw = json.dumps({
        "candidate_entries": [
            {
                "entry_kind": "rpc",
                "entry_symbol": "rpc_entry",
                "entry_file": "src/rpc.c",
                "chain": ["rpc_entry", "target_fn"],
                "external_trigger": "RPC request",
                "input_hints": ["invalid TLS PSK", "oversized capsule"],
            }
        ]
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.status == "ok"
    assert result.candidate_entries[0].input_hints == [
        "invalid TLS PSK",
        "oversized capsule",
    ]


def test_agent_entry_string_input_hint_is_not_split_into_characters(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "rpc.c").write_text("void rpc_entry(void) {}\n", encoding="utf-8")
    raw = json.dumps({
        "candidate_entries": [
            {
                "entry_kind": "rpc",
                "entry_symbol": "rpc_entry",
                "entry_file": "src/rpc.c",
                "external_trigger": "RPC request",
                "input_hints": "invalid TLS PSK",
            }
        ]
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.candidate_entries[0].input_hints == ["invalid TLS PSK"]


def test_agent_string_commands_and_warnings_are_not_split_into_characters(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    raw = json.dumps({
        "candidate_files": [{"path": "src/tls.c"}],
        "commands": "rg --files src",
        "warnings": "used fallback command",
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.commands == ["rg --files src"]
    assert result.warnings == ["used fallback command"]


def test_agent_single_object_candidate_fields_are_parsed(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    (src / "rpc.c").write_text("void rpc_entry(void) {}\n", encoding="utf-8")
    raw = json.dumps({
        "candidate_files": {"path": "src/tls.c", "reason": "single file object"},
        "candidate_entries": {
            "entry_kind": "rpc",
            "entry_symbol": "rpc_entry",
            "entry_file": "src/rpc.c",
        },
        "need_source_slices": {
            "file_path": "src/tls.c",
            "reason": "single slice object",
        },
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.candidate_files[0].path == "src/tls.c"
    assert result.candidate_entries[0].entry_symbol == "rpc_entry"
    assert result.need_source_slices == [
        {"file_path": "src/tls.c", "symbol": None, "reason": "single slice object"}
    ]


def test_agent_candidate_file_path_aliases_are_parsed(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    (src / "rpc.c").write_text("void rpc_entry(void) {}\n", encoding="utf-8")
    raw = json.dumps({
        "candidate_files": [
            {"file_path": "src/tls.c", "reason": "file_path alias"},
        ],
        "candidate_entries": [
            {
                "entry_kind": "rpc",
                "entry_symbol": "rpc_entry",
                "file_path": "src/rpc.c",
            }
        ],
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.candidate_files[0].validated is True
    assert result.candidate_files[0].path == "src/tls.c"
    assert result.candidate_entries[0].validated is True
    assert result.candidate_entries[0].entry_file == "src/rpc.c"


def test_agent_source_slice_path_aliases_are_parsed(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    raw = json.dumps({
        "need_source_slices": {
            "source_file": "src/tls.c",
            "reason": "need source_file alias",
        }
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.need_source_slices == [
        {"file_path": "src/tls.c", "symbol": None, "reason": "need source_file alias"}
    ]


def test_agent_entry_without_source_file_is_not_validated(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    raw = json.dumps({
        "candidate_entries": [
            {
                "entry_kind": "rpc",
                "entry_symbol": "rpc_entry",
                "chain": ["rpc_entry", "target_fn"],
                "external_trigger": "RPC request",
            }
        ]
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.status == "ok"
    assert result.candidate_entries[0].validated is False
    assert result.candidate_entries[0].validation_error == "entry_file_missing"


def test_agent_entry_directory_file_is_not_validated(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    src = tmp_path / "src"
    src.mkdir()
    (src / "rpc.c").write_text("void rpc_entry(void) {}\n", encoding="utf-8")
    raw = json.dumps({
        "candidate_entries": [
            {
                "entry_kind": "rpc",
                "entry_symbol": "rpc_entry",
                "entry_file": "src",
                "chain": ["rpc_entry", "target_fn"],
                "external_trigger": "RPC request",
            }
        ]
    })

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.status == "ok"
    assert result.candidate_entries[0].validated is False
    assert result.candidate_entries[0].entry_file == "src"
    assert result.candidate_entries[0].validation_error == "directory_candidate_not_allowed"


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


def test_agent_output_marks_streamed_claude_error_wrapper_as_error(tmp_path):
    from app.services.external_agent_discovery import parse_agent_output

    raw = "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": []}}),
        json.dumps({
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "api_error_status": "403",
            "result": "network access blocked in intranet",
        }),
    ])

    result = parse_agent_output("claude-code", raw, tmp_path)

    assert result.status == "error"
    assert "error_during_execution" in result.raw_summary
    assert "network access blocked" in result.raw_summary


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


def test_missing_cli_returns_unavailable(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", lambda _cmd: None)
    monkeypatch.setattr(
        "app.services.external_agent_discovery._probe_windows_shell_command",
        lambda _executable: None,
    )
    monkeypatch.setenv("APPDATA", str(tmp_path / "missing-appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "missing-userprofile"))

    health = check_provider_health("claude-code", "claude")

    assert health["status"] == "unavailable"
    assert "claude" in health["reason"]


def test_provider_command_supports_subcommand_style(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health, split_agent_command

    _set_existing_ccr_config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.services.external_agent_discovery.shutil.which",
        lambda cmd: "C:/tools/ccr.exe" if cmd == "ccr" else None,
    )

    assert split_agent_command("ccr code") == ["ccr", "code"]
    health = check_provider_health("claude-code", "ccr code")

    assert health["status"] == "available"
    assert health["argv"][0] == "C:/tools/ccr.exe"
    assert health["argv"][1] == "code"


def test_provider_command_strips_quotes_from_absolute_executable_path(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health, split_agent_command

    agent_dir = tmp_path / "Program Files" / "ccr"
    agent_dir.mkdir(parents=True)
    agent = agent_dir / "ccr.cmd"
    agent.write_text("@echo off\n", encoding="utf-8")
    command = f'"{agent}" code -p --output-format json'

    _set_existing_ccr_config(tmp_path, monkeypatch)
    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", lambda _cmd: None)

    assert split_agent_command(command)[0] == str(agent)
    health = check_provider_health("claude-code", command)

    assert health["status"] == "available"
    assert health["argv"][0] == str(agent)
    assert health["argv"][1:5] == ["code", "-p", "--output-format", "json"]


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


def test_provider_health_uses_claude_fallback_when_ccr_missing(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    monkeypatch.setattr(
        "app.services.external_agent_discovery.shutil.which",
        lambda cmd: "C:/tools/claude.cmd" if cmd in {"claude", "where.exe"} else None,
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery._probe_windows_shell_command",
        lambda _executable: None,
    )
    monkeypatch.setenv("APPDATA", str(tmp_path / "missing-appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "missing-userprofile"))

    health = check_provider_health("claude-code", "ccr code -p", fallback_commands=["claude -p"])

    assert health["status"] == "available"
    assert health["argv"][0] == "C:/tools/claude.cmd"
    assert health["argv"][1] == "-p"
    assert health["used_fallback"] is True
    assert health["attempts"][0]["status"] == "unavailable"
    assert health["attempts"][0]["executable"] == "ccr"


def test_provider_health_does_not_block_ccr_for_missing_default_config(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    ccr = tmp_path / "bin" / "ccr.cmd"
    claude = tmp_path / "bin" / "claude.cmd"
    ccr.parent.mkdir()
    ccr.write_text("@echo off\n", encoding="utf-8")
    claude.write_text("@echo off\n", encoding="utf-8")

    def fake_which(cmd):
        if cmd == "ccr":
            return str(ccr)
        if cmd == "claude":
            return str(claude)
        return None

    monkeypatch.setattr("app.services.external_agent_discovery.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", fake_which)
    monkeypatch.delenv("CCR_CONFIG_PATH", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))

    health = check_provider_health(
        "claude-code",
        "ccr code -p --output-format json",
        fallback_commands=["claude -p --output-format json"],
    )

    assert health["status"] == "available"
    assert health["argv"][0] == str(ccr)
    assert health["used_fallback"] is False
    assert len(health["attempts"]) == 1
    assert health["attempts"][0]["status"] == "available"


def test_provider_health_uses_fallback_after_explicit_missing_ccr_config(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    ccr = tmp_path / "bin" / "ccr.cmd"
    claude = tmp_path / "bin" / "claude.cmd"
    ccr.parent.mkdir()
    ccr.write_text("@echo off\n", encoding="utf-8")
    claude.write_text("@echo off\n", encoding="utf-8")
    missing_config = tmp_path / "missing" / "config-router.json"

    def fake_which(cmd):
        if cmd == "ccr":
            return str(ccr)
        if cmd == "claude":
            return str(claude)
        return None

    monkeypatch.setattr("app.services.external_agent_discovery.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", fake_which)
    monkeypatch.delenv("CCR_CONFIG_PATH", raising=False)

    health = check_provider_health(
        "claude-code",
        f'ccr code --config "{missing_config}" -p --output-format json',
        fallback_commands=["claude -p --output-format json"],
    )

    assert health["status"] == "available"
    assert health["argv"][0] == str(claude)
    assert health["used_fallback"] is True
    assert "ccr config file not found" in health["reason"]
    assert len(health["attempts"]) == 2
    assert health["attempts"][0]["status"] == "configuration_error"
    assert health["attempts"][0]["config_path"] == str(missing_config)
    assert health["attempts"][1]["status"] == "available"


def test_provider_health_accepts_existing_ccr_config_path(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    ccr = tmp_path / "bin" / "ccr.cmd"
    config = tmp_path / "router" / "config-router.json"
    ccr.parent.mkdir()
    config.parent.mkdir()
    ccr.write_text("@echo off\n", encoding="utf-8")
    config.write_text('{"server":{"host":"127.0.0.1","port":3456}}\n', encoding="utf-8")

    monkeypatch.setattr("app.services.external_agent_discovery.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "app.services.external_agent_discovery.shutil.which",
        lambda cmd: str(ccr) if cmd == "ccr" else None,
    )

    health = check_provider_health(
        "claude-code",
        f'ccr code --config "{config}" -p --output-format json',
    )

    assert health["status"] == "available"
    assert health["argv"][0] == str(ccr)
    assert "--config" in health["argv"]


def test_provider_fallback_command_list_preserves_semicolons_inside_quotes(tmp_path, monkeypatch):
    from app.services import external_agent_discovery as discovery

    agent_dir = tmp_path / "Tools;Beta"
    agent_dir.mkdir()
    agent = agent_dir / "ccr.cmd"
    agent.write_text("@echo off\n", encoding="utf-8")

    _set_existing_ccr_config(tmp_path, monkeypatch)
    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", lambda _cmd: None)
    monkeypatch.setattr(
        discovery.settings,
        "claude_code_fallback_commands",
        f'"{agent}" code -p; claude -p',
    )

    commands = discovery.provider_fallback_commands("claude-code")
    health = discovery.check_provider_health(
        "claude-code",
        "missing-agent",
        fallback_commands=commands,
    )

    assert commands[0] == f'"{agent}" code -p'
    assert commands[1] == "claude -p"
    assert health["status"] == "available"
    assert health["argv"][0] == str(agent)
    assert health["used_fallback"] is True


def test_settings_accept_plain_string_fallback_commands(monkeypatch):
    from app.config import Settings
    from app.services.external_agent_discovery import _coerce_command_list

    monkeypatch.setenv("CLAUDE_CODE_FALLBACK_COMMANDS", "claude -p --output-format json")

    configured = Settings().claude_code_fallback_commands

    assert configured == "claude -p --output-format json"
    assert _coerce_command_list(configured) == ["claude -p --output-format json"]


def test_command_list_accepts_json_array_string():
    from app.services.external_agent_discovery import _coerce_command_list

    assert _coerce_command_list('["ccr code -p", "claude -p --output-format json"]') == [
        "ccr code -p",
        "claude -p --output-format json",
    ]


def test_settings_accept_empty_fallback_commands(monkeypatch):
    from app.config import Settings
    from app.services.external_agent_discovery import _coerce_command_list

    monkeypatch.setenv("CLAUDE_CODE_FALLBACK_COMMANDS", "")

    configured = Settings().claude_code_fallback_commands

    assert configured == ""
    assert _coerce_command_list(configured) == []


def test_provider_health_finds_windows_npm_command_when_service_path_misses_it(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    ccr_cmd = npm_dir / "ccr.cmd"
    ccr_cmd.write_text("@echo off\n", encoding="utf-8")

    _set_existing_ccr_config(tmp_path, monkeypatch)
    monkeypatch.setattr("app.services.external_agent_discovery.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", lambda _cmd: None)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("PATH", "C:/Windows/System32")

    health = check_provider_health("claude-code", "ccr code -p --output-format json")

    assert health["status"] == "available"
    assert health["argv"][0] == str(ccr_cmd)
    assert health["argv"][1:5] == ["code", "-p", "--output-format", "json"]


def test_provider_health_uses_powershell_fallback_for_shell_only_ccr(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    _set_existing_ccr_config(tmp_path, monkeypatch)
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
    monkeypatch.setenv("APPDATA", str(tmp_path / "missing-appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "missing-userprofile"))

    health = check_provider_health("claude-code", "ccr code -p --output-format json")

    assert health["status"] == "available"
    assert health["launch_kind"] == "powershell"
    assert health["argv"][0].endswith("powershell.exe")
    assert "& 'ccr' 'code' '-p' $__codetalkPrompt '--output-format' 'json'" in health["argv"][-1]
    assert "--allowedTools" in health["argv"][-1]


def test_provider_health_powershell_print_mode_replaces_placeholder(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    _set_existing_ccr_config(tmp_path, monkeypatch)
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
    monkeypatch.setenv("APPDATA", str(tmp_path / "missing-appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "missing-userprofile"))

    health = check_provider_health(
        "claude-code",
        "ccr code -p configured-placeholder --output-format json",
    )

    assert health["status"] == "available"
    assert "& 'ccr' 'code' '-p' $__codetalkPrompt '--output-format' 'json'" in health["argv"][-1]
    assert "configured-placeholder" not in health["argv"][-1]


def test_provider_health_probes_shell_only_ccr_with_execution_policy_bypass(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    _set_existing_ccr_config(tmp_path, monkeypatch)
    captured: dict = {}

    class Completed:
        returncode = 0
        stdout = "Function ccr\n"

    def fake_which(cmd):
        if cmd.lower() == "powershell.exe":
            return "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        return None

    def fake_run(args, **kwargs):
        captured["args"] = args
        if "-ExecutionPolicy" not in args or "Bypass" not in args:
            raise AssertionError("PowerShell probe must bypass execution policy")
        return Completed()

    monkeypatch.setattr("app.services.external_agent_discovery.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", fake_which)
    monkeypatch.setattr("app.services.external_agent_discovery.subprocess.run", fake_run)
    monkeypatch.setenv("APPDATA", str(tmp_path / "missing-appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "missing-userprofile"))

    health = check_provider_health("claude-code", "ccr code -p --output-format json")

    assert health["status"] == "available"
    assert health["launch_kind"] == "powershell"
    assert "-ExecutionPolicy" in captured["args"]
    assert "-NoProfile" not in captured["args"]


def test_provider_health_wraps_windows_ps1_agent_with_powershell(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    ccr_ps1 = npm_dir / "ccr.ps1"
    ccr_ps1.write_text("param($Prompt)\n", encoding="utf-8")

    _set_existing_ccr_config(tmp_path, monkeypatch)
    def fake_which(cmd):
        if cmd.lower() == "powershell.exe":
            return "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        return None

    monkeypatch.setattr("app.services.external_agent_discovery.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", fake_which)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("PATH", "C:/Windows/System32")

    health = check_provider_health("claude-code", "ccr code -p --output-format json")

    assert health["status"] == "available"
    assert health["launch_kind"] == "powershell-script"
    assert health["configured_argv"][0] == str(ccr_ps1)
    assert "& '" + str(ccr_ps1).replace("'", "''") + "'" in health["argv"][-1]


def test_find_powershell_uses_systemroot_when_service_path_is_thin(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import _find_powershell

    powershell = tmp_path / "Windows" / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    powershell.parent.mkdir(parents=True)
    powershell.write_text("", encoding="utf-8")

    monkeypatch.setattr("app.services.external_agent_discovery.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", lambda _cmd: None)
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))

    assert _find_powershell() == str(powershell)


def test_external_agent_adapter_health_reports_launch_kind(monkeypatch):
    from app.adapters import external_agent as adapter_mod

    def fake_health(provider, command, fallback_commands=None):
        return {
            "provider": provider,
            "status": "available",
            "path": "PowerShell function ccr",
            "launch_kind": "powershell",
            "reason": "primary command unavailable; using fallback: claude -p",
            "attempts": [
                {"command": "ccr code -p", "status": "unavailable", "reason": "command not found: ccr"},
                {"command": "claude -p", "status": "available", "launch_kind": "powershell"},
            ],
        }

    monkeypatch.setattr(adapter_mod, "check_provider_health", fake_health)

    health = asyncio.run(
        adapter_mod.ExternalAgentAdapter("claude-code", "claude_code_command").health_check()
    )

    assert health.is_healthy is True
    assert "launch=powershell" in health.last_check
    assert "command not found: ccr" in health.last_check


def test_external_agent_adapter_analyze_returns_json_safe_nested_results(tmp_path, monkeypatch):
    from app.adapters import external_agent as adapter_mod
    from app.adapters.base import AnalysisRequest
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="rpc",
                        entry_symbol="rpc_entry",
                        entry_file="src/rpc.c",
                        chain=["rpc_entry", "target_fn"],
                        external_trigger="RPC request",
                        input_hints=["invalid TLS PSK"],
                        reason="public RPC entry",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(adapter_mod, "run_external_agent_discovery", fake_discovery)

    result = asyncio.run(
        adapter_mod.ExternalAgentAdapter("claude-code", "claude_code_command").analyze(
            AnalysisRequest(repo_local_path=str(tmp_path))
        )
    )

    json.dumps(result.data)
    entry = result.data["results"][0]["candidate_entries"][0]
    assert entry["entry_symbol"] == "rpc_entry"
    assert entry["input_hints"] == ["invalid TLS PSK"]


def test_provider_health_reports_all_attempted_commands_when_unavailable(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", lambda _cmd: None)
    monkeypatch.setattr(
        "app.services.external_agent_discovery._probe_windows_shell_command",
        lambda _executable: None,
    )
    monkeypatch.setenv("APPDATA", str(tmp_path / "missing-appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "missing-userprofile"))

    health = check_provider_health("claude-code", "ccr code -p", fallback_commands=["claude -p"])

    assert health["status"] == "unavailable"
    assert "ccr code -p" in health["reason"]
    assert "claude -p" in health["reason"]
    assert [attempt["executable"] for attempt in health["attempts"]] == ["ccr", "claude"]


def test_provider_health_includes_runtime_diagnostic_when_unavailable(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import check_provider_health

    monkeypatch.setattr("app.services.external_agent_discovery.shutil.which", lambda _cmd: None)
    monkeypatch.setattr("app.services.external_agent_discovery.os.getcwd", lambda: "E:/svc/codetalk")
    monkeypatch.setenv("APPDATA", str(tmp_path / "missing-appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "missing-userprofile"))
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


def test_run_external_agent_discovery_keeps_other_provider_when_one_crashes(tmp_path, monkeypatch):
    import app.services.external_agent_discovery as agent_mod
    from app.services.external_agent_discovery import AgentDiscoveryRequest, AgentDiscoveryResult

    async def fake_run_provider(provider, _request, **_kwargs):
        if provider == "opencode":
            raise RuntimeError("opencode wrapper crashed")
        return AgentDiscoveryResult(provider=provider, status="ok", raw_summary="usable result")

    monkeypatch.setattr(agent_mod, "_run_provider", fake_run_provider)

    results = asyncio.run(agent_mod.run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="req",
            repo_path=str(tmp_path),
            analysis_object_text="nvme-tcp-tls",
        ),
        providers=["claude-code", "opencode"],
    ))

    by_provider = {result.provider: result for result in results}

    assert by_provider["claude-code"].status == "ok"
    assert by_provider["opencode"].status == "error"
    assert "opencode wrapper crashed" in by_provider["opencode"].raw_summary


def test_run_provider_spawn_error_keeps_launch_diagnostics(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        raise OSError("CreateProcess failed")

    monkeypatch.setattr(
        "app.services.external_agent_discovery.check_provider_health",
        lambda provider, command, fallback_commands=None: {
            "status": "available",
            "argv": ["powershell.exe", "-Command", "& 'ccr' 'code' '-p'"],
            "configured_command": "ccr code -p",
            "configured_argv": ["ccr", "code", "-p"],
            "path": "PowerShell function ccr",
            "launch_kind": "powershell",
            "used_fallback": False,
            "attempts": [
                {
                    "command": "ccr code -p",
                    "status": "available",
                    "launch_kind": "powershell",
                    "path": "PowerShell function ccr",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [],
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="spawn-error-diagnostic",
            repo_path=str(tmp_path),
            analysis_object_text="nvme-tcp-tls",
        ),
        providers=["claude-code"],
    ))

    result = results[0]
    assert result.status == "error"
    assert "CreateProcess failed" in result.raw_summary
    assert "launch=powershell" in result.raw_summary
    assert "configured=ccr code -p" in result.raw_summary
    assert "PowerShell function ccr" in result.raw_summary
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


def test_run_provider_tries_fallback_when_primary_command_exits_nonzero(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    bad_agent = tmp_path / "bad_agent.py"
    bad_agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('ccr wrapper rejected args', file=sys.stderr)\n"
        "raise SystemExit(9)\n",
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls_entry(void) { return 0; }\n", encoding="utf-8")
    ok_agent = tmp_path / "ok_agent.py"
    ok_agent.write_text(
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({"
        "'candidate_files':[{'path':'src/tls.c','reason':'fallback found it','confidence':'high'}],"
        "'candidate_symbols':[],"
        "'candidate_entries':[],"
        "'need_source_slices':[],"
        "'commands':['rg --files'],"
        "'raw_summary':'fallback_ok'"
        "}))\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_command",
        f'"{sys.executable}" "{bad_agent}"',
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [f'"{sys.executable}" "{ok_agent}"'],
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="fallback-run",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
    ))

    assert results[0].status == "ok"
    assert results[0].raw_summary == "fallback_ok"
    assert results[0].candidate_files[0].validated is True
    assert any("primary command failed; using fallback" in item for item in results[0].warnings)
    assert any("ccr wrapper rejected args" in item for item in results[0].warnings)


def test_run_provider_uses_fallback_after_ccr_config_error(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    bad_agent = tmp_path / "ccr_config_error.py"
    bad_agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('Config file not found at C:/Users/me/.claude-code-router/config-router.json')\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    ok_agent = tmp_path / "ok_agent.py"
    ok_agent.write_text(
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'candidate_files':[],'candidate_symbols':[],"
        "'candidate_entries':[],'need_source_slices':[],"
        "'commands':[],'raw_summary':'fallback_ok'}))\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_command",
        f'"{sys.executable}" "{bad_agent}"',
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [f'"{sys.executable}" "{ok_agent}"'],
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="ccr-config-error",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
    ))

    assert results[0].status == "ok"
    assert results[0].raw_summary == "fallback_ok"
    assert any("Config file not found" in item for item in results[0].warnings)
    assert len(results[0].runtime_attempts) == 2
    assert results[0].runtime_attempts[0]["run_status"] == "error"
    assert results[0].runtime_attempts[1]["run_status"] == "ok"


def test_run_provider_uses_fallback_after_default_ccr_run_invalid_output(tmp_path, monkeypatch):
    from app.services.agent_discovery_session import create_agent_discovery_session
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    ccr = tmp_path / "bin" / "ccr.cmd"
    ccr.parent.mkdir()
    ccr.write_text("@echo off\n", encoding="utf-8")
    ok_agent = tmp_path / "ok_agent.py"
    ok_agent.write_text(
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'candidate_files':[],'candidate_symbols':[],"
        "'candidate_entries':[],'need_source_slices':[],"
        "'commands':[],'raw_summary':'fallback_ok'}))\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("CCR_CONFIG_PATH", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_command",
        f'"{ccr}" code -p --output-format json',
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [f'"{sys.executable}" "{ok_agent}"'],
    )
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="workspace_scope",
        artifact_dir=tmp_path / "artifacts",
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="ccr-preflight-config-error",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
        session=session,
    ))
    loaded = create_agent_discovery_session.load(tmp_path / "artifacts")
    runtime_attempts = [
        item for item in loaded.ledger.command_history
        if item.get("kind") == "runtime_attempt"
    ]

    assert results[0].status == "ok"
    assert results[0].raw_summary == "fallback_ok"
    assert any("primary command failed; using fallback" in item for item in results[0].warnings)
    assert any("invalid JSON: empty output" in item for item in results[0].warnings)
    assert len(results[0].runtime_attempts) == 2
    assert results[0].runtime_attempts[0]["status"] == "available"
    assert results[0].runtime_attempts[0]["run_status"] == "invalid_output"
    assert results[0].runtime_attempts[1]["run_status"] == "ok"
    assert runtime_attempts == results[0].runtime_attempts


def test_run_provider_fallback_preserves_primary_invalid_json_warning(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    bad_agent = tmp_path / "bad_json_agent.py"
    bad_agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('ccr login banner')\n",
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls_entry(void) { return 0; }\n", encoding="utf-8")
    ok_agent = tmp_path / "ok_agent.py"
    ok_agent.write_text(
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({"
        "'candidate_files':[{'path':'src/tls.c','reason':'fallback found it','confidence':'high'}],"
        "'candidate_symbols':[],"
        "'candidate_entries':[],"
        "'need_source_slices':[],"
        "'commands':['rg --files'],"
        "'raw_summary':'fallback_ok'"
        "}))\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_command",
        f'"{sys.executable}" "{bad_agent}"',
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [f'"{sys.executable}" "{ok_agent}"'],
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="fallback-invalid-json",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
    ))

    assert results[0].status == "ok"
    assert results[0].candidate_files[0].validated is True
    assert any("primary command failed; using fallback" in item for item in results[0].warnings)
    assert any("invalid JSON" in item for item in results[0].warnings)
    assert not any(item.strip() == "ccr login banner" for item in results[0].warnings)


def test_run_provider_records_runtime_attempts_in_session_ledger(tmp_path, monkeypatch):
    from app.services.agent_discovery_session import create_agent_discovery_session
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    bad_agent = tmp_path / "bad_json_agent.py"
    bad_agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('ccr login banner')\n",
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls_entry(void) { return 0; }\n", encoding="utf-8")
    ok_agent = tmp_path / "ok_agent.py"
    ok_agent.write_text(
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({"
        "'candidate_files':[{'path':'src/tls.c','reason':'fallback found it','confidence':'high'}],"
        "'candidate_symbols':[],"
        "'candidate_entries':[],"
        "'need_source_slices':[],"
        "'commands':['rg --files'],"
        "'raw_summary':'fallback_ok'"
        "}))\n",
        encoding="utf-8",
    )
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="workspace_scope",
        artifact_dir=tmp_path / "artifacts",
    )

    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_command",
        f'"{sys.executable}" "{bad_agent}"',
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [f'"{sys.executable}" "{ok_agent}"'],
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="obj_tls",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
        session=session,
    ))

    runtime_attempts = [
        item for item in session.ledger.command_history
        if item.get("kind") == "runtime_attempt"
    ]
    parsed_attempts = session.turns[0].parsed_result.get("runtime_attempts")
    loaded = create_agent_discovery_session.load(tmp_path / "artifacts")
    loaded_attempts = [
        item for item in loaded.ledger.command_history
        if item.get("kind") == "runtime_attempt"
    ]

    assert results[0].status == "ok"
    assert len(runtime_attempts) == 2
    assert runtime_attempts[0]["run_status"] == "invalid_output"
    assert "invalid JSON" in runtime_attempts[0]["run_message"]
    assert runtime_attempts[1]["run_status"] == "ok"
    assert runtime_attempts[0]["prompt_transport"] == "stdin"
    assert runtime_attempts[1]["prompt_transport"] == "stdin"
    assert parsed_attempts == runtime_attempts
    assert loaded_attempts == runtime_attempts


def test_runtime_attempt_artifacts_redact_secret_command_values(tmp_path, monkeypatch):
    from app.services.agent_discovery_session import create_agent_discovery_session
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    agent = tmp_path / "agent.py"
    agent.write_text(
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({"
        "'candidate_files':[],"
        "'candidate_symbols':[],"
        "'candidate_entries':[],"
        "'need_source_slices':[],"
        "'commands':[],"
        "'raw_summary':'ok'"
        "}))\n",
        encoding="utf-8",
    )
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="workspace_scope",
        artifact_dir=tmp_path / "artifacts",
    )
    secret = "sk-test-secret-123"

    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_command",
        f'"{sys.executable}" "{agent}" --api-key {secret} --token={secret}',
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [],
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="obj_tls",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
        session=session,
    ))

    loaded = create_agent_discovery_session.load(tmp_path / "artifacts")
    serialized = json.dumps(loaded.ledger.command_history, ensure_ascii=False)

    assert results[0].status == "ok"
    assert secret not in serialized
    assert "<redacted>" in serialized


def test_run_provider_redacts_secret_values_from_error_summary(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    secret = "sk-test-secret-456"
    agent = tmp_path / "agent_error.py"
    agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        f"print('auth failed token={secret}', file=sys.stderr)\n"
        "raise SystemExit(5)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_command",
        f'"{sys.executable}" "{agent}" --api-key {secret}',
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [],
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="secret-error",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
    ))

    serialized = json.dumps(results[0].__dict__, ensure_ascii=False)

    assert results[0].status == "error"
    assert secret not in serialized
    assert "<redacted>" in serialized


def test_external_agent_adapter_health_redacts_secret_attempts(monkeypatch):
    from app.adapters import external_agent as adapter_mod

    secret = "sk-test-secret-789"

    def fake_health(provider, command, fallback_commands=None):
        return {
            "status": "available",
            "path": "agent",
            "attempts": [
                {
                    "command": f"ccr code --api-key {secret}",
                    "status": "available",
                    "launch_kind": "exec",
                }
            ],
        }

    monkeypatch.setattr(adapter_mod, "check_provider_health", fake_health)

    health = asyncio.run(
        adapter_mod.ExternalAgentAdapter("claude-code", "claude_code_command").health_check()
    )

    assert secret not in health.last_check
    assert "<redacted>" in health.last_check


def test_agent_diagnostic_redaction_handles_quoted_and_bearer_values():
    from app.services.external_agent_discovery import redact_agent_diagnostic_text

    text = (
        "ccr code --api-key 'plain-secret-1' "
        '--token="plain-secret-2" '
        "Authorization: Bearer plainSecretToken123 "
        "password=plain-secret-3 "
        "sk-test-secret-quoted"
    )

    redacted = redact_agent_diagnostic_text(text)

    assert "plain-secret-1" not in redacted
    assert "plain-secret-2" not in redacted
    assert "plainSecretToken123" not in redacted
    assert "plain-secret-3" not in redacted
    assert "sk-test-secret-quoted" not in redacted
    assert redacted.count("<redacted>") >= 5


def test_run_provider_nonzero_exit_prefers_structured_agent_error(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    agent = tmp_path / "agent_error_wrapper.py"
    agent.write_text(
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({"
        "'type':'result',"
        "'subtype':'error_during_execution',"
        "'is_error':True,"
        "'api_error_status':'403',"
        "'result':'network access blocked in intranet'"
        "}))\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.external_agent_discovery.settings.claude_code_command", f"{sys.executable} {agent}")
    monkeypatch.setattr("app.services.external_agent_discovery.settings.claude_code_fallback_commands", [])

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="nonzero-error-wrapper",
            repo_path=str(tmp_path),
            analysis_object_text="tls",
        ),
        providers=["claude-code"],
    ))

    assert results[0].status == "error"
    assert "error_during_execution" in results[0].raw_summary
    assert "network access blocked" in results[0].raw_summary
    assert "stdout: {\"type\"" not in results[0].raw_summary


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


def test_kill_and_wait_process_still_waits_when_process_already_exited(monkeypatch):
    from app.services import external_agent_discovery as discovery

    sleeps: list[float] = []

    class FakeProc:
        def __init__(self):
            self.waited = False

        def kill(self):
            raise ProcessLookupError("already exited")

        async def wait(self):
            self.waited = True
            return 0

    async def fake_sleep(delay):
        sleeps.append(delay)

    fake_proc = FakeProc()
    monkeypatch.setattr(discovery.platform, "system", lambda: "Windows")
    monkeypatch.setattr(discovery.asyncio, "sleep", fake_sleep)

    asyncio.run(discovery._kill_and_wait_process(fake_proc))

    assert fake_proc.waited is True
    assert sleeps


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


def test_startup_probe_unavailable_includes_health_diagnostics(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import probe_external_agent_startup

    monkeypatch.setattr(
        "app.services.external_agent_discovery.check_provider_health",
        lambda provider, command, fallback_commands=None: {
            "status": "unavailable",
            "reason": "no agent command found; attempted: ccr code -p, claude -p",
            "diagnostic": {"summary": "PATH entries: C:/agent-bin"},
        },
    )

    result = asyncio.run(probe_external_agent_startup("claude-code", repo_path=tmp_path))

    assert result["healthy"] is False
    assert result["status"] == "unavailable"
    assert "no agent command found" in result["message"]
    assert result["health"]["diagnostic"]["summary"] == "PATH entries: C:/agent-bin"


def test_startup_probe_launches_agent_and_parses_json(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import probe_external_agent_startup

    captured: dict = {}

    class FakeProc:
        returncode = 0

        async def communicate(self, data):
            captured["stdin"] = data.decode("utf-8") if data else ""
            return (
                b'{"candidate_files":[],"candidate_symbols":[],"candidate_entries":[],'
                b'"need_source_slices":[],"commands":[],"raw_summary":"startup_probe_ok"}',
                b"",
            )

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["argv"] = args
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        return FakeProc()

    monkeypatch.setattr(
        "app.services.external_agent_discovery.check_provider_health",
        lambda provider, command, fallback_commands=None: {
            "status": "available",
            "argv": ["fake-agent", "-p", "--output-format", "json"],
            "path": "fake-agent",
        },
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = asyncio.run(probe_external_agent_startup("claude-code", repo_path=tmp_path))

    assert result["healthy"] is True
    assert result["status"] == "ok"
    assert result["message"] == "startup_probe_ok"
    assert captured["argv"][0:2] == ("fake-agent", "-p")
    assert "startup probe" in captured["argv"][2]
    assert captured["argv"][3:] == ("--output-format", "json")
    assert captured["cwd"] == str(tmp_path.resolve())
    assert captured["env"]["CODETALK_AGENT_READONLY"] == "1"
    assert captured["stdin"] == ""


def test_run_provider_claude_print_mode_passes_prompt_as_argument(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    captured: dict = {}

    class FakeProc:
        returncode = 0

        async def communicate(self, data):
            captured["stdin"] = data.decode("utf-8") if data else ""
            return (
                b'{"candidate_files":[],"candidate_symbols":[],"candidate_entries":[],'
                b'"need_source_slices":[],"commands":[],"raw_summary":"ok"}',
                b"",
            )

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["argv"] = args
        return FakeProc()

    monkeypatch.setattr(
        "app.services.external_agent_discovery.check_provider_health",
        lambda provider, command, fallback_commands=None: {
            "status": "available",
            "argv": ["fake-agent", "-p", "--output-format", "json"],
            "path": "fake-agent",
            "attempts": [{"command": command, "status": "available"}],
        },
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [],
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="print-mode",
            repo_path=str(tmp_path),
            analysis_object_text="nvme-tcp-tls",
        ),
        providers=["claude-code"],
    ))

    assert results[0].status == "ok"
    assert captured["argv"][0:2] == ("fake-agent", "-p")
    assert "analysis_object_text" in captured["argv"][2]
    assert captured["argv"][3:] == ("--output-format", "json")
    assert captured["stdin"] == ""


def test_run_provider_replaces_configured_claude_print_placeholder(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import AgentDiscoveryRequest, run_external_agent_discovery

    captured: dict = {}

    class FakeProc:
        returncode = 0

        async def communicate(self, data):
            captured["stdin"] = data.decode("utf-8") if data else ""
            return (
                b'{"candidate_files":[],"candidate_symbols":[],"candidate_entries":[],'
                b'"need_source_slices":[],"commands":[],"raw_summary":"ok"}',
                b"",
            )

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["argv"] = args
        return FakeProc()

    monkeypatch.setattr(
        "app.services.external_agent_discovery.check_provider_health",
        lambda provider, command, fallback_commands=None: {
            "status": "available",
            "argv": ["fake-agent", "-p", "configured placeholder", "--output-format", "json"],
            "path": "fake-agent",
            "attempts": [{"command": command, "status": "available"}],
        },
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [],
    )

    results = asyncio.run(run_external_agent_discovery(
        AgentDiscoveryRequest(
            request_id="print-placeholder",
            repo_path=str(tmp_path),
            analysis_object_text="nvme-tcp-tls",
        ),
        providers=["claude-code"],
    ))

    assert results[0].status == "ok"
    assert captured["argv"][0:2] == ("fake-agent", "-p")
    assert "analysis_object_text" in captured["argv"][2]
    assert "configured placeholder" not in captured["argv"]
    assert captured["argv"][3:] == ("--output-format", "json")
    assert captured["stdin"] == ""


def test_startup_probe_redacts_secret_values_from_response(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import probe_external_agent_startup

    agent = tmp_path / "agent.py"
    agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('{\"candidate_files\":[],\"candidate_symbols\":[],"
        "\"candidate_entries\":[],\"need_source_slices\":[],"
        "\"commands\":[],\"raw_summary\":\"startup_probe_ok\"}')\n",
        encoding="utf-8",
    )
    secret = "sk-startup-secret-123"

    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_command",
        f'"{sys.executable}" "{agent}" --api-key "{secret}"',
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [],
    )

    result = asyncio.run(probe_external_agent_startup("claude-code", repo_path=tmp_path))
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["healthy"] is True
    assert result["status"] == "ok"
    assert secret not in serialized
    assert "<redacted>" in serialized


def test_startup_probe_tries_fallback_when_primary_command_exits_nonzero(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import probe_external_agent_startup

    bad_agent = tmp_path / "bad_agent.py"
    bad_agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('ccr wrapper rejected args', file=sys.stderr)\n"
        "raise SystemExit(9)\n",
        encoding="utf-8",
    )
    ok_agent = tmp_path / "ok_agent.py"
    ok_agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('{\"candidate_files\":[],\"candidate_symbols\":[],"
        "\"candidate_entries\":[],\"need_source_slices\":[],"
        "\"commands\":[],\"raw_summary\":\"startup_probe_ok\"}')\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_command",
        f'"{sys.executable}" "{bad_agent}"',
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [f'"{sys.executable}" "{ok_agent}"'],
    )

    result = asyncio.run(probe_external_agent_startup("claude-code", repo_path=tmp_path))

    assert result["healthy"] is True
    assert result["status"] == "ok"
    assert result["message"] == "startup_probe_ok"
    attempts = result["health"]["attempts"]
    assert attempts[0]["probe_status"] == "error"
    assert "ccr wrapper rejected args" in attempts[0]["probe_message"]
    assert attempts[1]["probe_status"] == "ok"
    assert result["health"]["used_fallback"] is True


def test_startup_probe_uses_fallback_after_ccr_config_error(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import probe_external_agent_startup

    bad_agent = tmp_path / "ccr_config_error.py"
    bad_agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('Config file not found at C:/Users/me/.claude-code-router/config-router.json')\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    ok_agent = tmp_path / "ok_agent.py"
    ok_agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('{\"candidate_files\":[],\"candidate_symbols\":[],"
        "\"candidate_entries\":[],\"need_source_slices\":[],"
        "\"commands\":[],\"raw_summary\":\"startup_probe_ok\"}')\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_command",
        f'"{sys.executable}" "{bad_agent}"',
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [f'"{sys.executable}" "{ok_agent}"'],
    )

    result = asyncio.run(probe_external_agent_startup("claude-code", repo_path=tmp_path))

    assert result["healthy"] is True
    assert result["status"] == "ok"
    assert result["message"] == "startup_probe_ok"
    assert result["health"]["used_fallback"] is True
    attempts = result["health"]["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["probe_status"] == "error"
    assert "Config file not found" in attempts[0]["probe_message"]
    assert attempts[1]["probe_status"] == "ok"


def test_startup_probe_uses_fallback_after_default_ccr_run_invalid_output(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import probe_external_agent_startup

    ccr = tmp_path / "bin" / "ccr.cmd"
    ccr.parent.mkdir()
    ccr.write_text("@echo off\n", encoding="utf-8")
    ok_agent = tmp_path / "ok_agent.py"
    ok_agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('{\"candidate_files\":[],\"candidate_symbols\":[],"
        "\"candidate_entries\":[],\"need_source_slices\":[],"
        "\"commands\":[],\"raw_summary\":\"startup_probe_ok\"}')\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("CCR_CONFIG_PATH", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_command",
        f'"{ccr}" code -p --output-format json',
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [f'"{sys.executable}" "{ok_agent}"'],
    )

    result = asyncio.run(probe_external_agent_startup("claude-code", repo_path=tmp_path))

    assert result["healthy"] is True
    assert result["status"] == "ok"
    assert result["message"] == "startup_probe_ok"
    assert result["health"]["used_fallback"] is True
    assert "primary command failed; using fallback" in result["health"]["reason"]
    attempts = result["health"]["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["status"] == "available"
    assert attempts[0]["probe_status"] == "invalid_output"
    assert attempts[1]["probe_status"] == "ok"


def test_startup_probe_tries_fallback_when_primary_outputs_invalid_json(tmp_path, monkeypatch):
    from app.services.external_agent_discovery import probe_external_agent_startup

    bad_agent = tmp_path / "bad_json_agent.py"
    bad_agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('ccr banner before json')\n",
        encoding="utf-8",
    )
    ok_agent = tmp_path / "ok_agent.py"
    ok_agent.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('{\"candidate_files\":[],\"candidate_symbols\":[],"
        "\"candidate_entries\":[],\"need_source_slices\":[],"
        "\"commands\":[],\"raw_summary\":\"startup_probe_ok\"}')\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_command",
        f'"{sys.executable}" "{bad_agent}"',
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.claude_code_fallback_commands",
        [f'"{sys.executable}" "{ok_agent}"'],
    )

    result = asyncio.run(probe_external_agent_startup("claude-code", repo_path=tmp_path))

    assert result["healthy"] is True
    assert result["status"] == "ok"
    attempts = result["health"]["attempts"]
    assert attempts[0]["probe_status"] == "invalid_output"
    assert "invalid JSON" in attempts[0]["probe_message"]
    assert attempts[1]["probe_status"] == "ok"
    assert result["health"]["reason"].startswith("primary command failed")


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


def test_agent_candidate_path_with_outer_workspace_prefix_validates_from_nested_root(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    repo_root = tmp_path / "frontend" / "nof"
    tls_dir = repo_root / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        repo_root,
        "frontend/nof/nvmf_tcp/transport/tls/tls.c",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_markdown_backticks_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        tmp_path,
        "`nvmf_tcp/transport/tls/tls.c`",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_line_suffix_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        tmp_path,
        "nvmf_tcp/transport/tls/tls.c:42",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_github_line_suffix_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        tmp_path,
        "nvmf_tcp/transport/tls/tls.c#L42",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_line_range_suffix_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        tmp_path,
        "nvmf_tcp/transport/tls/tls.c:42-50",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_github_line_range_suffix_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        tmp_path,
        "nvmf_tcp/transport/tls/tls.c#L42-L50",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_file_uri_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    source = tls_dir / "tls.c"
    source.write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(tmp_path, source.as_uri())

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_file_uri_line_fragment_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    source = tls_dir / "tls.c"
    source.write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(tmp_path, f"{source.as_uri()}#L42-L50")

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_encoded_file_uri_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    source = tls_dir / "tls.c"
    source.write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(tmp_path, source.as_uri())

    assert validation.validated is True
    assert validation.path == "nvmf tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_markdown_link_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        tmp_path,
        "[tls.c](nvmf_tcp/transport/tls/tls.c)",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_angle_brackets_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        tmp_path,
        "<nvmf_tcp/transport/tls/tls.c>",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_bullet_prefix_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        tmp_path,
        "- nvmf_tcp/transport/tls/tls.c",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_label_prefix_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        tmp_path,
        "path: nvmf_tcp/transport/tls/tls.c",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_structured_label_prefix_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        tmp_path,
        "file_path: nvmf_tcp/transport/tls/tls.c",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_trailing_punctuation_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        tmp_path,
        "nvmf_tcp/transport/tls/tls.c,",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_candidate_path_with_plain_code_fence_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        tmp_path,
        "```\nnvmf_tcp/transport/tls/tls.c\n```",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


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


def test_agent_directory_candidate_with_outer_workspace_prefix_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    repo_root = tmp_path / "frontend" / "nof"
    tls_dir = repo_root / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    (tls_dir / "tls.c").write_text("int tls_c;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(
        repo_root,
        "frontend/nof/nvmf_tcp/transport/tls",
    )

    assert validation.validated is True
    assert validation.path == "nvmf_tcp/transport/tls/tls.c"


def test_agent_cxx_candidate_file_validates(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    src = tmp_path / "src"
    src.mkdir()
    (src / "transport.cxx").write_text("int transport;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(tmp_path, "src/transport.cxx")

    assert validation.validated is True
    assert validation.path == "src/transport.cxx"


def test_agent_directory_candidate_prefers_cxx_source_file(tmp_path):
    from app.services.external_agent_discovery import validate_agent_candidate_file

    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text("docs\n", encoding="utf-8")
    (src / "transport.cxx").write_text("int transport;\n", encoding="utf-8")

    validation = validate_agent_candidate_file(tmp_path, "src")

    assert validation.validated is True
    assert validation.path == "src/transport.cxx"


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


def test_duplicate_local_and_agent_candidate_keeps_local_source(tmp_path):
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
            source="repo_search",
            confidence="medium",
            reason="local content search matched transport/tls",
            role="primary",
        )
    ]
    agent = AgentDiscoveryResult(
        provider="claude-code",
        status="ok",
        candidate_files=[
            AgentCandidateFile(
                path="nof/nvmf_tcp/transport/tls/tls.c",
                reason="agent also found transport/tls",
                confidence="high",
                validated=True,
            )
        ],
    )

    merged, warnings = merge_source_candidates(tmp_path, existing, [agent])

    assert warnings == []
    assert len(merged) == 1
    assert merged[0].source == "repo_search"
    assert merged[0].confidence == "high"
    assert "claude-code" in merged[0].reason


def test_duplicate_existing_candidates_keep_best_source_priority(tmp_path):
    from app.schemas.workspace_analysis import ScopeCandidate
    from app.services.external_agent_discovery import merge_source_candidates

    src = tmp_path / "nof" / "nvmf_tcp" / "transport" / "tls"
    src.mkdir(parents=True)
    source = src / "tls.c"
    source.write_text("int tls;\n", encoding="utf-8")

    existing = [
        ScopeCandidate(
            path=str(source),
            source="repo_search",
            confidence="medium",
            reason="local content search matched transport/tls",
            role="primary",
        ),
        ScopeCandidate(
            path=str(source),
            source="gitnexus",
            confidence="high",
            reason="graph duplicate",
            role="related",
        ),
    ]

    merged, warnings = merge_source_candidates(tmp_path, existing, [])

    assert warnings == []
    assert len(merged) == 1
    assert merged[0].source == "repo_search"
    assert merged[0].confidence == "high"
    assert "gitnexus" in merged[0].reason


def test_duplicate_existing_candidates_keep_best_role_priority(tmp_path):
    from app.schemas.workspace_analysis import ScopeCandidate
    from app.services.external_agent_discovery import merge_source_candidates

    src = tmp_path / "nof" / "nvmf_tcp" / "transport" / "tls"
    src.mkdir(parents=True)
    source = src / "tls.c"
    source.write_text("int tls;\n", encoding="utf-8")

    existing = [
        ScopeCandidate(
            path=str(source),
            source="repo_search",
            confidence="medium",
            reason="broad local match",
            role="related",
        ),
        ScopeCandidate(
            path=str(source),
            source="repo_search",
            confidence="medium",
            reason="path hint matched primary module",
            role="primary",
        ),
    ]

    merged, warnings = merge_source_candidates(tmp_path, existing, [])

    assert warnings == []
    assert len(merged) == 1
    assert merged[0].source == "repo_search"
    assert merged[0].role == "primary"
    assert "path hint matched primary module" in merged[0].reason


def test_merge_source_candidates_ranks_primary_role_before_related(tmp_path):
    from app.schemas.workspace_analysis import ScopeCandidate
    from app.services.external_agent_discovery import merge_source_candidates

    related_dir = tmp_path / "aaa_examples" / "transport" / "tls"
    primary_dir = tmp_path / "zzz_real" / "transport" / "tls"
    related_dir.mkdir(parents=True)
    primary_dir.mkdir(parents=True)
    related_source = related_dir / "tls.c"
    primary_source = primary_dir / "tls.c"
    related_source.write_text("int example_tls;\n", encoding="utf-8")
    primary_source.write_text("int real_tls;\n", encoding="utf-8")

    existing = [
        ScopeCandidate(
            path=str(related_source),
            source="repo_search",
            confidence="high",
            reason="example path also matched tls",
            role="related",
        ),
        ScopeCandidate(
            path=str(primary_source),
            source="repo_search",
            confidence="high",
            reason="primary module path matched tls",
            role="primary",
        ),
    ]

    merged, warnings = merge_source_candidates(tmp_path, existing, [])

    assert warnings == []
    assert merged[0].role == "primary"
    assert merged[0].path.replace("\\", "/").endswith("zzz_real/transport/tls/tls.c")


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


def test_workspace_agent_scope_ledger_rejects_directory_entry_file(tmp_path):
    import app.services.workspace_scope_resolver as scope_mod
    from app.services.agent_discovery_session import create_agent_discovery_session
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "rpc.c").write_text("void rpc_entry(void) {}\n", encoding="utf-8")
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="workspace_scope",
        artifact_dir=tmp_path / "artifacts",
    )
    result = AgentDiscoveryResult(
        provider="claude-code",
        status="ok",
        candidate_entries=[
            AgentCandidateEntry(
                entry_kind="rpc",
                entry_symbol="rpc_entry",
                entry_file="src",
                chain=["rpc_entry", "target"],
                reason="directory is not a source-backed entry file",
                validated=True,
            )
        ],
    )

    scope_mod._record_agent_scope_results(
        session=session,
        object_id="obj",
        repo_path=str(tmp_path),
        results=[result],
    )

    assert session.ledger.validated_entries == []
    assert session.ledger.rejected_entries[0]["entry_file"] == "src"
    assert session.ledger.rejected_entries[0]["validation_error"] == "directory_candidate_not_allowed"


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


def test_merge_source_candidates_uses_raw_summary_when_warnings_missing(tmp_path):
    from app.services.external_agent_discovery import AgentDiscoveryResult, merge_source_candidates

    result = AgentDiscoveryResult(
        provider="claude-code",
        status="error",
        raw_summary="spawn failed: ccr is not on backend PATH",
    )

    merged, warnings = merge_source_candidates(tmp_path, [], [result])

    assert merged == []
    assert warnings == ["claude-code: error - spawn failed: ccr is not on backend PATH"]


def test_coverage_external_agent_warnings_use_raw_summary_when_warnings_missing():
    from app.services.coverage_analyzer import _external_agent_warnings

    warnings = _external_agent_warnings([
        {
            "provider": "claude-code",
            "status": "error",
            "warnings": [],
            "raw_summary": "spawn failed: ccr is not on backend PATH",
        }
    ])

    assert warnings == ["claude-code: spawn failed: ccr is not on backend PATH"]


def test_agent_entry_upsert_preserves_existing_input_hints():
    from app.services.coverage_analyzer import _upsert_agent_entry

    entries = [
        {
            "object_id": "gap1",
            "provider": "claude-code",
            "entry_symbol": "rpc_tls_entry",
            "entry_file": "src/rpc.c",
            "validation_error": "",
            "input_hints": ["invalid TLS PSK", "oversized capsule"],
            "chain": ["rpc_tls_entry", "tls_handshake"],
        }
    ]

    _upsert_agent_entry(
        entries,
        {
            "object_id": "gap1",
            "provider": "claude-code",
            "entry_symbol": "rpc_tls_entry",
            "entry_file": "src/rpc.c",
            "validation_error": "",
            "input_hints": [],
            "chain": ["rpc_tls_entry", "tls_handshake"],
        },
    )

    assert len(entries) == 1
    assert entries[0]["input_hints"] == ["invalid TLS PSK", "oversized capsule"]


def test_agent_entry_upsert_preserves_existing_trigger_and_reason():
    from app.services.coverage_analyzer import _upsert_agent_entry

    entries = [
        {
            "object_id": "gap1",
            "provider": "claude-code",
            "entry_symbol": "rpc_tls_entry",
            "entry_file": "src/rpc.c",
            "validation_error": "",
            "external_trigger": "RPC tls-entry",
            "reason": "public RPC handler reaches TLS handshake",
        }
    ]

    _upsert_agent_entry(
        entries,
        {
            "object_id": "gap1",
            "provider": "claude-code",
            "entry_symbol": "rpc_tls_entry",
            "entry_file": "src/rpc.c",
            "validation_error": "",
            "external_trigger": "",
            "reason": "",
        },
    )

    assert entries[0]["external_trigger"] == "RPC tls-entry"
    assert entries[0]["reason"] == "public RPC handler reaches TLS handshake"


def test_filter_resolved_unverified_entries_keeps_distinct_symbol_in_same_file():
    from app.services.coverage_analyzer import _filter_resolved_agent_unverified_entries

    filtered = _filter_resolved_agent_unverified_entries(
        [
            {
                "provider": "claude-code",
                "entry_symbol": "rpc_tls_entry",
                "entry_file": "src/rpc.c",
            },
            {
                "provider": "claude-code",
                "entry_symbol": "rpc_admin_entry",
                "entry_file": "src/rpc.c",
            },
        ],
        [
            {
                "provider": "claude-code",
                "entry_symbol": "rpc_tls_entry",
                "entry_file": "src/rpc.c",
            }
        ],
    )

    assert [entry["entry_symbol"] for entry in filtered] == ["rpc_admin_entry"]


def test_filter_resolved_unverified_entries_drops_symbolless_same_file_candidate():
    from app.services.coverage_analyzer import _filter_resolved_agent_unverified_entries

    filtered = _filter_resolved_agent_unverified_entries(
        [
            {
                "provider": "claude-code",
                "entry_symbol": "",
                "entry_file": "src/rpc.c",
            }
        ],
        [
            {
                "provider": "claude-code",
                "entry_symbol": "",
                "entry_file": "src/rpc.c",
            }
        ],
    )

    assert filtered == []


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


def test_workspace_resolver_keeps_local_candidate_when_agent_matches_same_file(tmp_path, monkeypatch):
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
    assert resolved.candidate_files[0].source == "repo_search"
    assert resolved.candidate_files[0].role == "primary"
    assert "claude-code" in resolved.candidate_files[0].reason
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


def test_workspace_resolver_finds_nvme_tls_embedded_in_chinese_text(tmp_path, monkeypatch):
    _write_tls_repo(tmp_path)

    async def fake_discovery(_request, **_kwargs):
        return []

    monkeypatch.setattr(
        "app.services.workspace_scope_resolver.run_external_agent_discovery",
        fake_discovery,
    )
    text = (
        "\u8bf7\u5206\u6790nvme-tcp-tls\u6a21\u5757"
        "\uff0c\u6e90\u7801\u76ee\u5f55\u53ef\u80fd\u5728frontend\u6216nof"
    )
    obj = AnalysisObject(id="obj_tls_long", text=text, kind="module")

    resolved = asyncio.run(WorkspaceScopeResolver()._resolve_object(
        obj=obj,
        ws_id="ws",
        repo_path=str(tmp_path),
        index=_GraphIndex(None),
        limits=LLMLimits(max_files_per_object=8),
        gitnexus_available=False,
    ))
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


def test_workspace_path_hint_with_parent_dirs_finds_tls_from_nof_repo_root(tmp_path, monkeypatch):
    from app.services.workspace_scope_resolver import _path_hint_repo_hits_blocking

    _write_tls_tree_at(tmp_path, "nvmf_tcp/transport/tls")

    hits = _path_hint_repo_hits_blocking(
        str(tmp_path),
        ["frontend/nof/nvmf_tcp/transport/tls"],
        8,
    )

    assert any(hit.replace("\\", "/").endswith("nvmf_tcp/transport/tls/tls.c") for hit in hits)


def test_workspace_path_keyword_ranking_prioritizes_module_named_source(tmp_path):
    from app.services.workspace_scope_resolver import _path_keyword_repo_hits_blocking

    tls_dir = tmp_path / "nof" / "nvmf_tcp" / "transport" / "tls"
    tls_dir.mkdir(parents=True)
    for name in ("alpha.c", "beta.c", "gamma.c", "tls.c"):
        (tls_dir / name).write_text("int placeholder(void) { return 0; }\n", encoding="utf-8")

    hits = _path_keyword_repo_hits_blocking(
        str(tmp_path),
        ["nvme", "tcp", "tls", "nvmf_tcp/transport/tls", "transport/tls"],
        2,
    )
    rel_hits = [Path(hit).relative_to(tmp_path).as_posix() for hit in hits]

    assert rel_hits[0] == "nof/nvmf_tcp/transport/tls/tls.c"


def test_workspace_path_keyword_ranking_prioritizes_root_transport_tls_over_examples(tmp_path):
    from app.services.workspace_scope_resolver import _path_keyword_repo_hits_blocking

    real_tls = tmp_path / "transport" / "tls"
    example_tls = tmp_path / "examples" / "transport" / "tls"
    real_tls.mkdir(parents=True)
    example_tls.mkdir(parents=True)
    (real_tls / "tls.c").write_text("int real_tls(void) { return 0; }\n", encoding="utf-8")
    (example_tls / "tls.c").write_text("int example_tls(void) { return 0; }\n", encoding="utf-8")

    hits = _path_keyword_repo_hits_blocking(
        str(tmp_path),
        ["nvme", "tcp", "tls", "nvmf_tcp/transport/tls", "transport/tls"],
        1,
    )
    rel_hits = [Path(hit).relative_to(tmp_path).as_posix() for hit in hits]

    assert rel_hits == ["transport/tls/tls.c"]


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


def test_workspace_resolver_round2_failure_keeps_local_source_candidate(tmp_path, monkeypatch):
    from app.schemas.workspace_analysis import AnalysisPlan
    from app.services.external_agent_discovery import AgentCandidateFile, AgentDiscoveryResult

    _write_tls_repo(tmp_path)
    calls: list[str] = []

    async def fake_discovery(request, **_kwargs):
        calls.append(request.request_id)
        if "round2" in request.request_id:
            raise RuntimeError("round2 agent crashed")
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_files=[
                    AgentCandidateFile(
                        path="nof/nvmf_tcp/transport/tls/missing.c",
                        reason="stale agent path",
                        confidence="high",
                    )
                ],
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

    resolved = preview.resolved_objects[0]
    paths = [c.path.replace("\\", "/") for c in resolved.candidate_files if c.path]

    assert any(path.endswith("nof/nvmf_tcp/transport/tls/tls.c") for path in paths)
    assert any("round2" in call for call in calls)
    assert any("round2 agent crashed" in warning for warning in resolved.warnings)
    assert preview.external_agent_status["external_agent"] == "error"


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
                        input_hints=["invalid TLS PSK", "oversized capsule"],
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
    assert gap["entry_paths"][0]["input_hints"] == ["invalid TLS PSK", "oversized capsule"]
    assert gap["black_box_cases"]
    case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
    assert "RPC recover-session" in case_text
    assert "invalid TLS PSK" in case_text
    assert "oversized capsule" in case_text
    card = design["entry_discovery"]["cards"][0]
    candidate = card["candidate_external_entries"][0]
    assert candidate["input_hints"] == ["invalid TLS PSK", "oversized capsule"]


def test_black_box_cases_keep_string_input_hint_as_single_hint():
    from app.adapters.coverage import FunctionHit
    from app.services.coverage_analyzer import _build_black_box_cases

    cases = _build_black_box_cases(
        FunctionHit(
            function_name="recover_tls",
            file_path="src/tls.c",
            line_start=1,
            triggered=False,
            hit_count=0,
        ),
        [
            {
                "entry_kind": "rpc",
                "entry_label": "RPC recover",
                "input_hints": "invalid TLS PSK",
            }
        ],
        [],
    )

    text = json.dumps(cases, ensure_ascii=False)

    assert "invalid TLS PSK" in text
    assert "i, n, v, a, l, i, d" not in text


def test_coverage_agent_one_hit_processing_failure_keeps_other_hit_context(tmp_path, monkeypatch):
    import app.services.coverage_analyzer as coverage_mod
    from app.adapters.coverage import FunctionHit, ModuleCoverage
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.c").write_text("void bad_recover(void) {}\n", encoding="utf-8")
    (src / "good.c").write_text("void good_recover(void) {}\n", encoding="utf-8")
    (src / "rpc.c").write_text("void rpc_recover(void) {}\n", encoding="utf-8")

    bad_hit = FunctionHit(
        function_name="bad_recover",
        file_path="src/bad.c",
        line_start=1,
        triggered=False,
        hit_count=0,
    )
    good_hit = FunctionHit(
        function_name="good_recover",
        file_path="src/good.c",
        line_start=1,
        triggered=False,
        hit_count=0,
    )
    module = ModuleCoverage(
        module_path="src",
        line_rate=0.0,
        branch_rate=0.0,
        function_rate=0.0,
        function_hits=[bad_hit, good_hit],
    )

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="rpc",
                        entry_symbol="rpc_recover",
                        entry_file="src/rpc.c",
                        chain=["rpc_recover"],
                        reason="public RPC entry",
                        validated=True,
                    )
                ],
            )
        ]

    original_collect = coverage_mod._collect_agent_entry_results

    def flaky_collect(*args, object_id, **kwargs):
        if "bad_recover" in object_id:
            raise RuntimeError("entry collection crashed")
        return original_collect(*args, object_id=object_id, **kwargs)

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    monkeypatch.setattr(coverage_mod, "_collect_agent_entry_results", flaky_collect)

    contexts = asyncio.run(
        coverage_mod._resolve_external_agent_entries_for_hits(
            [(module, bad_hit), (module, good_hit)],
            repo_path=str(tmp_path),
        )
    )

    bad_context = contexts["src/bad.c:bad_recover:1"]
    good_context = contexts["src/good.c:good_recover:1"]

    assert bad_context["status"] == "error"
    assert any("entry collection crashed" in item["raw_summary"] for item in bad_context["raw_results"])
    assert good_context["status"] == "available"
    assert good_context["validated_entries"][0]["entry_symbol"] == "rpc_recover"


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

    gap = next(g for g in design["gaps"] if g.get("function_name") == "internal_recover")
    entry_card_item = gap["external_entry_card"]["entries"][0]
    assert entry_card_item["provider"] == "claude-code"
    assert entry_card_item["turn_id"] == "coverage:src/session.c:internal_recover:1"
    assert entry_card_item["source_verification"] == "source_backed"
    assert entry_card_item["validation_error"] is None


def test_coverage_verified_agent_entry_card_keeps_external_trigger(tmp_path, monkeypatch):
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
                        external_trigger="RPC recover-session with invalid TLS PSK",
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
    assert candidate["external_trigger"] == "RPC recover-session with invalid TLS PSK"


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


def test_coverage_agent_entry_collect_rejects_directory_entry_file(tmp_path):
    import app.services.coverage_analyzer as coverage_mod
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "rpc.c").write_text("void rpc_entry(void) {}\n", encoding="utf-8")
    result = AgentDiscoveryResult(
        provider="claude-code",
        status="ok",
        candidate_entries=[
            AgentCandidateEntry(
                entry_kind="rpc",
                entry_symbol="",
                entry_file="src",
                chain=["internal_gap"],
                external_trigger="RPC entry",
                reason="agent returned only the containing directory",
                validated=True,
            )
        ],
    )
    validated: list[dict] = []
    unverified: list[dict] = []

    coverage_mod._collect_agent_entry_results(
        [result],
        repo_root=tmp_path,
        object_id="src/internal.c:internal_gap:1",
        turn_id="coverage:src/internal.c:internal_gap:1",
        agent_session=None,
        validated_entries=validated,
        unverified_entries=unverified,
        status_by_provider={},
        raw_results=[],
    )

    assert validated == []
    assert unverified[0]["entry_file"] == "src"
    assert unverified[0]["source_verification"] == "needs_source_verification"
    assert unverified[0]["validation_error"] == "directory_candidate_not_allowed"


def test_coverage_agent_entry_collect_resolves_directory_with_entry_symbol(tmp_path):
    import app.services.coverage_analyzer as coverage_mod
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "rpc.c").write_text("void rpc_entry(void) {}\n", encoding="utf-8")
    result = AgentDiscoveryResult(
        provider="claude-code",
        status="ok",
        candidate_entries=[
            AgentCandidateEntry(
                entry_kind="rpc",
                entry_symbol="rpc_entry",
                entry_file="src",
                chain=["rpc_entry", "internal_gap"],
                external_trigger="RPC entry",
                reason="agent returned directory plus symbol",
                validated=True,
            )
        ],
    )
    validated: list[dict] = []
    unverified: list[dict] = []

    coverage_mod._collect_agent_entry_results(
        [result],
        repo_root=tmp_path,
        object_id="src/internal.c:internal_gap:1",
        turn_id="coverage:src/internal.c:internal_gap:1",
        agent_session=None,
        validated_entries=validated,
        unverified_entries=unverified,
        status_by_provider={},
        raw_results=[],
    )

    assert unverified == []
    assert validated[0]["entry_file"] == "src/rpc.c"
    assert validated[0]["source_verification"] == "source_backed"
    assert validated[0]["validation_error"] is None


def test_coverage_agent_directory_entry_symbol_generates_black_box_ready(tmp_path, monkeypatch):
    import asyncio
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "internal.c").write_text(
        "void internal_gap(void) {\n"
        "    if (1) { return; }\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "rpc.c").write_text("void rpc_entry(void) { internal_gap(); }\n", encoding="utf-8")

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="rpc",
                        entry_symbol="rpc_entry",
                        entry_file="src",
                        chain=["rpc_entry", "internal_gap"],
                        external_trigger="RPC entry",
                        reason="agent returned directory plus symbol",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "h,internal,src/internal.c:1-3,internal_gap,false,0\n"
    )

    design = asyncio.run(
        build_coverage_test_design(modules, workspace_id="ws-1", repo_path=str(tmp_path))
    )

    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
    assert gap["entry_paths"][0]["entry_file"] == "src/rpc.c"
    assert gap["black_box_cases"]


def test_coverage_agent_entry_collect_rejects_entry_without_file(tmp_path):
    import app.services.coverage_analyzer as coverage_mod
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    result = AgentDiscoveryResult(
        provider="claude-code",
        status="ok",
        candidate_entries=[
            AgentCandidateEntry(
                entry_kind="rpc",
                entry_symbol="",
                entry_file=None,
                chain=["internal_gap"],
                external_trigger="RPC entry",
                reason="agent returned no source file",
                validated=True,
            )
        ],
    )
    validated: list[dict] = []
    unverified: list[dict] = []

    coverage_mod._collect_agent_entry_results(
        [result],
        repo_root=tmp_path,
        object_id="src/internal.c:internal_gap:1",
        turn_id="coverage:src/internal.c:internal_gap:1",
        agent_session=None,
        validated_entries=validated,
        unverified_entries=unverified,
        status_by_provider={},
        raw_results=[],
    )

    assert validated == []
    assert unverified[0]["entry_file"] is None
    assert unverified[0]["source_verification"] == "needs_source_verification"
    assert unverified[0]["validation_error"] == "entry_file_missing"


def test_coverage_agent_entry_collect_resolves_symbol_without_entry_file(tmp_path):
    import app.services.coverage_analyzer as coverage_mod
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "rpc.c").write_text("void rpc_entry(void) {}\n", encoding="utf-8")
    result = AgentDiscoveryResult(
        provider="claude-code",
        status="ok",
        candidate_entries=[
            AgentCandidateEntry(
                entry_kind="rpc",
                entry_symbol="rpc_entry",
                entry_file=None,
                chain=["rpc_entry", "internal_gap"],
                external_trigger="RPC entry",
                reason="agent returned symbol without source file",
                validated=True,
            )
        ],
    )
    validated: list[dict] = []
    unverified: list[dict] = []

    coverage_mod._collect_agent_entry_results(
        [result],
        repo_root=tmp_path,
        object_id="src/internal.c:internal_gap:1",
        turn_id="coverage:src/internal.c:internal_gap:1",
        agent_session=None,
        validated_entries=validated,
        unverified_entries=unverified,
        status_by_provider={},
        raw_results=[],
    )

    assert unverified == []
    assert validated[0]["entry_file"] == "src/rpc.c"
    assert validated[0]["source_verification"] == "source_backed"
    assert validated[0]["validation_error"] is None


def test_coverage_agent_entry_collect_rebinds_wrong_file_to_entry_symbol_definition(tmp_path):
    import app.services.coverage_analyzer as coverage_mod
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "wrong.c").write_text("void unrelated_entry(void) {}\n", encoding="utf-8")
    (src / "rpc.c").write_text("void rpc_entry(void) {}\n", encoding="utf-8")
    result = AgentDiscoveryResult(
        provider="claude-code",
        status="ok",
        candidate_entries=[
            AgentCandidateEntry(
                entry_kind="rpc",
                entry_symbol="rpc_entry",
                entry_file="src/wrong.c",
                chain=["rpc_entry", "internal_gap"],
                external_trigger="RPC entry",
                reason="agent returned a stale source file for the entry symbol",
                validated=True,
            )
        ],
    )
    validated: list[dict] = []
    unverified: list[dict] = []

    coverage_mod._collect_agent_entry_results(
        [result],
        repo_root=tmp_path,
        object_id="src/internal.c:internal_gap:1",
        turn_id="coverage:src/internal.c:internal_gap:1",
        agent_session=None,
        validated_entries=validated,
        unverified_entries=unverified,
        status_by_provider={},
        raw_results=[],
    )

    assert unverified == []
    assert validated[0]["entry_file"] == "src/rpc.c"
    assert validated[0]["source_verification"] == "source_backed"
    assert validated[0]["validation_error"] is None


def test_coverage_agent_symbol_without_file_generates_black_box_ready(tmp_path, monkeypatch):
    import asyncio
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "internal.c").write_text(
        "void internal_gap(void) {\n"
        "    if (1) { return; }\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "rpc.c").write_text("void rpc_entry(void) { internal_gap(); }\n", encoding="utf-8")

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="rpc",
                        entry_symbol="rpc_entry",
                        entry_file=None,
                        chain=["rpc_entry", "internal_gap"],
                        external_trigger="RPC entry",
                        reason="agent returned symbol only",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "h,internal,src/internal.c:1-3,internal_gap,false,0\n"
    )

    design = asyncio.run(
        build_coverage_test_design(modules, workspace_id="ws-1", repo_path=str(tmp_path))
    )

    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
    assert gap["entry_paths"][0]["entry_file"] == "src/rpc.c"
    assert gap["black_box_cases"]


def test_coverage_agent_symbol_without_trigger_keeps_public_entry_label(tmp_path, monkeypatch):
    import asyncio
    import json
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "internal.c").write_text(
        "void internal_gap(void) {\n"
        "    if (1) { return; }\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "rpc.c").write_text(
        "void rpc_tls_entry(void) { internal_gap(); }\n",
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
                        entry_symbol="rpc_tls_entry",
                        entry_file=None,
                        chain=["rpc_tls_entry", "internal_gap"],
                        external_trigger="",
                        reason="agent returned public RPC symbol only",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "h,internal,src/internal.c:1-3,internal_gap,false,0\n"
    )

    design = asyncio.run(
        build_coverage_test_design(modules, workspace_id="ws-1", repo_path=str(tmp_path))
    )

    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)

    assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
    assert "RPC rpc_tls_entry" in case_text
    assert "rpc entry" not in case_text


def test_coverage_agent_self_symbol_does_not_generate_black_box_ready(tmp_path, monkeypatch):
    import asyncio
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "internal.c").write_text(
        "void internal_gap(void) {\n"
        "    if (1) { return; }\n"
        "}\n",
        encoding="utf-8",
    )

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="function",
                        entry_symbol="internal_gap",
                        entry_file="src/internal.c",
                        chain=["internal_gap"],
                        external_trigger="direct function",
                        reason="agent mistook target function for a public entry",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "h,internal,src/internal.c:1-3,internal_gap,false,0\n"
    )

    design = asyncio.run(
        build_coverage_test_design(modules, workspace_id="ws-1", repo_path=str(tmp_path))
    )

    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    assert gap["entry_paths"] == []
    assert gap["black_box_readiness"]["case_type"] != "black_box_ready"
    card = design["entry_discovery"]["cards"][0]
    candidate = card["candidate_external_entries"][0]
    assert candidate["entry_symbol"] == "internal_gap"
    assert candidate["validation_error"] == "self_target_entry"
    assert card["source_verification_status"] == "rejected_external_entry_candidate"
    assert card["gray_box_allowed"] is True


def test_coverage_agent_plain_function_entry_does_not_generate_black_box_ready(tmp_path, monkeypatch):
    import asyncio
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "internal.c").write_text(
        "void helper_wrapper(void) { internal_gap(); }\n"
        "void internal_gap(void) {\n"
        "    if (1) { return; }\n"
        "}\n",
        encoding="utf-8",
    )

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="function",
                        entry_symbol="helper_wrapper",
                        entry_file="src/internal.c",
                        chain=["helper_wrapper", "internal_gap"],
                        external_trigger="helper function",
                        reason="agent returned an internal helper caller, not a public trigger",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "h,internal,src/internal.c:2-4,internal_gap,false,0\n"
    )

    design = asyncio.run(
        build_coverage_test_design(modules, workspace_id="ws-1", repo_path=str(tmp_path))
    )

    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    assert gap["entry_paths"] == []
    assert gap["black_box_readiness"]["case_type"] != "black_box_ready"
    card = design["entry_discovery"]["cards"][0]
    candidate = card["candidate_external_entries"][0]
    assert candidate["entry_symbol"] == "helper_wrapper"
    assert candidate["validation_error"] == "not_public_trigger_surface"
    assert card["source_verification_status"] == "rejected_external_entry_candidate"
    assert card["gray_box_allowed"] is True


def test_coverage_agent_generic_external_helper_does_not_generate_black_box_ready(tmp_path, monkeypatch):
    import asyncio
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "internal.c").write_text(
        "void helper_wrapper(void) { internal_gap(); }\n"
        "void internal_gap(void) {\n"
        "    if (1) { return; }\n"
        "}\n",
        encoding="utf-8",
    )

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="external",
                        entry_symbol="helper_wrapper",
                        entry_file="src/internal.c",
                        chain=["helper_wrapper", "internal_gap"],
                        external_trigger="helper function",
                        reason="agent used a generic external label for an internal helper",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "h,internal,src/internal.c:2-4,internal_gap,false,0\n"
    )

    design = asyncio.run(
        build_coverage_test_design(modules, workspace_id="ws-1", repo_path=str(tmp_path))
    )

    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    assert gap["entry_paths"] == []
    assert gap["black_box_readiness"]["case_type"] != "black_box_ready"
    card = design["entry_discovery"]["cards"][0]
    candidate = card["candidate_external_entries"][0]
    assert candidate["entry_symbol"] == "helper_wrapper"
    assert candidate["validation_error"] == "not_public_trigger_surface"
    assert card["source_verification_status"] == "rejected_external_entry_candidate"
    assert card["gray_box_allowed"] is True


def test_coverage_agent_generic_external_with_rpc_trigger_generates_black_box_ready(tmp_path, monkeypatch):
    import asyncio
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design
    from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

    src = tmp_path / "src"
    src.mkdir()
    (src / "internal.c").write_text(
        "void internal_gap(void) {\n"
        "    if (1) { return; }\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "rpc.c").write_text("void rpc_entry(void) { internal_gap(); }\n", encoding="utf-8")

    async def fake_discovery(_request, **_kwargs):
        return [
            AgentDiscoveryResult(
                provider="claude-code",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="external",
                        entry_symbol="rpc_entry",
                        entry_file="src/rpc.c",
                        chain=["rpc_entry", "internal_gap"],
                        external_trigger="RPC request rpc-entry",
                        reason="agent used a generic external kind but supplied an RPC trigger",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "h,internal,src/internal.c:1-3,internal_gap,false,0\n"
    )

    design = asyncio.run(
        build_coverage_test_design(modules, workspace_id="ws-1", repo_path=str(tmp_path))
    )

    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
    assert gap["entry_paths"][0]["entry_file"] == "src/rpc.c"


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
    same_symbol_candidates = [
        item for item in card["candidate_external_entries"]
        if item.get("entry_symbol") == "maybe_cli"
    ]
    assert len(same_symbol_candidates) == 1


def test_coverage_agent_unverified_symbol_without_trigger_keeps_public_label(tmp_path, monkeypatch):
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
                        external_trigger="",
                        reason="unverified CLI symbol only",
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
    card = design["entry_discovery"]["cards"][0]
    candidate = card["candidate_external_entries"][0]

    assert gap["entry_paths"] == []
    assert gap["black_box_readiness"]["case_type"] != "black_box_ready"
    assert candidate["entry_label"] == "CLI maybe_cli"
    assert candidate["validation_error"] == "file_not_found"


def test_coverage_agent_first_round_failure_is_visible_in_entry_card(tmp_path, monkeypatch):
    import app.services.coverage_analyzer as coverage_mod
    from app.services.coverage_analyzer import build_coverage_test_design

    src = tmp_path / "src"
    src.mkdir()
    (src / "util.c").write_text(
        "void internal_helper(void) {\n"
        "    if (1) { return; }\n"
        "}\n",
        encoding="utf-8",
    )

    async def fake_discovery(_request, **_kwargs):
        raise RuntimeError("agent backend spawn failed")

    monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)
    modules = _coverage_modules(
        "feature,module,code_location,function,triggered,hit_count\n"
        "h,util,src/util.c:1-3,internal_helper,false,0\n"
    )

    design = asyncio.run(
        build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
            artifact_dir=tmp_path / "artifacts",
            analysis_id="cov-1",
        )
    )

    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    card = design["entry_discovery"]["cards"][0]
    assert gap["tool_status"]["external_agent"] == "error"
    assert card["external_agent"]["status"] == "error"
    assert card["external_agent"]["warnings"] == ["agent backend spawn failed"]


def test_coverage_agent_repeated_unverified_entry_across_rounds_is_deduped(tmp_path, monkeypatch):
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
    calls = []

    async def fake_discovery(request, **_kwargs):
        calls.append(request.request_id)
        return [
            AgentDiscoveryResult(
                provider="opencode",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="cli",
                        entry_symbol="maybe_cli",
                        entry_file=None,
                        chain=["maybe_cli", "internal_helper"],
                        external_trigger="CLI maybe",
                        reason="still missing source file",
                        validated=False,
                        validation_error="entry_file_missing",
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
        build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
            artifact_dir=tmp_path / "artifacts",
            analysis_id="cov-1",
        )
    )

    assert any("round2" in call for call in calls)
    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    assert len(gap["evidence"]["external_agent"]["unverified_entries"]) == 1
    card = design["entry_discovery"]["cards"][0]
    same_symbol_candidates = [
        item for item in card["candidate_external_entries"]
        if item.get("entry_symbol") == "maybe_cli"
    ]
    assert len(same_symbol_candidates) == 1
    assert same_symbol_candidates[0]["turn_id"].endswith(":round2")


def test_coverage_agent_round2_failure_keeps_round1_candidate(tmp_path, monkeypatch):
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
    calls: list[str] = []

    async def fake_discovery(request, **_kwargs):
        calls.append(request.request_id)
        if "round2" in request.request_id:
            raise RuntimeError("round2 agent crashed")
        return [
            AgentDiscoveryResult(
                provider="opencode",
                status="ok",
                candidate_entries=[
                    AgentCandidateEntry(
                        entry_kind="cli",
                        entry_symbol="maybe_cli",
                        entry_file=None,
                        chain=["maybe_cli", "internal_helper"],
                        external_trigger="CLI maybe",
                        reason="round 1 candidate still useful",
                        validated=False,
                        validation_error="entry_file_missing",
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
        build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
            artifact_dir=tmp_path / "artifacts",
            analysis_id="cov-1",
        )
    )

    assert any("round2" in call for call in calls)
    gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
    card = design["entry_discovery"]["cards"][0]
    assert gap["entry_paths"] == []
    assert card["candidate_external_entries"][0]["entry_symbol"] == "maybe_cli"
    raw_results = gap["evidence"]["external_agent"]["raw_results"]
    assert any(item["status"] == "error" and "round2 agent crashed" in item["raw_summary"] for item in raw_results)


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


def test_coverage_agent_unverified_entry_without_file_triggers_round2(tmp_path, monkeypatch):
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
                    candidate_entries=[
                        AgentCandidateEntry(
                            entry_kind="rpc",
                            entry_symbol="maybe_rpc_tls_entry",
                            entry_file=None,
                            chain=["maybe_rpc_tls_entry", "internal_tls_gap"],
                            external_trigger="RPC tls-entry",
                            reason="candidate entry lacks source file and needs another search round",
                            validated=False,
                            validation_error="entry_file_missing",
                        )
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
                        reason="round 2 source context verified the public entry",
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
    assert gap["entry_paths"][0]["entry_symbol"] == "rpc_tls_entry"
    card = design["entry_discovery"]["cards"][0]
    same_symbol_candidates = [
        candidate for candidate in card["candidate_external_entries"]
        if candidate.get("entry_symbol") == "rpc_tls_entry"
    ]
    assert [candidate["source_verification"] for candidate in same_symbol_candidates] == [
        "source_backed"
    ]
