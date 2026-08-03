from __future__ import annotations

import base64
import hashlib
import json
import multiprocessing
import os
import secrets
import signal
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.services.quality_benchmark_generator import (
    _artifact_hash_manifest,
    _generator_output_schema,
    _generator_prompt,
    _materialize_candidate,
    _verify_artifact_hash_manifest,
    generate_quality_benchmark_artifacts,
)
from app.services.quality_benchmark_workbench import BenchmarkWorkbenchResult
from app.services.workbench_workflow_runner import (
    _apply_benchmark_work_sufficiency,
)
from jsonschema import Draft202012Validator


def test_generator_prompt_uses_explicit_public_analysis_target() -> None:
    prompt = _generator_prompt(
        case_id="bmcweb-redfish-reset-action-info-001",
        mode="rapid",
        analysis_target=(
            "Redfish ComputerSystem.Reset ActionInfo host-transition behavior; "
            "exclude Manager.Reset."
        ),
    )

    assert "ComputerSystem.Reset ActionInfo host-transition behavior" in prompt
    assert "exclude Manager.Reset" in prompt
    assert "gold_claims" not in prompt


def _truth_paths(tmp_path: Path) -> tuple[Path, ...]:
    truth_root = tmp_path / "hidden-truth"
    return tuple(
        truth_root / name
        for name in (
            "gold_claims.json",
            "coverage_universe.json",
            "critical_chains.json",
            "execution_oracles.json",
        )
    )


def _candidate_response() -> dict:
    return {
        "claims": [{
            "claim_id": "C1",
            "claim": "line two is observed",
            "semantic_key": "public.line.two",
            "critical": True,
            "evidence_refs": [
                {"path": "storage.c", "start_line": 2, "end_line": 2}
            ],
        }],
        "breadth_candidates": [{
            "candidate_id": "B1",
            "narrative": "The source observation covers line two.",
            "evidence_refs": ["source://storage.c#L2-L2"],
        }],
        "breadth_scenarios": [{
            "scenario_id": "S1",
            "candidate_ids": ["B1"],
            "status": "READY",
            "narrative": "Observe line two through the public scenario.",
            "evidence_refs": ["source://storage.c#L2-L2"],
        }],
        "depth_chains": [{
            "chain_id": "D1",
            "nodes": [{
                "node_id": "N1",
                "status": "closed",
                "narrative": "Line two establishes the chain node.",
                "evidence_refs": ["source://storage.c#L2-L2"],
            }],
            "edges": [{
                "edge_id": "E1",
                "status": "closed",
                "narrative": "Line two connects the observed node to its effect.",
                "evidence_refs": ["source://storage.c#L2-L2"],
            }],
            "disconfirming_checks": [{
                "check_id": "K1",
                "status": "pass",
                "narrative": "Line two rules out the reversed observation.",
                "evidence_refs": ["source://storage.c#L2-L2"],
            }],
            "narrative": "public chain",
        }],
    }


def _fake_workbench_result(
    tmp_path: Path, *, cache_reused: bool = False
) -> BenchmarkWorkbenchResult:
    task_artifact = tmp_path / "task-artifact"
    agent_artifact = task_artifact / "agent_runs" / "analyze"
    agent_artifact.mkdir(parents=True)
    response_path = agent_artifact / "benchmark_response.json"
    response_path.write_text(json.dumps(_candidate_response()), encoding="utf-8")
    (task_artifact / "execution.json").write_text(
        json.dumps({"status": "completed"}), encoding="utf-8"
    )
    return BenchmarkWorkbenchResult(
        task_run_id="task_run_test",
        status="completed",
        task_artifact_dir=task_artifact,
        response_path=response_path,
        first_response_path=response_path,
        repair_attempt_count=0,
        terminal_block_reason=None,
        work_sufficiency={
            "status": "reused" if cache_reused else "sufficient",
            "auto_continue": False,
            "elapsed_seconds": 10.0,
            "cache_reused": cache_reused,
            **(
                {
                    "reuse_source_sha256": hashlib.sha256(
                        response_path.read_bytes()
                    ).hexdigest()
                }
                if cache_reused
                else {}
            ),
            "reasons": [],
        },
    )


def _benchmark_task_run(
    tmp_path: Path, response: dict, *, cache_reused: bool = False
) -> SimpleNamespace:
    artifact_dir = tmp_path / "benchmark-task"
    agent_dir = artifact_dir / "agent_runs" / "analyze"
    agent_dir.mkdir(parents=True)
    (agent_dir / "benchmark_response.json").write_text(
        json.dumps(response), encoding="utf-8"
    )
    (agent_dir / "benchmark_codex_invocation.json").write_text(
        json.dumps({"schema_version": "quality-benchmark-codex-invocation-v1"}),
        encoding="utf-8",
    )
    return SimpleNamespace(
        workflow_snapshot={"id": "quality-benchmark-generation-v1"},
        execution_profile={"id": "rapid"},
        task_bundle={"execution_profile": {"id": "rapid"}},
        artifact_dir=str(artifact_dir),
        created_at="",
        agent_runs=[{
            "step_id": "analyze",
            "artifact_dir": str(agent_dir),
            "cache_reused": cache_reused,
            **({"reuse_source": "accepted-prior-result"} if cache_reused else {}),
        }],
    )


def test_cold_fast_shallow_benchmark_is_sent_to_existing_quality_repair(
    tmp_path,
) -> None:
    shallow = _candidate_response()

    audit, diagnostic = _apply_benchmark_work_sufficiency(
        audit={"status": "not_applicable", "issues": []},
        task_run=_benchmark_task_run(tmp_path, shallow),
        elapsed_seconds=45.0,
        remaining_seconds=600.0,
    )

    assert diagnostic["status"] == "insufficient"
    assert diagnostic["auto_continue"] is True
    assert audit["status"] == "needs_rework"
    assert audit["deliverable"] is False
    assert audit["issues"][0]["artifact"] == "benchmark_response.json"


def test_cold_fast_benchmark_with_substantive_three_axis_work_is_accepted(
    tmp_path,
) -> None:
    substantive = _candidate_response()
    substantive["claims"] = [
        json.loads(json.dumps(substantive["claims"][0])) for _ in range(4)
    ]
    substantive["breadth_candidates"] = [
        json.loads(json.dumps(substantive["breadth_candidates"][0]))
        for _ in range(4)
    ]
    substantive["breadth_scenarios"] = [
        json.loads(json.dumps(substantive["breadth_scenarios"][0]))
        for _ in range(3)
    ]
    substantive["depth_chains"][0]["nodes"] *= 2
    for index, item in enumerate(substantive["claims"]):
        item["evidence_refs"] = [
            {"path": "storage.c", "start_line": index + 1, "end_line": index + 1}
        ]

    audit, diagnostic = _apply_benchmark_work_sufficiency(
        audit={"status": "not_applicable", "issues": []},
        task_run=_benchmark_task_run(tmp_path, substantive),
        elapsed_seconds=220.363,
        remaining_seconds=600.0,
    )

    assert diagnostic["status"] == "sufficient"
    assert diagnostic["auto_continue"] is False
    assert diagnostic["axis_evidence"]["claims"] == 4
    assert audit["status"] == "not_applicable"


def test_cached_fast_benchmark_binds_reuse_to_actual_response_bytes(
    tmp_path,
) -> None:
    response = _candidate_response()
    task_run = _benchmark_task_run(tmp_path, response, cache_reused=True)
    response_path = (
        Path(task_run.agent_runs[0]["artifact_dir"]) / "benchmark_response.json"
    )

    _audit, diagnostic = _apply_benchmark_work_sufficiency(
        audit={"status": "not_applicable", "issues": []},
        task_run=task_run,
        elapsed_seconds=20.0,
        remaining_seconds=600.0,
    )

    assert diagnostic["status"] == "reused"
    assert diagnostic["cache_reused"] is True
    assert diagnostic["reuse_source_sha256"] == hashlib.sha256(
        response_path.read_bytes()
    ).hexdigest()


def _candidate_with_encoded_secret_in_every_string(secret: str) -> tuple[dict, set[str]]:
    variants = (
        secret,
        base64.b64encode(secret.encode()).decode(),
        base64.urlsafe_b64encode(secret.encode()).decode(),
        base64.urlsafe_b64encode(secret.encode()).decode().rstrip("="),
        secret.encode().hex(),
        secret.encode().hex().upper(),
        "".join(f"%{byte:02X}" for byte in secret.encode()),
        "".join(f"%{byte:02x}" for byte in secret.encode()),
        urllib.parse.quote(secret, safe=""),
        urllib.parse.quote_plus(secret, safe=""),
        urllib.parse.quote(secret, safe="").replace("%2B", "%2b").replace(
            "%2F", "%2f"
        ).replace("%3D", "%3d"),
    )
    observed: set[str] = set()
    variant_index = 0

    def inject(value):
        nonlocal variant_index
        if isinstance(value, str):
            variant = variants[variant_index % len(variants)]
            variant_index += 1
            observed.add(variant)
            return f"{value} credential={variant}"
        if isinstance(value, list):
            return [inject(item) for item in value]
        if isinstance(value, dict):
            return {key: inject(item) for key, item in value.items()}
        return value

    return inject(_candidate_response()), observed


def test_generator_schema_and_materializer_never_require_hidden_truth_ids(tmp_path) -> None:
    schema_text = json.dumps(_generator_output_schema())
    for forbidden in ("gold_id", "coverage_item_ids", "universe_item_ids", "truth_package"):
        assert forbidden not in schema_text

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\nthree\n", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    _materialize_candidate(
        {
            "claims": [{
                "claim_id": "C1",
                "claim": "line two is observed",
                "semantic_key": "public.line.two",
                "critical": True,
                "evidence_refs": [{"path": "storage.c", "start_line": 2, "end_line": 2}],
            }],
            "breadth_candidates": [{
                "candidate_id": "B1",
                "narrative": "The source observation covers line two.",
                "evidence_refs": ["source://storage.c#L2-L2"],
            }],
            "breadth_scenarios": [{
                "scenario_id": "S1",
                "candidate_ids": ["B1"],
                "status": "READY",
                "narrative": "Observe line two through the public scenario.",
                "evidence_refs": ["source://storage.c#L2-L2"],
            }],
            "depth_chains": [{
                "chain_id": "D1",
                "nodes": [],
                "edges": [],
                "disconfirming_checks": [],
                "narrative": "public chain",
            }],
        },
        source_dir=source,
        output_dir=output,
    )

    ledger = json.loads((output / "claim_ledger.json").read_text())
    cards = json.loads((output / "evidence_cards.json").read_text())
    assert ledger["claims"][0]["l1_status"] == "verified"
    assert cards[0]["excerpt"] == "two"
    published = "\n".join(
        f"{path.name}\n{path.read_text(encoding='utf-8')}"
        for path in output.iterdir()
    )
    assert "gold" not in published


@pytest.mark.parametrize(
    "collection,index,field",
    [
        ("breadth_candidates", 0, "narrative"),
        ("breadth_scenarios", 0, "narrative"),
        ("depth_nodes", 0, "narrative"),
        ("depth_edges", 0, "narrative"),
        ("depth_checks", 0, "narrative"),
    ],
)
def test_generator_schema_rejects_missing_per_observation_semantics(
    collection, index, field
) -> None:
    payload = _candidate_response()
    if collection == "depth_nodes":
        target = payload["depth_chains"][0]["nodes"][index]
    elif collection == "depth_edges":
        target = payload["depth_chains"][0]["edges"][index]
    elif collection == "depth_checks":
        target = payload["depth_chains"][0]["disconfirming_checks"][index]
    else:
        target = payload[collection][index]
    target.pop(field)

    errors = list(
        Draft202012Validator(_generator_output_schema()).iter_errors(payload)
    )

    assert errors
    assert any("narrative" in error.message for error in errors)


def test_materializer_rejects_traversal_and_invalid_line_evidence(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\n", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    response = {
        "claims": [{
            "claim_id": "C1",
            "claim": "unsafe",
            "semantic_key": "unsafe",
            "critical": True,
            "evidence_refs": [
                {"path": "../secret", "start_line": 1, "end_line": 1},
                {"path": "storage.c", "start_line": 2, "end_line": 2},
            ],
        }],
        "breadth_candidates": [],
        "breadth_scenarios": [],
        "depth_chains": [],
    }

    _materialize_candidate(response, source_dir=source, output_dir=output)

    ledger = json.loads((output / "claim_ledger.json").read_text())
    assert ledger["claims"][0]["evidence_refs"] == []
    assert ledger["claims"][0]["verification_status"] == "insufficient"


def test_generator_executes_through_workbench_and_publishes_content_hash_manifest(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_workbench(**kwargs):
        seen.update(kwargs)
        return _fake_workbench_result(tmp_path)

    monkeypatch.setattr(
        "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
        fake_workbench,
    )
    output = tmp_path / "attempt"

    generate_quality_benchmark_artifacts(
        case_id="workbench-case",
        source_dir=source,
        output_dir=output,
        model="test-model",
        mode="rapid",
        timeout_seconds=30,
        codetalk_revision="test-revision",
        truth_paths=_truth_paths(tmp_path),
    )

    assert seen["source_dir"] == source.resolve()
    assert seen["model"] == "test-model"
    assert seen["mode"] == "rapid"
    assert float(seen["deadline_monotonic"]) > time.monotonic()
    manifest = json.loads((output / "artifact_hash_manifest.json").read_text())
    assert set(manifest["artifacts"]) == {
        "benchmark_response.json",
        "generation_manifest.json",
        "repair_summary.json",
        "versions.json",
        "workbench_audit.json",
        "first_pass/claim_ledger.json",
        "first_pass/evidence_cards.json",
        "first_pass/quality_breadth.json",
        "first_pass/quality_depth_candidate.json",
        "final_after_auto_repair/claim_ledger.json",
        "final_after_auto_repair/evidence_cards.json",
        "final_after_auto_repair/quality_breadth.json",
        "final_after_auto_repair/quality_depth_candidate.json",
    }
    assert len(manifest["root_sha256"]) == 64
    generation = json.loads((output / "generation_manifest.json").read_text())
    versions = json.loads((output / "versions.json").read_text())
    assert generation["runtime"] == "codetalk-workbench"
    assert generation["task_run_id"] == "task_run_test"
    assert generation["work_sufficiency"]["status"] == "sufficient"
    assert versions["evaluator"] == "quality-evaluation-v5"
    assert json.loads((output / "repair_summary.json").read_text()) == {
        "attempt_count": 0,
        "elapsed_seconds": pytest.approx(generation["elapsed_seconds"], abs=0.01),
        "terminal_block_reason": None,
    }
    assert (output / "first_pass" / "claim_ledger.json").read_bytes() == (
        output / "final_after_auto_repair" / "claim_ledger.json"
    ).read_bytes()
    _verify_artifact_hash_manifest(output)
    for path in [output, *output.rglob("*")]:
        assert path.stat().st_mode & 0o222 == 0


def test_generator_propagates_workbench_cache_reuse(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
        lambda **_kwargs: _fake_workbench_result(tmp_path, cache_reused=True),
    )
    output = tmp_path / "cached"

    generate_quality_benchmark_artifacts(
        case_id="cached-case",
        source_dir=source,
        output_dir=output,
        model="test-model",
        mode="rapid",
        timeout_seconds=30,
        codetalk_revision="test-revision",
        truth_paths=_truth_paths(tmp_path),
    )

    generation = json.loads((output / "generation_manifest.json").read_text())
    response_sha256 = hashlib.sha256(
        json.dumps(_candidate_response()).encode("utf-8")
    ).hexdigest()
    assert generation["cache_reused"] is True
    assert generation["work_sufficiency"]["status"] == "reused"
    assert generation["work_sufficiency"]["reuse_source_sha256"] == response_sha256
    audit = json.loads((output / "workbench_audit.json").read_text())
    assert audit["task_artifact_hashes"]["benchmark_response.json"] == response_sha256
    assert hashlib.sha256(
        (output / "benchmark_response.json").read_bytes()
    ).hexdigest() == response_sha256


def test_generator_hash_manifest_includes_nested_same_name_file(tmp_path) -> None:
    root = tmp_path / "generator"
    nested = root / "first_pass" / "artifact_hash_manifest.json"
    nested.parent.mkdir(parents=True)
    nested.write_text('{"candidate":"not-control-metadata"}', encoding="utf-8")

    manifest = _artifact_hash_manifest(root)

    assert "first_pass/artifact_hash_manifest.json" in manifest["artifacts"]


def test_generator_rejects_truth_leak_before_workbench_invocation(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    invoked = False

    def forbidden_workbench(**_kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("Workbench must not see a truth-bearing surface")

    monkeypatch.setattr(
        "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
        forbidden_workbench,
    )
    output = tmp_path / "truth-leak"

    with pytest.raises(RuntimeError, match="immutable failure evidence"):
        generate_quality_benchmark_artifacts(
            case_id="gold_claims.json",
            source_dir=source,
            output_dir=output,
            model="test-model",
            mode="rapid",
            timeout_seconds=30,
            codetalk_revision="test-revision",
            truth_paths=_truth_paths(tmp_path),
        )

    assert invoked is False
    failure = json.loads((output / "generation_failure.json").read_text())
    assert failure["failure_code"] == "candidate_materialization_failed"
    assert failure["truth_inputs"] == []


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="real benchmark Workbench integration currently requires macOS Seatbelt",
)
def test_benchmark_workbench_uses_builtin_managed_codex_non_tty_exec_json(
    tmp_path, monkeypatch
) -> None:
    from app.config import settings
    from app.database import _SCHEMA
    from app.services import quality_benchmark_workbench as workbench_module

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    host_codex_home = tmp_path / "host-codex-home"
    host_codex_home.mkdir()
    auth_canary = f"workbench-auth-{secrets.token_urlsafe(32)}"
    (host_codex_home / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": auth_canary,
                    "account_id": "feature-gate-account",
                    "id_token": f"id-{auth_canary}",
                    "refresh_token": f"refresh-{auth_canary}",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(host_codex_home))
    fake_agent = source / "codex"
    candidate_json = json.dumps(_candidate_response(), ensure_ascii=True)
    fake_agent.write_text(
        "#!/bin/sh\nset -eu\n"
        "if [ \"${1:-}\" = \"--version\" ]; then "
        "printf 'codex-cli 0.test\\n'; exit 0; fi\n"
        "all_args=\" $* \"\n"
        "case \"$all_args\" in *\" exec \"*) ;; *) exit 70 ;; esac\n"
        "case \"$all_args\" in *\" --json \"*) ;; *) exit 71 ;; esac\n"
        "case \"$all_args\" in *\" --skip-git-repo-check \"*) ;; *) exit 72 ;; esac\n"
        "case \"$all_args\" in *\" --ignore-user-config \"*) ;; *) exit 73 ;; esac\n"
        "case \"$all_args\" in *\" --ignore-rules \"*) ;; *) exit 74 ;; esac\n"
        "test ! -t 0\n"
        "test -f \"$CODEX_HOME/auth.json\"\n"
        "test ! -L \"$CODEX_HOME/auth.json\"\n"
        f"grep -Fq '{auth_canary}' \"$CODEX_HOME/auth.json\"\n"
        "grep -Fq 'two' \"$CODETALK_REPO_PATH/storage.c\"\n"
        "schema_path=''\n"
        "output_path=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --output-schema) schema_path=$2; shift 2 ;;\n"
        "    -o|--output-last-message) output_path=$2; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "test \"$schema_path\" = "
        "\"$CODETALK_AGENT_ARTIFACT_DIR/benchmark_output_schema.json\"\n"
        "test \"$output_path\" = "
        "\"$CODETALK_AGENT_ARTIFACT_DIR/benchmark_response.json\"\n"
        "grep -Fq '\"claims\"' \"$schema_path\"\n"
        "cat >/dev/null\n"
        f"printf '%s' '{candidate_json}' >"
        "\"$output_path\"\n"
        "printf 'benchmark artifact ready\\n'\n",
        encoding="utf-8",
    )
    fake_agent.chmod(fake_agent.stat().st_mode | stat.S_IXUSR)
    production_db = tmp_path / "production.db"
    with sqlite3.connect(production_db) as db:
        db.executescript(_SCHEMA)
        db.commit()
    monkeypatch.setattr(settings, "sqlite_db", str(production_db))
    monkeypatch.setenv("PATH", f"{source}{os.pathsep}{os.environ['PATH']}")

    workflow = workbench_module._benchmark_workflow(
        prompt="Analyze the pinned source.",
        output_schema=_generator_output_schema(),
        timeout_seconds=20,
    )
    assert workflow["steps"][0]["provider"] == "agent-runtime:default-codex"

    result = workbench_module.execute_quality_benchmark_workbench(
        case_id="real-workbench-case",
        source_dir=source.resolve(),
        workbench_root=tmp_path / "workbench",
        model="test-model",
        mode="rapid",
        deadline_monotonic=time.monotonic() + 20,
        prompt="Analyze only the pinned source and write benchmark_response.json.",
        output_schema=_generator_output_schema(),
        approved_network_targets=("localhost:9",),
    )

    assert result.response_path.is_file()
    assert json.loads(result.response_path.read_text()) == _candidate_response()
    assert (result.task_artifact_dir / "task_run.json").is_file()
    assert (result.task_artifact_dir / "workflow_execution.json").is_file()
    sandbox_policy = json.loads(
        (
            result.task_artifact_dir
            / "agent_runs"
            / "analyze"
            / "sandbox_policy.json"
        ).read_text()
    )
    assert sandbox_policy["status"] == "active"
    assert sandbox_policy["benchmark_opt_in"] is True
    assert sandbox_policy["codex_home_credentials"] == "isolated_minimal"
    agent_artifact = result.task_artifact_dir / "agent_runs" / "analyze"
    execution_input = json.loads(
        (
            result.task_artifact_dir
            / "agent_runs"
            / "analyze"
            / "execution_input.json"
        ).read_text()
    )
    assert execution_input["prompt_transport"] == "codex_exec_json"
    wrapper_path = Path(execution_input["process_command"][0])
    assert wrapper_path.name == "codex"
    assert wrapper_path.parent.name.startswith(".benchmark-codex-wrapper-")
    assert "agent-runtime:default-codex" not in execution_input["process_command"]
    assert "exec" in execution_input["process_command"]
    assert "--json" in execution_input["process_command"]
    exec_index = execution_input["process_command"].index("exec")
    for flag in (
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
    ):
        assert execution_input["process_command"].index(flag) > exec_index
    invocation = json.loads(
        (agent_artifact / "benchmark_codex_invocation.json").read_text()
    )
    invoked_argv = invocation["argv"]
    output_schema_index = invoked_argv.index("--output-schema")
    output_message_index = invoked_argv.index("--output-last-message")
    assert invoked_argv[output_schema_index + 1] == str(
        agent_artifact / "benchmark_output_schema.json"
    )
    assert invoked_argv[output_message_index + 1] == str(result.response_path)
    assert execution_input["stdin"]["agent_output_contract"]["goal"].startswith(
        "Analyze only the pinned source"
    )
    provider_snapshot = json.loads(
        (result.task_artifact_dir / "provider_snapshot.json").read_text()
    )
    runtime = provider_snapshot["providers"]["agent-runtime:default-codex"]
    assert runtime["status"] == "configured"
    assert Path(runtime["command"][0]).name == "codex"
    assert Path(runtime["command"][0]).parent.name.startswith(
        ".benchmark-codex-wrapper-"
    )
    assert runtime["command"][1:] == [
        "exec",
        "-m",
        "test-model",
        "-c",
        'model_reasoning_effort="low"',
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
    ]
    assert runtime["prompt_transport"] == "codex_exec_json"
    runtime_evidence = json.loads(
        (result.task_artifact_dir / "benchmark_runtime.json").read_text()
    )
    assert runtime_evidence["codex_command"] == str(fake_agent)
    assert runtime_evidence["command"] == runtime["command"][0]
    assert runtime_evidence["cli_version"] == "codex-cli 0.test"
    assert runtime_evidence["model"] == "test-model"
    assert runtime_evidence["mode"] == "rapid"
    assert runtime_evidence["args"] == runtime["command"][1:]
    assert len(runtime_evidence["executable_sha256"]) == 64
    assert len(runtime_evidence["wrapper_sha256"]) == 64
    assert len(runtime_evidence["invocation_sha256"]) == 64
    schema_path = agent_artifact / "benchmark_output_schema.json"
    assert schema_path.is_file()
    assert runtime_evidence["output_schema_sha256"] == hashlib.sha256(
        schema_path.read_bytes()
    ).hexdigest()
    assert str(source.resolve()) in sandbox_policy["read_paths"]
    assert str(source.resolve()) not in sandbox_policy["write_paths"]
    assert str(agent_artifact.resolve()) in sandbox_policy["write_paths"]
    with sqlite3.connect(production_db) as db:
        assert db.execute("SELECT COUNT(*) FROM agent_runtimes").fetchone()[0] == 0
    assert not list((tmp_path / "workbench").glob(".benchmark-runtime-*.sqlite3"))
    assert result.credential_fingerprints
    assert auth_canary not in repr(result.credential_fingerprints)
    assert not any(
        path.name == "auth.json" or path.name.startswith(".runtime-")
        for path in result.task_artifact_dir.rglob("*")
    )
    task_artifacts = b"\n".join(
        path.read_bytes()
        for path in result.task_artifact_dir.rglob("*")
        if path.is_file()
    ).decode("utf-8", errors="replace")
    assert auth_canary not in task_artifacts


def test_benchmark_codex_wrapper_rejects_boundary_escape_and_output_override(
    tmp_path,
) -> None:
    from app.services.quality_benchmark_workbench import (
        _materialize_benchmark_codex_wrapper,
    )

    workbench = tmp_path / "workbench"
    workbench.mkdir()
    marker = tmp_path / "codex-invoked"
    codex = tmp_path / "codex"
    codex.write_text(
        f"#!/bin/sh\nprintf invoked > '{marker}'\n",
        encoding="utf-8",
    )
    codex.chmod(codex.stat().st_mode | stat.S_IXUSR)
    wrapper, wrapper_sha256 = _materialize_benchmark_codex_wrapper(
        workbench_root=workbench,
        codex_command=codex,
    )
    assert hashlib.sha256(wrapper.read_bytes()).hexdigest() == wrapper_sha256

    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = subprocess.run(
        [str(wrapper), "exec"],
        env={**os.environ, "CODETALK_AGENT_ARTIFACT_DIR": str(outside)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert escaped.returncode == 78
    assert not marker.exists()

    artifact = workbench / "task_runs" / "task-1" / "agent_runs" / "analyze"
    artifact.mkdir(parents=True)
    (artifact / "benchmark_output_schema.json").write_text("{}\n", encoding="utf-8")
    overridden = subprocess.run(
        [str(wrapper), "exec", "--output-schema", str(tmp_path / "evil.json")],
        env={**os.environ, "CODETALK_AGENT_ARTIFACT_DIR": str(artifact)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert overridden.returncode == 78
    assert not marker.exists()


def test_generator_deadline_covers_materialization_and_publishes_unified_failure(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
        lambda **_kwargs: _fake_workbench_result(tmp_path),
    )
    original = _materialize_candidate

    def slow_materializer(*args, **kwargs):
        time.sleep(1.05)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.quality_benchmark_generator._materialize_candidate",
        slow_materializer,
    )
    output = tmp_path / "deadline-attempt"

    with pytest.raises(RuntimeError, match="absolute deadline"):
        generate_quality_benchmark_artifacts(
            case_id="deadline-case",
            source_dir=source,
            output_dir=output,
            model="test-model",
            mode="rapid",
            timeout_seconds=1,
            codetalk_revision="test-revision",
            truth_paths=_truth_paths(tmp_path),
        )

    failure = json.loads((output / "generation_failure.json").read_text())
    assert failure["status"] == "timed_out"
    assert failure["failure_code"] == "absolute_deadline_exceeded"
    assert failure["truth_inputs"] == []
    assert not (output / "first_pass").exists()
    assert not (output / "task-artifact").exists()
    for path in [output, *output.rglob("*")]:
        assert path.stat().st_mode & 0o222 == 0


def test_generator_deadline_covers_atomic_publication(tmp_path, monkeypatch) -> None:
    from app.services import quality_benchmark_generator as generator_module

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    monkeypatch.setattr(
        generator_module,
        "execute_quality_benchmark_workbench",
        lambda **_kwargs: _fake_workbench_result(tmp_path),
    )
    original_publish = generator_module._rename_directory_noreplace
    publication_count = 0

    def delayed_first_publish(source_path, destination_path):
        nonlocal publication_count
        publication_count += 1
        if publication_count == 1:
            time.sleep(1.05)
        return original_publish(source_path, destination_path)

    monkeypatch.setattr(
        generator_module,
        "_rename_directory_noreplace",
        delayed_first_publish,
    )
    output = tmp_path / "publication-deadline"

    with pytest.raises(RuntimeError, match="absolute deadline"):
        generate_quality_benchmark_artifacts(
            case_id="publication-deadline-case",
            source_dir=source,
            output_dir=output,
            model="test-model",
            mode="rapid",
            timeout_seconds=1,
            codetalk_revision="test-revision",
            truth_paths=_truth_paths(tmp_path),
        )

    failure = json.loads((output / "generation_failure.json").read_text())
    assert failure["failure_code"] == "absolute_deadline_exceeded"
    assert not (output / "generation_manifest.json").exists()


@pytest.mark.parametrize("stage", ["parse", "materialize", "hash", "publish"])
def test_generator_hard_deadline_terminates_each_postprocess_stage(
    tmp_path, monkeypatch, stage
) -> None:
    from app.services import quality_benchmark_generator as generator_module

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    monkeypatch.setattr(
        generator_module,
        "execute_quality_benchmark_workbench",
        lambda **_kwargs: _fake_workbench_result(tmp_path),
    )

    def attack_stage(observed_stage: str) -> None:
        if observed_stage == stage:
            time.sleep(5)

    monkeypatch.setattr(generator_module, "_postprocess_stage_hook", attack_stage)
    output = tmp_path / f"deadline-{stage}"
    original_children = {child.pid for child in multiprocessing.active_children()}
    started = time.monotonic()

    with pytest.raises(RuntimeError, match="absolute deadline"):
        generate_quality_benchmark_artifacts(
            case_id=f"deadline-{stage}",
            source_dir=source,
            output_dir=output,
            model="test-model",
            mode="rapid",
            timeout_seconds=1,
            codetalk_revision="test-revision",
            truth_paths=_truth_paths(tmp_path),
        )

    assert time.monotonic() - started < 1.3
    assert {child.pid for child in multiprocessing.active_children()} <= original_children
    assert not (output / "generation_manifest.json").exists()
    _verify_artifact_hash_manifest(output)


def test_deadline_before_child_setsid_falls_back_to_pid_termination(
    tmp_path, monkeypatch
) -> None:
    from app.services import quality_benchmark_generator as generator_module

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    monkeypatch.setattr(
        generator_module,
        "execute_quality_benchmark_workbench",
        lambda **_kwargs: _fake_workbench_result(tmp_path),
    )
    child_marker = tmp_path / "pre-setsid-child.pid"

    def block_before_setsid() -> None:
        child_marker.write_text(str(os.getpid()), encoding="ascii")
        while True:
            time.sleep(10)

    monkeypatch.setattr(generator_module.os, "setsid", block_before_setsid)
    output = tmp_path / "pre-setsid-timeout"
    started = time.monotonic()
    child_pid: int | None = None
    remained_active = False
    remained_killable = False
    try:
        with pytest.raises(RuntimeError, match="absolute deadline"):
            generate_quality_benchmark_artifacts(
                case_id="pre-setsid-timeout",
                source_dir=source,
                output_dir=output,
                model="test-model",
                mode="rapid",
                timeout_seconds=1,
                codetalk_revision="test-revision",
                truth_paths=_truth_paths(tmp_path),
            )
        assert child_marker.is_file()
        child_pid = int(child_marker.read_text(encoding="ascii"))
        remained_active = any(
            child.pid == child_pid and child.is_alive()
            for child in multiprocessing.active_children()
        )
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            remained_killable = False
        else:
            remained_killable = True
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for child in multiprocessing.active_children():
            if child.pid == child_pid:
                child.join(0.5)

    assert time.monotonic() - started < 1.4
    assert remained_active is False
    assert remained_killable is False
    _verify_artifact_hash_manifest(output)


def test_generator_fails_closed_on_encoded_credential_material_in_candidate_text(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    secret = f"sk-proj-{secrets.token_urlsafe(32)}"
    adversarial, encoded_variants = _candidate_with_encoded_secret_in_every_string(
        secret
    )
    result = _fake_workbench_result(tmp_path)
    result.response_path.write_text(json.dumps(adversarial), encoding="utf-8")
    monkeypatch.setenv("BENCHMARK_TEST_API_KEY", secret)
    monkeypatch.setattr(
        "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
        lambda **_kwargs: result,
    )
    output = tmp_path / "secret-exfiltration"

    with pytest.raises(RuntimeError, match="immutable failure evidence"):
        generate_quality_benchmark_artifacts(
            case_id="secret-exfiltration",
            source_dir=source,
            output_dir=output,
            model="test-model",
            mode="rapid",
            timeout_seconds=10,
            codetalk_revision="test-revision",
            truth_paths=_truth_paths(tmp_path),
        )

    failure = json.loads((output / "generation_failure.json").read_text())
    assert failure["status"] == "invalid"
    assert failure["failure_code"] == "candidate_secret_material_detected"
    assert not (output / "first_pass").exists()
    published = b"\n".join(
        path.read_bytes() for path in output.rglob("*") if path.is_file()
    ).decode("utf-8")
    assert secret not in published
    assert all(variant not in published for variant in encoded_variants)

    clean_result = _fake_workbench_result(tmp_path / "clean")
    monkeypatch.setattr(
        "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
        lambda **_kwargs: clean_result,
    )
    clean_output = tmp_path / "clean-candidate"
    generate_quality_benchmark_artifacts(
        case_id="clean-candidate",
        source_dir=source,
        output_dir=clean_output,
        model="test-model",
        mode="rapid",
        timeout_seconds=10,
        codetalk_revision="test-revision",
        truth_paths=_truth_paths(tmp_path),
    )
    clean_ledger = json.loads(
        (clean_output / "first_pass" / "claim_ledger.json").read_text()
    )
    assert clean_ledger["claims"][0]["claim"] == "line two is observed"


def test_generator_rejects_isolated_auth_exfil_without_publishing_credential(
    tmp_path, monkeypatch
) -> None:
    from app.services.agent_sandbox import credential_value_fingerprints

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    credential = f"auth-feature-gate-{secrets.token_urlsafe(36)}+/="
    adversarial, encoded_variants = _candidate_with_encoded_secret_in_every_string(
        credential
    )
    result = replace(
        _fake_workbench_result(tmp_path),
        credential_fingerprints=credential_value_fingerprints((credential,)),
    )
    result.response_path.write_text(json.dumps(adversarial), encoding="utf-8")
    monkeypatch.setattr(
        "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
        lambda **_kwargs: result,
    )
    output = tmp_path / "isolated-auth-exfiltration"

    with pytest.raises(RuntimeError, match="immutable failure evidence"):
        generate_quality_benchmark_artifacts(
            case_id="isolated-auth-exfiltration",
            source_dir=source,
            output_dir=output,
            model="test-model",
            mode="rapid",
            timeout_seconds=10,
            codetalk_revision="test-revision",
            truth_paths=_truth_paths(tmp_path),
        )

    failure = json.loads((output / "generation_failure.json").read_text())
    assert failure["status"] == "invalid"
    assert failure["failure_code"] == "candidate_secret_material_detected"
    published = b"\n".join(
        path.read_bytes() for path in output.rglob("*") if path.is_file()
    ).decode("utf-8")
    assert credential not in published
    assert all(variant not in published for variant in encoded_variants)


def test_success_publication_is_invisible_until_every_manifest_is_complete(
    tmp_path, monkeypatch
) -> None:
    from app.services import quality_benchmark_generator as generator_module

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    monkeypatch.setattr(
        generator_module,
        "execute_quality_benchmark_workbench",
        lambda **_kwargs: _fake_workbench_result(tmp_path),
    )
    output = tmp_path / "atomic-visible"
    prepublish = tmp_path / "prepublish.marker"
    observations: list[bool] = []

    def pause_before_publish(stage: str) -> None:
        if stage == "publish":
            prepublish.write_text("ready", encoding="utf-8")
            time.sleep(0.25)

    monkeypatch.setattr(generator_module, "_postprocess_stage_hook", pause_before_publish)

    def observe() -> None:
        limit = time.monotonic() + 5
        while not prepublish.exists() and time.monotonic() < limit:
            time.sleep(0.005)
        observations.append(output.exists())

    observer = threading.Thread(target=observe)
    observer.start()
    generate_quality_benchmark_artifacts(
        case_id="atomic-visible",
        source_dir=source,
        output_dir=output,
        model="test-model",
        mode="rapid",
        timeout_seconds=10,
        codetalk_revision="test-revision",
        truth_paths=_truth_paths(tmp_path),
    )
    observer.join(timeout=2)

    assert observations == [False]
    assert (output / "generation_manifest.json").is_file()
    assert (output / "artifact_hash_manifest.json").is_file()
    _verify_artifact_hash_manifest(output)


def test_hash_manifest_detects_mutation_of_any_published_file(tmp_path, monkeypatch) -> None:
    from app.services import quality_benchmark_generator as generator_module

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    monkeypatch.setattr(
        generator_module,
        "execute_quality_benchmark_workbench",
        lambda **_kwargs: _fake_workbench_result(tmp_path),
    )
    output = tmp_path / "hash-mutation"
    generate_quality_benchmark_artifacts(
        case_id="hash-mutation",
        source_dir=source,
        output_dir=output,
        model="test-model",
        mode="rapid",
        timeout_seconds=10,
        codetalk_revision="test-revision",
        truth_paths=_truth_paths(tmp_path),
    )

    target = output / "versions.json"
    target.chmod(0o600)
    target.write_text('{"mutated":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        _verify_artifact_hash_manifest(output)


def test_generator_fails_closed_when_workbench_failed_with_a_stale_response(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    failed = replace(_fake_workbench_result(tmp_path), status="failed")
    monkeypatch.setattr(
        "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
        lambda **_kwargs: failed,
    )
    output = tmp_path / "failed-workbench"

    with pytest.raises(RuntimeError, match="immutable failure evidence"):
        generate_quality_benchmark_artifacts(
            case_id="failed-workbench-case",
            source_dir=source,
            output_dir=output,
            model="test-model",
            mode="rapid",
            timeout_seconds=30,
            codetalk_revision="test-revision",
            truth_paths=_truth_paths(tmp_path),
        )

    failure = json.loads((output / "generation_failure.json").read_text())
    assert failure["failure_code"] == "workbench_failed"
    assert not (output / "first_pass").exists()


@pytest.mark.parametrize(
    ("workbench_status", "failure_status", "failure_code"),
    [
        ("timed_out", "timed_out", "workbench_timed_out"),
        ("cancelled", "cancelled", "workbench_cancelled"),
        ("quality_blocked", "quality_blocked", "workbench_quality_blocked"),
        ("invalid", "invalid", "workbench_invalid"),
        ("error", "error", "workbench_error"),
    ],
)
def test_generator_preserves_terminal_workbench_status_matrix(
    tmp_path, monkeypatch, workbench_status, failure_status, failure_code
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    result = replace(_fake_workbench_result(tmp_path), status=workbench_status)
    monkeypatch.setattr(
        "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
        lambda **_kwargs: result,
    )
    output = tmp_path / f"status-{workbench_status}"

    with pytest.raises(RuntimeError, match="immutable failure evidence"):
        generate_quality_benchmark_artifacts(
            case_id="status-case",
            source_dir=source,
            output_dir=output,
            model="test-model",
            mode="rapid",
            timeout_seconds=10,
            codetalk_revision="test-revision",
            truth_paths=_truth_paths(tmp_path),
        )

    failure = json.loads((output / "generation_failure.json").read_text())
    assert failure["status"] == failure_status
    assert failure["failure_code"] == failure_code
    _verify_artifact_hash_manifest(output)


def test_quality_blocked_failure_retains_sanitized_workbench_audit(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    result = replace(_fake_workbench_result(tmp_path), status="quality_blocked")
    monkeypatch.setattr(
        "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
        lambda **_kwargs: result,
    )
    output = tmp_path / "blocked-with-audit"

    with pytest.raises(RuntimeError, match="immutable failure evidence"):
        generate_quality_benchmark_artifacts(
            case_id="blocked-audit-case",
            source_dir=source,
            output_dir=output,
            model="test-model",
            mode="rapid",
            timeout_seconds=10,
            codetalk_revision="test-revision",
            truth_paths=_truth_paths(tmp_path),
        )

    audit = json.loads((output / "workbench_audit.json").read_text())
    assert audit["task_run_id"] == result.task_run_id
    assert audit["workbench_status"] == "quality_blocked"
    _verify_artifact_hash_manifest(output)


def test_generator_exports_only_sanitized_workbench_audit_not_runtime_credentials(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    result = _fake_workbench_result(tmp_path)
    runtime_home = (
        result.task_artifact_dir
        / "agent_runs"
        / "analyze"
        / ".runtime-codex-home-secret"
    )
    runtime_home.mkdir()
    (runtime_home / "auth.json").write_text("SUPER_SECRET_TOKEN", encoding="utf-8")
    (runtime_home / "config.toml").write_text(
        'secret = "SUPER_SECRET_CONFIG"\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
        lambda **_kwargs: result,
    )
    output = tmp_path / "sanitized-workbench"

    generate_quality_benchmark_artifacts(
        case_id="sanitized-workbench-case",
        source_dir=source,
        output_dir=output,
        model="test-model",
        mode="rapid",
        timeout_seconds=30,
        codetalk_revision="test-revision",
        truth_paths=_truth_paths(tmp_path),
    )

    names = {path.name for path in output.rglob("*")}
    assert "workbench_task" not in names
    assert not any(name.startswith(".runtime-") for name in names)
    assert "auth.json" not in names
    assert "config.toml" not in names
    published = b"\n".join(
        path.read_bytes() for path in output.rglob("*") if path.is_file()
    )
    assert b"SUPER_SECRET" not in published
    audit = json.loads((output / "workbench_audit.json").read_text())
    assert audit["task_run_id"] == "task_run_test"
    assert audit["workbench_status"] == "completed"
    assert set(audit) == {
        "schema_version",
        "task_run_id",
        "workbench_status",
        "repair_attempt_count",
        "terminal_blocked",
        "task_artifact_hashes",
            "first_provenance",
            "final_provenance",
            "work_sufficiency",
        }


@pytest.mark.parametrize("failure_kind", ["workbench", "invalid_json", "materialize"])
def test_generator_all_terminal_failures_publish_the_same_redacted_evidence_contract(
    tmp_path, monkeypatch, failure_kind
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    result = _fake_workbench_result(tmp_path)
    if failure_kind == "workbench":
        monkeypatch.setattr(
            "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("SECRET /truth/gold_claims.json")),
        )
    else:
        if failure_kind == "invalid_json":
            result.response_path.write_text("not json SECRET", encoding="utf-8")
        monkeypatch.setattr(
            "app.services.quality_benchmark_generator.execute_quality_benchmark_workbench",
            lambda **_kwargs: result,
        )
        if failure_kind == "materialize":
            monkeypatch.setattr(
                "app.services.quality_benchmark_generator._materialize_candidate",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("SECRET truth")),
            )
    output = tmp_path / f"failure-{failure_kind}"

    with pytest.raises(RuntimeError, match="immutable failure evidence"):
        generate_quality_benchmark_artifacts(
            case_id="failure-case",
            source_dir=source,
            output_dir=output,
            model="test-model",
            mode="rapid",
            timeout_seconds=30,
            codetalk_revision="test-revision",
            truth_paths=_truth_paths(tmp_path),
        )

    failure = json.loads((output / "generation_failure.json").read_text())
    assert set(failure) == {
        "schema_version",
        "status",
        "failure_code",
        "case_id",
        "mode",
            "model",
            "codetalk_revision",
            "source_tree",
            "elapsed_seconds",
        "timeout_seconds",
        "truth_inputs",
    }
    assert "SECRET" not in (output / "generation_failure.json").read_text()
    assert "truth" not in failure["failure_code"]
    _verify_artifact_hash_manifest(output)


def test_workbench_first_and_final_use_validated_repair_provenance(
    tmp_path, monkeypatch
) -> None:
    from app.services import quality_benchmark_workbench as workbench_module

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("one\ntwo\n", encoding="utf-8")
    task_artifact = tmp_path / "workbench" / "task_runs" / "task_run_contract"
    agent_artifact = task_artifact / "agent_runs" / "analyze"
    fake_codex = tmp_path / "provenance-codex"
    fake_codex.write_text(
        "#!/bin/sh\ncat >/dev/null\n",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

    @contextmanager
    def fake_runtime_binding(*, workbench_root, model, mode, **_kwargs):
        wrapper, wrapper_sha256 = (
            workbench_module._materialize_benchmark_codex_wrapper(
                workbench_root=workbench_root,
                codex_command=fake_codex,
            )
        )
        yield {
            "schema_version": "quality-benchmark-runtime-v1",
            "runtime_id": "default-codex",
            "provider": "agent-runtime:default-codex",
            "command": str(wrapper),
            "codex_command": str(fake_codex),
            "args": [
                "exec",
                "-m",
                model,
                "-c",
                f'model_reasoning_effort="{"high" if mode == "deep" else "low"}"',
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
            ],
            "prompt_transport": "codex_exec_json",
            "requires_network": True,
            "model": model,
            "mode": mode,
            "model_reasoning_effort": "high" if mode == "deep" else "low",
            "cli_version": "codex-cli provenance-test",
            "executable_sha256": hashlib.sha256(fake_codex.read_bytes()).hexdigest(),
            "wrapper_sha256": wrapper_sha256,
            "bound_at": "provenance-test",
        }

    class FakePreparer:
        def __init__(self, **_kwargs):
            pass

        def prepare(self, **_kwargs):
            agent_artifact.mkdir(parents=True)
            return SimpleNamespace(
                task_run_id="task_run_contract",
                artifact_dir=str(task_artifact),
            )

    class FakeRunner:
        def __init__(self, _root, *, event_sink, is_cancelled):
            self.event_sink = event_sink
            self.is_cancelled = is_cancelled

        def execute_task_run(self, _task_run_id, *, timeout_sec):
            assert timeout_sec > 0
            runtime = json.loads(
                (task_artifact / "benchmark_runtime.json").read_text()
            )
            wrapper_run = subprocess.run(
                [
                    runtime["command"],
                    "exec",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-m",
                    runtime["model"],
                    "-c",
                    f'model_reasoning_effort={runtime["model_reasoning_effort"]}',
                    "--skip-git-repo-check",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--json",
                    "--add-dir",
                    str(agent_artifact),
                    "--cd",
                    str(agent_artifact),
                ],
                cwd=agent_artifact,
                env={
                    **os.environ,
                    "CODETALK_AGENT_ARTIFACT_DIR": str(agent_artifact),
                },
                input="analyze",
                text=True,
                capture_output=True,
                check=False,
            )
            assert wrapper_run.returncode == 0, wrapper_run.stderr
            response = agent_artifact / "benchmark_response.json"
            transient = _candidate_response()
            transient["claims"][0]["claim"] = "transient and unverified"
            response.write_text(json.dumps(transient), encoding="utf-8")
            self.event_sink(
                "artifact_created",
                {
                    "harness_event_kind": "artifact_created",
                    "path": "benchmark_response.json",
                },
            )
            attempt0 = _candidate_response()
            attempt0["claims"][0]["claim"] = "validated attempt zero"
            response.write_text(json.dumps(attempt0), encoding="utf-8")
            repair_dir = agent_artifact / "quality_repairs" / "attempt_1"
            repair_dir.mkdir(parents=True)
            (repair_dir / "quality_audit_before.json").write_text(
                json.dumps({"status": "needs_rework"}), encoding="utf-8"
            )
            self.event_sink(
                "quality_repair_started",
                {"step_id": "analyze", "attempt": 1},
            )
            final = _candidate_response()
            final["claims"][0]["claim"] = "validated repair one"
            response.write_text(json.dumps(final), encoding="utf-8")
            response_sha = hashlib.sha256(response.read_bytes()).hexdigest()
            (task_artifact / "quality_repair_summary.json").write_text(
                json.dumps({"attempt_count": 1, "successful_attempt_count": 1}),
                encoding="utf-8",
            )
            (task_artifact / "workflow_outputs.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "outputs": [
                            {
                                "id": "benchmark_response",
                                "artifact": "benchmark_response.json",
                                "status": "ok",
                                "sha256": response_sha,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(status="completed")

    monkeypatch.setattr(workbench_module, "WorkbenchTaskRunPreparer", FakePreparer)
    monkeypatch.setattr(workbench_module, "WorkbenchWorkflowRunner", FakeRunner)
    monkeypatch.setattr(workbench_module, "WorkflowStore", lambda _path: object())
    monkeypatch.setattr(
        workbench_module,
        "_benchmark_managed_codex_runtime",
        fake_runtime_binding,
    )
    monkeypatch.setattr(
        workbench_module,
        "benchmark_agent_sandbox",
        lambda **_kwargs: nullcontext(),
    )

    result = workbench_module.execute_quality_benchmark_workbench(
        case_id="provenance",
        source_dir=source.resolve(),
        workbench_root=tmp_path / "workbench",
        model="test-model",
        mode="rapid",
        deadline_monotonic=time.monotonic() + 10,
        prompt="analyze",
        output_schema=_generator_output_schema(),
        approved_network_targets=(),
    )

    first = json.loads(result.first_response_path.read_text())
    final = json.loads(result.response_path.read_text())
    assert first["claims"][0]["claim"] == "validated attempt zero"
    assert final["claims"][0]["claim"] == "validated repair one"
    assert result.first_provenance["attempt"] == 0
    assert result.first_provenance["event"] == "quality_repair_started"
    assert result.final_provenance["attempt"] == 1
    assert result.final_provenance["event"] == "workflow_output_validated"


def _materialize_prepublication_controls(task_artifact: Path) -> None:
    from app.services.workbench_artifact_manifest import write_task_artifact_manifest

    outputs = json.loads(
        (task_artifact / "workflow_outputs.json").read_text(encoding="utf-8")
    )
    execution = {"task_run_id": "task-1", **outputs}
    (task_artifact / "workflow_execution.json").write_text(
        json.dumps(execution), encoding="utf-8"
    )
    write_task_artifact_manifest(task_artifact, task_run_id="task-1")


def test_evaluator_prepublication_gate_reuses_repair_then_reaudits(
    tmp_path: Path,
) -> None:
    from app.services.quality_benchmark_workbench import (
        _run_evaluator_owned_prepublication_repair,
    )

    task_artifact = tmp_path / "task"
    agent_artifact = task_artifact / "agent_runs" / "analyze"
    agent_artifact.mkdir(parents=True)
    response_path = agent_artifact / "benchmark_response.json"
    compound = _candidate_response()
    compound["claims"][0]["claim"] = "queue request and resume it"
    response_path.write_text(json.dumps(compound), encoding="utf-8")
    original_bytes = response_path.read_bytes()
    workflow_payload = {
        "task_run_id": "task-1",
        "status": "completed",
        "outputs": [{
            "id": "benchmark_response",
            "artifact": "benchmark_response.json",
            "status": "ok",
            "sha256": hashlib.sha256(original_bytes).hexdigest(),
            "size_bytes": len(original_bytes),
            "preview": original_bytes.decode("utf-8"),
        }],
    }
    (task_artifact / "workflow_outputs.json").write_text(
        json.dumps(workflow_payload), encoding="utf-8"
    )
    _materialize_prepublication_controls(task_artifact)
    gate_calls = []

    def evaluator_gate(path: Path) -> dict:
        candidate = json.loads(path.read_text(encoding="utf-8"))
        gate_calls.append(candidate["claims"][0]["claim"])
        if " and " in candidate["claims"][0]["claim"]:
            return {
                "status": "needs_rework",
                "issues": [{
                    "code": "compound_claim_requires_split",
                    "artifact": "benchmark_response.json",
                    "field": "claims",
                    "row_id": "C1",
                    "operation": "split_candidate_statement",
                    "repairable": True,
                }],
            }
        return {"status": "completed", "issues": []}

    class FakeRunner:
        def __init__(self) -> None:
            self.repair_calls = []

        def _attempt_external_agent_quality_repair(self, **kwargs):
            self.repair_calls.append(kwargs)
            repaired = _candidate_response()
            repaired["claims"][0]["claim"] = "queue request"
            response_path.write_text(json.dumps(repaired), encoding="utf-8")
            return {
                "attempted": True,
                "candidate_ready": True,
                "snapshot": {"benchmark_response.json": original_bytes},
            }

    runner = FakeRunner()
    result = _run_evaluator_owned_prepublication_repair(
        runner=runner,
        task_run=SimpleNamespace(task_run_id="task-1"),
        step_results=[{"step_id": "analyze", "type": "agent_task"}],
        response_path=response_path,
        task_artifact=task_artifact,
        gate=evaluator_gate,
        deadline_monotonic=time.monotonic() + 10,
    )

    assert gate_calls == ["queue request and resume it", "queue request"]
    assert len(runner.repair_calls) == 1
    assert runner.repair_calls[0]["audit"]["issues"][0] == {
        "code": "compound_claim_requires_split",
        "artifact": "benchmark_response.json",
        "field": "claims",
        "row_id": "C1",
        "operation": "split_candidate_statement",
        "repairable": True,
    }
    assert result["attempt_count"] == 1
    assert result["successful_attempt_count"] == 1
    outputs = json.loads((task_artifact / "workflow_outputs.json").read_text())
    final_bytes = response_path.read_bytes()
    final_sha256 = hashlib.sha256(final_bytes).hexdigest()
    assert outputs["outputs"][0]["sha256"] == final_sha256
    assert outputs["outputs"][0]["size_bytes"] == len(final_bytes)
    execution = json.loads((task_artifact / "workflow_execution.json").read_text())
    assert execution["outputs"][0]["sha256"] == final_sha256
    assert execution["outputs"][0]["size_bytes"] == len(final_bytes)
    summary = json.loads((task_artifact / "quality_repair_summary.json").read_text())
    assert summary["successful_attempt_count"] == 1
    manifest = json.loads(
        (task_artifact / "task_artifact_manifest.json").read_text(encoding="utf-8")
    )
    manifest_by_path = {
        item["relative_path"]: item for item in manifest["artifacts"]
    }
    for path in (
        "agent_runs/analyze/benchmark_response.json",
        "workflow_outputs.json",
        "workflow_execution.json",
        "quality_repair_summary.json",
    ):
        artifact_bytes = (task_artifact / path).read_bytes()
        assert manifest_by_path[path]["sha256"] == hashlib.sha256(
            artifact_bytes
        ).hexdigest()
        assert manifest_by_path[path]["size_bytes"] == len(artifact_bytes)


def test_evaluator_prepublication_gate_restores_candidate_when_repair_still_fails(
    tmp_path: Path,
) -> None:
    from app.services.quality_benchmark_workbench import (
        BenchmarkWorkbenchError,
        _run_evaluator_owned_prepublication_repair,
    )

    task_artifact = tmp_path / "task"
    response_path = task_artifact / "agent_runs" / "analyze" / "benchmark_response.json"
    response_path.parent.mkdir(parents=True)
    response_path.write_text('{"claims":[{"claim":"a and b"}]}', encoding="utf-8")
    original_bytes = response_path.read_bytes()
    (task_artifact / "workflow_outputs.json").write_text(
        '{"status":"completed","outputs":[]}', encoding="utf-8"
    )
    _materialize_prepublication_controls(task_artifact)

    class FakeRunner:
        def _attempt_external_agent_quality_repair(self, **_kwargs):
            response_path.write_text('{"claims":[{"claim":"still a and b"}]}', encoding="utf-8")
            return {
                "attempted": True,
                "candidate_ready": True,
                "snapshot": {"benchmark_response.json": original_bytes},
            }

    gate = lambda _path: {
        "status": "needs_rework",
        "issues": [{
            "code": "compound_claim_requires_split",
            "artifact": "benchmark_response.json",
            "field": "claims",
            "row_id": "C1",
            "operation": "split_candidate_statement",
            "repairable": True,
        }],
    }
    with pytest.raises(BenchmarkWorkbenchError, match="did not clear"):
        _run_evaluator_owned_prepublication_repair(
            runner=FakeRunner(),
            task_run=SimpleNamespace(task_run_id="task-1"),
            step_results=[],
            response_path=response_path,
            task_artifact=task_artifact,
            gate=gate,
            deadline_monotonic=time.monotonic() + 10,
        )

    assert response_path.read_bytes() == original_bytes


def test_evaluator_prepublication_gate_accumulates_existing_repair_attempt(
    tmp_path: Path,
) -> None:
    from app.services.quality_benchmark_workbench import (
        _run_evaluator_owned_prepublication_repair,
    )

    task_artifact = tmp_path / "task"
    response_path = task_artifact / "agent_runs" / "analyze" / "benchmark_response.json"
    response_path.parent.mkdir(parents=True)
    response_path.write_text('{"claims":[{"claim":"a and b"}]}', encoding="utf-8")
    original = response_path.read_bytes()
    (task_artifact / "workflow_outputs.json").write_text(
        json.dumps({
            "status": "completed",
            "outputs": [{
                "id": "benchmark_response",
                "artifact": "benchmark_response.json",
                "status": "ok",
                "sha256": hashlib.sha256(original).hexdigest(),
            }],
        }), encoding="utf-8"
    )
    (task_artifact / "quality_repair_summary.json").write_text(
        '{"attempt_count":1,"successful_attempt_count":1}', encoding="utf-8"
    )
    _materialize_prepublication_controls(task_artifact)
    calls = []

    class FakeRunner:
        def _attempt_external_agent_quality_repair(self, **kwargs):
            calls.append(kwargs)
            response_path.write_text('{"claims":[{"claim":"a"}]}', encoding="utf-8")
            return {"attempted": True, "candidate_ready": True}

    def gate(path: Path) -> dict:
        compound = " and " in path.read_text(encoding="utf-8")
        return {
            "status": "needs_rework" if compound else "completed",
            "issues": ([{
                "code": "compound_claim_requires_split",
                "artifact": "benchmark_response.json",
                "field": "claims",
                "row_id": "C1",
                "operation": "split_candidate_statement",
                "repairable": True,
            }] if compound else []),
        }

    result = _run_evaluator_owned_prepublication_repair(
        runner=FakeRunner(),
        task_run=SimpleNamespace(task_run_id="task-1"),
        step_results=[],
        response_path=response_path,
        task_artifact=task_artifact,
        gate=gate,
        deadline_monotonic=time.monotonic() + 10,
    )

    assert calls[0]["attempt_number"] == 2
    assert result["attempt_count"] == 2
    assert result["successful_attempt_count"] == 2


def test_evaluator_prepublication_gate_accumulates_audit_only_existing_repair(
    tmp_path: Path,
) -> None:
    from app.services.quality_benchmark_workbench import (
        _run_evaluator_owned_prepublication_repair,
    )

    task_artifact = tmp_path / "task"
    response_path = task_artifact / "agent_runs" / "analyze" / "benchmark_response.json"
    response_path.parent.mkdir(parents=True)
    response_path.write_text('{"claims":[{"claim":"a and b"}]}', encoding="utf-8")
    original = response_path.read_bytes()
    (task_artifact / "workflow_outputs.json").write_text(
        json.dumps({
            "status": "completed",
            "outputs": [{
                "id": "benchmark_response",
                "artifact": "benchmark_response.json",
                "status": "ok",
                "sha256": hashlib.sha256(original).hexdigest(),
            }],
        }), encoding="utf-8"
    )
    (task_artifact / "test_activity_quality_audit.json").write_text(
        json.dumps({
            "status": "completed",
            "external_agent_quality_repair": {
                "attempted": True,
                "accepted": True,
            },
        }),
        encoding="utf-8",
    )
    _materialize_prepublication_controls(task_artifact)
    calls = []

    class FakeRunner:
        def _attempt_external_agent_quality_repair(self, **kwargs):
            calls.append(kwargs)
            response_path.write_text('{"claims":[{"claim":"a"}]}', encoding="utf-8")
            return {"attempted": True, "candidate_ready": True}

    def gate(path: Path) -> dict:
        compound = " and " in path.read_text(encoding="utf-8")
        return {
            "status": "needs_rework" if compound else "completed",
            "issues": ([{
                "code": "compound_claim_requires_split",
                "artifact": "benchmark_response.json",
                "field": "claims",
                "row_id": "C1",
                "operation": "split_candidate_statement",
                "repairable": True,
            }] if compound else []),
        }

    result = _run_evaluator_owned_prepublication_repair(
        runner=FakeRunner(),
        task_run=SimpleNamespace(task_run_id="task-1"),
        step_results=[],
        response_path=response_path,
        task_artifact=task_artifact,
        gate=gate,
        deadline_monotonic=time.monotonic() + 10,
    )

    assert calls[0]["attempt_number"] == 2
    assert result["attempt_count"] == 2
    assert result["successful_attempt_count"] == 2


def test_evaluator_prepublication_gate_rolls_back_when_second_gate_raises(
    tmp_path: Path,
) -> None:
    from app.services.quality_benchmark_workbench import (
        BenchmarkWorkbenchQualityBlocked,
        _run_evaluator_owned_prepublication_repair,
    )

    task_artifact = tmp_path / "task"
    response_path = task_artifact / "agent_runs" / "analyze" / "benchmark_response.json"
    response_path.parent.mkdir(parents=True)
    response_path.write_text('{"claims":[{"claim":"a and b"}]}', encoding="utf-8")
    original_response = response_path.read_bytes()
    outputs_path = task_artifact / "workflow_outputs.json"
    outputs_path.write_text('{"status":"completed","outputs":[]}', encoding="utf-8")
    _materialize_prepublication_controls(task_artifact)
    original_outputs = outputs_path.read_bytes()
    execution_path = task_artifact / "workflow_execution.json"
    original_execution = execution_path.read_bytes()
    manifest_path = task_artifact / "task_artifact_manifest.json"
    original_manifest = manifest_path.read_bytes()
    calls = 0

    class FakeRunner:
        def _attempt_external_agent_quality_repair(self, **_kwargs):
            response_path.write_text('{"claims":[{"claim":"a"}]}', encoding="utf-8")
            return {"attempted": True, "candidate_ready": True}

    def gate(_path: Path) -> dict:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("judge unavailable")
        return {
            "status": "needs_rework",
            "issues": [{
                "code": "compound_claim_requires_split",
                "artifact": "benchmark_response.json",
                "field": "claims",
                "row_id": "C1",
                "operation": "split_candidate_statement",
                "repairable": True,
            }],
        }

    with pytest.raises(
        BenchmarkWorkbenchQualityBlocked, match="failed and was rolled back"
    ):
        _run_evaluator_owned_prepublication_repair(
            runner=FakeRunner(),
            task_run=SimpleNamespace(task_run_id="task-1"),
            step_results=[],
            response_path=response_path,
            task_artifact=task_artifact,
            gate=gate,
            deadline_monotonic=time.monotonic() + 10,
        )

    assert response_path.read_bytes() == original_response
    assert outputs_path.read_bytes() == original_outputs
    assert execution_path.read_bytes() == original_execution
    assert manifest_path.read_bytes() == original_manifest
    assert not (task_artifact / "quality_repair_summary.json").exists()


def test_evaluator_prepublication_gate_rolls_back_when_manifest_refresh_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.quality_benchmark_workbench as workbench_module

    task_artifact = tmp_path / "task"
    response_path = task_artifact / "agent_runs" / "analyze" / "benchmark_response.json"
    response_path.parent.mkdir(parents=True)
    response_path.write_text('{"claims":[{"claim":"a and b"}]}', encoding="utf-8")
    response_before = response_path.read_bytes()
    outputs_path = task_artifact / "workflow_outputs.json"
    outputs_path.write_text(
        json.dumps({
            "status": "completed",
            "outputs": [{
                "id": "benchmark_response",
                "artifact": "benchmark_response.json",
                "status": "ok",
                "sha256": hashlib.sha256(response_before).hexdigest(),
            }],
        }),
        encoding="utf-8",
    )
    _materialize_prepublication_controls(task_artifact)
    execution_path = task_artifact / "workflow_execution.json"
    manifest_path = task_artifact / "task_artifact_manifest.json"
    controls_before = {
        path: path.read_bytes()
        for path in (outputs_path, execution_path, manifest_path)
    }

    class FakeRunner:
        def _attempt_external_agent_quality_repair(self, **_kwargs):
            response_path.write_text('{"claims":[{"claim":"a"}]}', encoding="utf-8")
            return {"attempted": True, "candidate_ready": True}

    def gate(path: Path) -> dict:
        compound = " and " in path.read_text(encoding="utf-8")
        return {
            "status": "needs_rework" if compound else "completed",
            "issues": ([{
                "code": "compound_claim_requires_split",
                "artifact": "benchmark_response.json",
                "field": "claims",
                "row_id": "C1",
                "operation": "split_candidate_statement",
                "repairable": True,
            }] if compound else []),
        }

    monkeypatch.setattr(
        workbench_module,
        "write_task_artifact_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(
        workbench_module.BenchmarkWorkbenchQualityBlocked,
        match="failed and was rolled back",
    ):
        workbench_module._run_evaluator_owned_prepublication_repair(
            runner=FakeRunner(),
            task_run=SimpleNamespace(task_run_id="task-1"),
            step_results=[],
            response_path=response_path,
            task_artifact=task_artifact,
            gate=gate,
            deadline_monotonic=time.monotonic() + 10,
        )

    assert response_path.read_bytes() == response_before
    for path, original in controls_before.items():
        assert path.read_bytes() == original
    assert not (task_artifact / "quality_repair_summary.json").exists()


def test_evaluator_repair_exhaustion_is_classified_as_quality_blocked() -> None:
    from app.services.quality_benchmark_generator import _failure_classification
    from app.services.quality_benchmark_workbench import (
        BenchmarkWorkbenchQualityBlocked,
    )

    assert _failure_classification(
        BenchmarkWorkbenchQualityBlocked("compound repair exhausted")
    ) == ("evaluator_repair_exhausted", "quality_blocked")
