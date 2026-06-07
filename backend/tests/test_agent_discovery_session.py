import json
from pathlib import Path


def test_session_can_save_and_load_ledger(tmp_path):
    from app.services.agent_discovery_session import create_agent_discovery_session

    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="workspace_scope",
        task_id="task-1",
        workspace_id="ws-1",
        artifact_dir=tmp_path / "artifacts",
    )
    session.ledger.add_validated_file(
        object_id="obj_tls",
        path="nof/nvmf_tcp/transport/tls/tls.c",
        provider="claude-code",
        reason="validated source",
    )
    session.save()

    loaded = create_agent_discovery_session.load(tmp_path / "artifacts")

    assert loaded.session_id == session.session_id
    assert loaded.ledger.validated_files[0]["path"] == "nof/nvmf_tcp/transport/tls/tls.c"
    assert (tmp_path / "artifacts" / "agent_discovery_session.json").exists()
    assert (tmp_path / "artifacts" / "agent_discovery_ledger.json").exists()


def test_session_load_tolerates_legacy_and_future_artifact_fields(tmp_path):
    from app.services.agent_discovery_session import create_agent_discovery_session

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "agent_discovery_session.json").write_text(
        json.dumps({
            "session_id": "sess-legacy",
            "repo_path": str(tmp_path),
            "goal": "coverage_entry",
            "artifact_dir": str(artifact_dir),
            "turns": [{
                "turn_id": "turn_001_claude_code",
                "provider": "claude-code",
                "goal": "coverage_entry",
                "status": "ok",
                "parsed_result": {"raw_summary": "legacy artifact"},
                "future_turn_field": "ignored",
            }],
            "future_session_field": "ignored",
        }),
        encoding="utf-8",
    )
    (artifact_dir / "agent_discovery_ledger.json").write_text(
        json.dumps({
            "validated_files": [{
                "object_id": "obj",
                "path": "src/tls.c",
                "provider": "claude-code",
            }],
            "future_ledger_field": [{"ignored": True}],
        }),
        encoding="utf-8",
    )

    loaded = create_agent_discovery_session.load(artifact_dir)

    assert loaded.session_id == "sess-legacy"
    assert loaded.turns[0].validation_result == {}
    assert loaded.turns[0].prompt_path is None
    assert loaded.ledger.validated_files[0]["path"] == "src/tls.c"
    assert not hasattr(loaded.ledger, "future_ledger_field")


def test_rejected_file_enters_context_do_not_repeat(tmp_path):
    from app.services.agent_discovery_session import (
        AgentContextPacketInput,
        create_agent_discovery_session,
    )

    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="workspace_scope",
        artifact_dir=tmp_path / "artifacts",
    )
    session.ledger.add_rejected_file(
        object_id="obj_tls",
        path="missing/tls.c",
        provider="opencode",
        reason="file_not_found",
    )

    packet = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj_tls",
            current_goal="source_scope",
            analysis_object_text="nvme-tcp-tls",
            expanded_terms=["nvmf_tcp/transport/tls"],
        )
    )

    assert "missing/tls.c" in packet["do_not_repeat"]["paths"]
    assert packet["rejected_facts"]["files"][0]["reason"] == "file_not_found"
    assert (tmp_path / "artifacts" / "external_agent_context_packets" / "packet_001.json").exists()


def test_context_packet_treats_legacy_facts_without_object_id_as_global(tmp_path):
    from app.services.agent_discovery_session import (
        AgentContextPacketInput,
        create_agent_discovery_session,
    )

    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="workspace_scope",
        artifact_dir=tmp_path / "artifacts",
    )
    session.ledger.validated_files.append({
        "path": "src/legacy.c",
        "provider": "claude-code",
        "reason": "legacy artifact before object_id existed",
    })
    session.ledger.rejected_files.append({
        "path": "src/missing.c",
        "provider": "claude-code",
        "reason": "file_not_found",
    })

    packet = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj-new",
            current_goal="source_scope",
            analysis_object_text="legacy",
            expanded_terms=["legacy"],
        )
    )

    assert packet["validated_facts"]["files"][0]["path"] == "src/legacy.c"
    assert packet["rejected_facts"]["files"][0]["path"] == "src/missing.c"
    assert "src/missing.c" in packet["do_not_repeat"]["paths"]


def test_rejected_entry_file_enters_context_do_not_repeat(tmp_path):
    from app.services.agent_discovery_session import (
        AgentContextPacketInput,
        create_agent_discovery_session,
    )

    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )
    session.ledger.add_rejected_entry({
        "object_id": "obj_rpc",
        "provider": "claude-code",
        "entry_symbol": "rpc_entry",
        "entry_file": "src",
        "validation_error": "directory_candidate_not_allowed",
    })

    packet = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj_rpc",
            current_goal="coverage_entry",
            analysis_object_text="internal_gap",
            expanded_terms=["internal_gap"],
        )
    )

    assert "src" in packet["do_not_repeat"]["paths"]
    assert packet["rejected_facts"]["entries"][0]["validation_error"] == "directory_candidate_not_allowed"


def test_rejected_entry_symbol_enters_context_do_not_repeat(tmp_path):
    from app.services.agent_discovery_session import (
        AgentContextPacketInput,
        create_agent_discovery_session,
    )

    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )
    session.ledger.add_rejected_entry({
        "object_id": "obj_rpc",
        "provider": "claude-code",
        "entry_symbol": "rpc_entry",
        "entry_file": None,
        "validation_error": "entry_file_missing",
    })

    packet = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj_rpc",
            current_goal="coverage_entry",
            analysis_object_text="internal_gap",
            expanded_terms=["internal_gap"],
        )
    )

    assert "rpc_entry" in packet["do_not_repeat"]["entry_symbols"]


def test_entry_ledger_dedupes_per_object(tmp_path):
    from app.services.agent_discovery_session import (
        AgentContextPacketInput,
        create_agent_discovery_session,
    )

    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )
    for object_id in ("obj-a", "obj-b"):
        session.ledger.add_rejected_entry({
            "object_id": object_id,
            "provider": "claude-code",
            "entry_symbol": "rpc_entry",
            "entry_file": None,
            "validation_error": "entry_file_missing",
        })
        session.ledger.add_validated_entry({
            "object_id": object_id,
            "provider": "claude-code",
            "entry_symbol": "rpc_entry",
            "entry_file": "src/rpc.c",
            "validation_error": None,
        })

    packet_a = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj-a",
            current_goal="coverage_entry",
            analysis_object_text="gap-a",
            expanded_terms=["gap-a"],
        )
    )
    packet_b = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj-b",
            current_goal="coverage_entry",
            analysis_object_text="gap-b",
            expanded_terms=["gap-b"],
        )
    )

    assert [item["object_id"] for item in session.ledger.rejected_entries] == ["obj-a", "obj-b"]
    assert [item["object_id"] for item in session.ledger.validated_entries] == ["obj-a", "obj-b"]
    assert packet_a["rejected_facts"]["entries"][0]["object_id"] == "obj-a"
    assert packet_b["rejected_facts"]["entries"][0]["object_id"] == "obj-b"
    assert packet_a["validated_facts"]["entries"][0]["object_id"] == "obj-a"
    assert packet_b["validated_facts"]["entries"][0]["object_id"] == "obj-b"


def test_entry_ledger_preserves_list_evidence_on_duplicate_update(tmp_path):
    from app.services.agent_discovery_session import (
        AgentContextPacketInput,
        create_agent_discovery_session,
    )

    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )
    session.ledger.add_validated_entry({
        "object_id": "obj",
        "provider": "claude-code",
        "entry_symbol": "rpc_tls_entry",
        "entry_file": "src/rpc.c",
        "input_hints": ["invalid TLS PSK", "oversized capsule"],
        "chain": ["rpc_tls_entry", "tls_handshake"],
    })
    session.ledger.add_validated_entry({
        "object_id": "obj",
        "provider": "claude-code",
        "entry_symbol": "rpc_tls_entry",
        "entry_file": "src/rpc.c",
        "input_hints": [],
        "chain": ["tls_handshake", "tls_error_path"],
    })

    packet = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj",
            current_goal="coverage_entry",
            analysis_object_text="tls_handshake",
            expanded_terms=["tls_handshake"],
        )
    )

    entry = packet["validated_facts"]["entries"][0]
    assert entry["input_hints"] == ["invalid TLS PSK", "oversized capsule"]
    assert entry["chain"] == ["rpc_tls_entry", "tls_handshake", "tls_error_path"]


def test_entry_ledger_preserves_existing_trigger_and_reason(tmp_path):
    from app.services.agent_discovery_session import (
        AgentContextPacketInput,
        create_agent_discovery_session,
    )

    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )
    session.ledger.add_validated_entry({
        "object_id": "obj",
        "provider": "claude-code",
        "entry_symbol": "rpc_tls_entry",
        "entry_file": "src/rpc.c",
        "external_trigger": "RPC tls-entry",
        "reason": "public RPC handler reaches TLS handshake",
    })
    session.ledger.add_validated_entry({
        "object_id": "obj",
        "provider": "claude-code",
        "entry_symbol": "rpc_tls_entry",
        "entry_file": "src/rpc.c",
        "external_trigger": "",
        "reason": "",
    })

    packet = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj",
            current_goal="coverage_entry",
            analysis_object_text="tls_handshake",
            expanded_terms=["tls_handshake"],
        )
    )

    entry = packet["validated_facts"]["entries"][0]
    assert entry["external_trigger"] == "RPC tls-entry"
    assert entry["reason"] == "public RPC handler reaches TLS handshake"


def test_raw_output_is_not_used_as_fact_in_context_packet(tmp_path):
    from app.services.agent_discovery_session import (
        AgentContextPacketInput,
        create_agent_discovery_session,
    )

    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="workspace_scope",
        artifact_dir=tmp_path / "artifacts",
    )
    session.record_turn(
        provider="claude-code",
        goal="source_scope",
        prompt="prompt",
        raw_output="RAW SUMMARY THAT SHOULD NOT BECOME MEMORY",
        parsed_result={"raw_summary": "same"},
        validation_result={},
        status="ok",
    )

    packet = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj_tls",
            current_goal="source_scope",
            analysis_object_text="nvme-tcp-tls",
            expanded_terms=["tls"],
        )
    )

    payload = json.dumps(packet, ensure_ascii=False)
    assert "RAW SUMMARY THAT SHOULD NOT BECOME MEMORY" not in payload
    assert packet["previous_agent_findings"] == []


def test_source_slice_rejects_outside_repo_and_non_source(tmp_path):
    from app.services.agent_discovery_session import create_agent_discovery_session

    readme = tmp_path / "README.md"
    readme.write_text("not source\n", encoding="utf-8")
    outside = tmp_path.parent / "outside.c"
    outside.write_text("int outside;\n", encoding="utf-8")
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )

    assert session.add_source_slice("README.md", symbol=None, reason="bad").validated is False
    assert session.add_source_slice(str(outside), symbol=None, reason="bad").validated is False
    assert len(session.ledger.rejected_files) == 2


def test_source_slice_rejects_directory_candidate(tmp_path):
    from app.services.agent_discovery_session import create_agent_discovery_session

    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )

    ref = session.add_source_slice("src", symbol=None, reason="directory request")

    assert ref.validated is False
    assert ref.file_path == "src"
    assert ref.validation_error == "directory_candidate_not_allowed"
    assert session.ledger.source_slices == []
    assert session.ledger.rejected_files[0]["reason"] == "directory_candidate_not_allowed"


def test_source_slice_saves_hash_and_excerpt(tmp_path):
    from app.services.agent_discovery_session import create_agent_discovery_session

    source = tmp_path / "src" / "tls.c"
    source.parent.mkdir()
    source.write_text(
        "\n".join(f"int line_{i};" for i in range(1, 40)),
        encoding="utf-8",
    )
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )

    ref = session.add_source_slice("src/tls.c", symbol="line_20", reason="need context")

    assert ref.validated is True
    assert ref.file_path == "src/tls.c"
    assert ref.sha256
    assert "line_20" in ref.excerpt
    assert (tmp_path / "artifacts" / "external_agent_source_slices").exists()


def test_source_slices_are_scoped_to_context_object(tmp_path):
    from app.services.agent_discovery_session import (
        AgentContextPacketInput,
        create_agent_discovery_session,
    )

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.c").write_text("int only_for_a(void) { return 1; }\n", encoding="utf-8")
    (src / "b.c").write_text("int only_for_b(void) { return 2; }\n", encoding="utf-8")
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )

    session.add_source_slice(
        "src/a.c",
        symbol="only_for_a",
        reason="obj-a requested context",
        object_id="obj-a",
    )

    packet_b = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj-b",
            current_goal="coverage_entry",
            analysis_object_text="gap-b",
            expanded_terms=["gap-b"],
        )
    )

    assert packet_b["relevant_source_slices"] == []


def test_invalid_source_slice_requests_do_not_consume_read_budget(tmp_path, monkeypatch):
    from app.services.agent_discovery_session import create_agent_discovery_session

    monkeypatch.setattr(
        "app.services.agent_discovery_session.settings.agent_discovery_max_source_slices",
        1,
        raising=False,
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )

    refs = session.add_source_slices_from_requests([
        {"file_path": "src", "reason": "directory should be rejected"},
        {"file_path": "src/tls.c", "reason": "valid source"},
    ])

    assert [ref.validated for ref in refs] == [False, True]
    assert len(session.ledger.source_slices) == 1
    assert session.ledger.source_slices[0]["file_path"] == "src/tls.c"


def test_duplicate_source_slice_requests_do_not_consume_read_budget(tmp_path, monkeypatch):
    from app.services.agent_discovery_session import create_agent_discovery_session

    monkeypatch.setattr(
        "app.services.agent_discovery_session.settings.agent_discovery_max_source_slices",
        2,
        raising=False,
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    (src / "rpc.c").write_text("int rpc;\n", encoding="utf-8")
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )

    refs = session.add_source_slices_from_requests([
        {"file_path": "src/tls.c", "symbol": "tls", "reason": "first"},
        {"file_path": "src/tls.c", "symbol": "tls", "reason": "duplicate"},
        {"file_path": "src/rpc.c", "symbol": "rpc", "reason": "second unique"},
    ])

    assert [ref.file_path for ref in refs if ref.validated] == ["src/tls.c", "src/rpc.c"]
    assert [item["file_path"] for item in session.ledger.source_slices] == [
        "src/tls.c",
        "src/rpc.c",
    ]


def test_duplicate_source_slice_requests_are_deduped_per_object(tmp_path, monkeypatch):
    from app.services.agent_discovery_session import create_agent_discovery_session

    monkeypatch.setattr(
        "app.services.agent_discovery_session.settings.agent_discovery_max_source_slices",
        4,
        raising=False,
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "shared.c").write_text("int shared_entry(void) { return 1; }\n", encoding="utf-8")
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )

    refs_a = session.add_source_slices_from_requests(
        [{"file_path": "src/shared.c", "symbol": "shared_entry", "reason": "obj-a"}],
        object_id="obj-a",
    )
    refs_b = session.add_source_slices_from_requests(
        [{"file_path": "src/shared.c", "symbol": "shared_entry", "reason": "obj-b"}],
        object_id="obj-b",
    )

    assert [ref.object_id for ref in refs_a if ref.validated] == ["obj-a"]
    assert [ref.object_id for ref in refs_b if ref.validated] == ["obj-b"]
    assert [
        (item["object_id"], item["file_path"], item["symbol"])
        for item in session.ledger.source_slices
    ] == [
        ("obj-a", "src/shared.c", "shared_entry"),
        ("obj-b", "src/shared.c", "shared_entry"),
    ]


def test_duplicate_invalid_source_slice_requests_do_not_duplicate_rejections(tmp_path, monkeypatch):
    from app.services.agent_discovery_session import create_agent_discovery_session

    monkeypatch.setattr(
        "app.services.agent_discovery_session.settings.agent_discovery_max_source_slices",
        1,
        raising=False,
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )

    refs = session.add_source_slices_from_requests([
        {"file_path": "src", "reason": "directory"},
        {"file_path": "src", "reason": "same directory again"},
        {"file_path": "src/tls.c", "reason": "valid source"},
    ])

    assert [ref.validated for ref in refs] == [False, True]
    assert len(session.ledger.rejected_files) == 1
    assert session.ledger.rejected_files[0]["path"] == "src"
    assert session.ledger.source_slices[0]["file_path"] == "src/tls.c"


def test_invalid_source_slice_rejections_are_deduped_per_object(tmp_path, monkeypatch):
    from app.services.agent_discovery_session import create_agent_discovery_session

    monkeypatch.setattr(
        "app.services.agent_discovery_session.settings.agent_discovery_max_source_slices",
        1,
        raising=False,
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )

    first = session.add_source_slices_from_requests(
        [{"file_path": "src", "reason": "directory"}],
        object_id="obj-a",
    )
    second = session.add_source_slices_from_requests(
        [{"file_path": "src", "reason": "directory"}],
        object_id="obj-b",
    )

    assert [ref.validated for ref in first] == [False]
    assert [ref.validated for ref in second] == [False]
    assert [
        (item["object_id"], item["path"], item["reason"])
        for item in session.ledger.rejected_files
    ] == [
        ("obj-a", "src", "directory_candidate_not_allowed"),
        ("obj-b", "src", "directory_candidate_not_allowed"),
    ]


def test_context_packet_overflow_requests_next_round(tmp_path, monkeypatch):
    from app.services.agent_discovery_session import (
        AgentContextPacketInput,
        create_agent_discovery_session,
    )

    monkeypatch.setattr(
        "app.services.agent_discovery_session.settings.agent_discovery_context_packet_max_chars",
        500,
        raising=False,
    )
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="workspace_scope",
        artifact_dir=tmp_path / "artifacts",
    )
    for idx in range(30):
        session.ledger.add_validated_file(
            object_id="obj",
            path=f"src/file_{idx}.c",
            provider="local",
            reason="x" * 80,
        )

    packet = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj",
            current_goal="source_scope",
            analysis_object_text="big",
            expanded_terms=["big"],
        )
    )

    assert packet["context_overflow"]["overflow"] is True
    assert packet["context_overflow"]["policy"] == "request_more_in_next_round"


def test_context_packet_overflow_trims_rejected_entries(tmp_path, monkeypatch):
    from app.services.agent_discovery_session import (
        AgentContextPacketInput,
        create_agent_discovery_session,
    )

    monkeypatch.setattr(
        "app.services.agent_discovery_session.settings.agent_discovery_context_packet_max_chars",
        800,
        raising=False,
    )
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )
    for idx in range(30):
        session.ledger.add_rejected_entry({
            "object_id": "obj",
            "provider": "claude-code",
            "entry_symbol": f"rpc_entry_{idx}",
            "entry_file": f"src/missing_{idx}.c",
            "validation_error": "file_not_found",
            "reason": "x" * 120,
        })

    packet = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj",
            current_goal="coverage_entry",
            analysis_object_text="target",
            expanded_terms=["target"],
        )
    )

    assert packet["context_overflow"]["overflow"] is True
    assert len(packet["rejected_facts"]["entries"]) <= 10
    assert len(packet["do_not_repeat"]["entry_symbols"]) <= 10


def test_context_packet_overflow_enforces_configured_char_budget(tmp_path, monkeypatch):
    from app.services.agent_discovery_session import (
        AgentContextPacketInput,
        create_agent_discovery_session,
    )

    max_chars = 1200
    monkeypatch.setattr(
        "app.services.agent_discovery_session.settings.agent_discovery_context_packet_max_chars",
        max_chars,
        raising=False,
    )
    session = create_agent_discovery_session(
        repo_path=str(tmp_path),
        goal="coverage_entry",
        artifact_dir=tmp_path / "artifacts",
    )
    for idx in range(25):
        session.ledger.add_validated_file(
            object_id="obj",
            path=f"src/very/deep/path/file_{idx}.c",
            provider="claude-code",
            reason="validated " + ("x" * 200),
        )
        session.ledger.add_rejected_entry({
            "object_id": "obj",
            "provider": "claude-code",
            "entry_symbol": f"rpc_entry_{idx}",
            "entry_file": f"src/missing_{idx}.c",
            "validation_error": "file_not_found",
            "reason": "rejected " + ("y" * 200),
        })

    packet = session.build_context_packet(
        AgentContextPacketInput(
            object_id="obj",
            current_goal="coverage_entry",
            analysis_object_text="target",
            expanded_terms=[f"term_{idx}_{'z' * 40}" for idx in range(30)],
        )
    )

    assert packet["context_overflow"]["overflow"] is True
    assert len(json.dumps(packet, ensure_ascii=False)) <= max_chars
    assert "validated_facts" in packet["context_overflow"]["dropped_sections"]
