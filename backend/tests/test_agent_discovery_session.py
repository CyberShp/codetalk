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
