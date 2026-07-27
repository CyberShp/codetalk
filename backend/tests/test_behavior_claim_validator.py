import json
import asyncio
from pathlib import Path

import pytest

from app.services.agent_cli_bridge import AGENT_FINAL_ANSWER_PREFIX
from app.llm.base import LLMResponse


def _request():
    return {
        "kind": "behavior_claim_validation_request",
        "schema_version": 2,
        "request_sha256": "request-123",
        "repo_path": "/repo",
        "claims": [
            {
                "claim_id": "ROW:sfmea.json:SFMEA-001",
                "type": "sfmea_row_behavior",
                "artifact": "sfmea.json",
                "row_id": "SFMEA-001",
                "statement": "source rejects NULL input",
                "binding": "binding-1",
                "context_ids": ["CTX-001"],
            },
            {
                "claim_id": "ROW:black_box_cases.json:TC-001",
                "type": "black_box_case_behavior",
                "artifact": "black_box_cases.json",
                "row_id": "TC-001",
                "statement": "wire status is observable",
                "binding": "binding-2",
                "context_ids": ["CTX-001"],
            },
        ],
        "contexts": [
            {
                "context_id": "CTX-001",
                "path": "lib/iscsi/iscsi.c",
                "start_line": 1,
                "end_line": 4,
                "sha256": "source-sha",
                "content": "000003: if (params == NULL) return -1;",
            }
        ],
    }


def test_behavior_claim_audit_readiness_blocks_codex_when_active_model_is_missing(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.behavior_claim_validator import build_behavior_claim_audit_readiness

    database = tmp_path / "settings.db"
    connection = __import__("sqlite3").connect(database)
    connection.executescript(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE llm_configs (id TEXT PRIMARY KEY, is_chat_model INTEGER);"
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(settings, "sqlite_db", database)
    monkeypatch.setattr(settings, "behavior_claim_audit_enabled", True)
    monkeypatch.setattr(settings, "behavior_claim_audit_runtime_id", "auto")

    readiness = build_behavior_claim_audit_readiness(
        required=True,
        generator_identities=["agent-runtime:default-codex"],
    )

    assert readiness["status"] == "blocked"
    assert readiness["mode"] == "missing_active_chat_model"
    assert "活跃聊天模型" in readiness["message"]


def test_behavior_claim_audit_readiness_accepts_explicit_audit_model(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.behavior_claim_validator import build_behavior_claim_audit_readiness

    database = tmp_path / "settings.db"
    connection = __import__("sqlite3").connect(database)
    connection.executescript(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE llm_configs (id TEXT PRIMARY KEY, is_chat_model INTEGER);"
        "INSERT INTO settings VALUES ('behavior_claim_audit_model_id', 'audit-1');"
        "INSERT INTO llm_configs VALUES ('audit-1', 1);"
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(settings, "sqlite_db", database)
    monkeypatch.setattr(settings, "behavior_claim_audit_enabled", True)

    readiness = build_behavior_claim_audit_readiness(
        required=True,
        generator_identities=["agent-runtime:default-codex"],
    )

    assert readiness["status"] == "ready"
    assert readiness["mode"] == "configured_independent_model"


@pytest.mark.asyncio
async def test_materialize_routes_codex_generation_to_active_builtin_llm(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.behavior_claim_validator import materialize_behavior_claim_validation

    monkeypatch.setattr(settings, "behavior_claim_audit_enabled", True)
    monkeypatch.setattr(settings, "behavior_claim_audit_runtime_id", "auto")
    calls = []

    class FakeLLM:
        async def complete_once(self, messages, max_tokens, temperature):
            calls.append(
                {
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
            )
            request = json.loads(
                messages[0]["content"].split("VALIDATION_REQUEST:\n", 1)[1]
            )
            return LLMResponse(
                content=json.dumps(
                    {
                        "claims": [
                            {
                                "claim_id": claim["claim_id"],
                                "binding": claim["binding"],
                                "status": "supports",
                                "reason": "由独立内置模型依据源码片段核验",
                            }
                            for claim in request["claims"]
                        ]
                    }
                ),
                model="deepseek-chat",
                usage={},
                finish_reason="stop",
            )

        async def close(self):
            calls.append({"closed": True})

    result = await materialize_behavior_claim_validation(
        artifact_dir=tmp_path / "artifacts",
        repo_path=tmp_path,
        generator_identity="agent-runtime:default-codex",
        request=_request(),
        runtime_loader=lambda _runtime_id: {
            "id": "default-codex",
            "provider": "codex",
            "enabled": True,
        },
        llm_factory=lambda: FakeLLM(),
    )

    assert result["status"] == "completed"
    assert result["validator"]["provider"] == "builtin-llm"
    assert result["validator"]["model"] == "deepseek-chat"
    assert result["validator"]["independent"] is True
    assert [item["status"] for item in result["claims"]] == [
        "supports",
        "supports",
    ]
    assert calls[-1] == {"closed": True}


@pytest.mark.asyncio
async def test_materialize_routes_builtin_generation_to_configured_independent_audit_model(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.behavior_claim_validator import materialize_behavior_claim_validation

    monkeypatch.setattr(settings, "behavior_claim_audit_enabled", True)
    calls = []

    class FakeLLM:
        async def complete_once(self, messages, max_tokens, temperature):
            calls.append({"max_tokens": max_tokens, "temperature": temperature})
            request = json.loads(
                messages[0]["content"].split("VALIDATION_REQUEST:\n", 1)[1]
            )
            return LLMResponse(
                content=json.dumps({"claims": [
                    {
                        "claim_id": claim["claim_id"],
                        "binding": claim["binding"],
                        "status": "supports",
                        "reason": "独立模型核验通过",
                    }
                    for claim in request["claims"]
                ]}),
                model="deepseek-reasoner",
                usage={},
                finish_reason="stop",
            )

        async def close(self):
            calls.append({"closed": True})

    async def configured_audit_loader():
        return FakeLLM(), "audit-config-id", "deepseek-reasoner"

    result = await materialize_behavior_claim_validation(
        artifact_dir=tmp_path / "artifacts",
        repo_path=tmp_path,
        generator_identity="builtin-llm:deepseek-chat",
        request=_request(),
        builtin_audit_loader=configured_audit_loader,
    )

    assert result["status"] == "completed"
    assert result["validator"]["runtime_id"] == "llm-config:audit-config-id"
    assert result["validator"]["model"] == "deepseek-reasoner"
    assert result["validator"]["independent"] is True
    assert calls[-1] == {"closed": True}


@pytest.mark.asyncio
async def test_materialize_behavior_claim_validation_emits_heartbeats_while_audit_runs(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.behavior_claim_validator import materialize_behavior_claim_validation

    monkeypatch.setattr(settings, "behavior_claim_audit_enabled", True)
    monkeypatch.setattr(settings, "behavior_claim_audit_heartbeat_seconds", 0.01)
    progress = []

    async def slow_streamer(**kwargs):
        await asyncio.sleep(0.04)
        request = json.loads(kwargs["prompt"].split("VALIDATION_REQUEST:\n", 1)[1])
        yield AGENT_FINAL_ANSWER_PREFIX + json.dumps(
            {
                "claims": [
                    {
                        "claim_id": claim["claim_id"],
                        "binding": claim["binding"],
                        "status": "supports",
                        "reason": "verified",
                    }
                    for claim in request["claims"]
                ]
            }
        )

    runtime = {
        "id": "default-codex",
        "provider": "codex",
        "command": "codex",
        "args": [],
        "resume_args": [],
        "env": {},
        "enabled": True,
    }
    result = await materialize_behavior_claim_validation(
        artifact_dir=tmp_path / "artifacts",
        repo_path=tmp_path,
        generator_identity="deepseek-chat",
        request=_request(),
        runtime_loader=lambda runtime_id: runtime,
        streamer=slow_streamer,
        on_progress=progress.append,
    )

    assert result["status"] == "completed"
    heartbeats = [item for item in progress if item.get("kind") == "stage_heartbeat"]
    assert heartbeats
    assert heartbeats[0]["stage_id"] == "behavior_claim_validation"
    assert heartbeats[0]["pending_batch_count"] == 1
    assert "事实核验仍在进行" in heartbeats[0]["user_message"]


def test_normalize_behavior_claim_verdicts_binds_current_request_and_fills_omissions():
    from app.services.behavior_claim_validator import (
        build_behavior_claim_audit_prompt,
        normalize_behavior_claim_verdicts,
    )

    raw = """```json
    {"claims":[{"claim_id":"ROW:sfmea.json:SFMEA-001","status":"supports","reason":"guard returns"}]}
    ```"""

    result = normalize_behavior_claim_verdicts(
        raw_output=raw,
        request=_request(),
        validator={"provider": "codex", "model": "gpt-5.6-sol", "independent": True},
    )

    assert result["status"] == "completed"
    assert result["candidate_count"] == 2
    assert result["requested_count"] == 2
    assert result["truncated"] is False
    assert result["claims"][0] == {
        "claim_id": "ROW:sfmea.json:SFMEA-001",
        "binding": "binding-1",
        "status": "supports",
        "reason": "guard returns",
        "field_patch": {},
        "context_ids": ["CTX-001"],
    }
    assert result["claims"][1]["status"] == "insufficient"
    assert "未返回" in result["claims"][1]["reason"]

    with pytest.raises(ValueError, match="没有返回任何 claim"):
        normalize_behavior_claim_verdicts(
            raw_output='{"detail":"provider rejected the model"}',
            request=_request(),
            validator={"provider": "codex", "independent": True},
        )

    prompt = build_behavior_claim_audit_prompt(_request())
    assert "mitigation 是建议动作" in prompt
    assert "test_mapping 是候选落点" in prompt
    assert "不要要求建议动作已经存在于源码" in prompt
    assert "准确的局部源码事实" in prompt
    assert "日志文字同样允许" in prompt
    assert "evidence_bindings" in prompt
    assert "遗漏的关键条件会反转陈述含义" in prompt
    assert "命令行选项的真实语义" in prompt
    assert "oracle_basis" in prompt
    assert "正常保护行为或测试覆盖缺口" in prompt
    assert "field_patch" in prompt


def test_normalize_behavior_claim_verdicts_recovers_self_contradictory_support_reason():
    from app.services.behavior_claim_validator import normalize_behavior_claim_verdicts

    raw = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "ROW:sfmea.json:SFMEA-001",
                    "binding": "binding-1",
                    "status": "contradicts",
                    "reason": "The claim is fully supported.",
                    "field_patch": {},
                }
            ]
        }
    )

    result = normalize_behavior_claim_verdicts(
        raw_output=raw,
        request=_request(),
        validator={"provider": "codex", "independent": True},
    )

    assert result["claims"][0]["status"] == "supports"
    assert "自相矛盾" in result["claims"][0]["reason"]


def test_normalize_behavior_claim_verdicts_recovers_explicit_final_status_correction():
    from app.services.behavior_claim_validator import normalize_behavior_claim_verdicts

    raw = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "ROW:sfmea.json:SFMEA-001",
                    "binding": "binding-1",
                    "status": "contradicts",
                    "reason": (
                        "The source supports the claim. "
                        "Re-evaluating: Changing to supports."
                    ),
                    "field_patch": {},
                }
            ]
        }
    )

    result = normalize_behavior_claim_verdicts(
        raw_output=raw,
        request=_request(),
        validator={"provider": "codex", "independent": True},
    )

    assert result["claims"][0]["status"] == "supports"
    assert "自相矛盾" in result["claims"][0]["reason"]


def test_normalize_behavior_claim_verdicts_keeps_only_allowed_field_patch_keys():
    from app.services.behavior_claim_validator import normalize_behavior_claim_verdicts

    raw = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "ROW:sfmea.json:SFMEA-001",
                    "binding": "binding-1",
                    "status": "contradicts",
                    "reason": "detection claims a log that does not exist",
                    "field_patch": {
                        "detection": "通过登录响应状态与连接状态观测，不声称存在该日志。",
                        "sfmea_id": "MUST-NOT-CHANGE",
                        "technical_claims": [],
                    },
                },
                {
                    "claim_id": "ROW:black_box_cases.json:TC-001",
                    "binding": "binding-2",
                    "status": "supports",
                    "reason": "fully supported",
                    "field_patch": {"expected_result": "must be ignored"},
                },
            ]
        }
    )

    result = normalize_behavior_claim_verdicts(
        raw_output=raw,
        request=_request(),
        validator={"provider": "codex", "independent": True},
    )

    assert result["claims"][0]["field_patch"] == {
        "detection": "通过登录响应状态与连接状态观测，不声称存在该日志。"
    }
    assert result["claims"][1].get("field_patch") == {}


def test_black_box_behavior_patch_keeps_oracle_basis_correction():
    from app.services.behavior_claim_validator import normalize_behavior_claim_verdicts

    request = {
        "claims": [
            {
                "claim_id": "ROW:black_box_cases.json:BB-11",
                "binding": "binding-bb-11",
                "type": "black_box_case_behavior",
            }
        ]
    }
    raw = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "ROW:black_box_cases.json:BB-11",
                    "binding": "binding-bb-11",
                    "status": "contradicts",
                    "reason": "the current oracle cites an unrelated unit test",
                    "field_patch": {
                        "oracle_basis": (
                            "Use observed keyring/sysfs before-after state; "
                            "do not claim cleanup without source or measured evidence."
                        ),
                        "technical_claims": [],
                    },
                }
            ]
        }
    )

    result = normalize_behavior_claim_verdicts(
        raw_output=raw,
        request=request,
        validator={"provider": "codex", "independent": True},
    )

    assert result["claims"][0]["field_patch"] == {
        "oracle_basis": (
            "Use observed keyring/sysfs before-after state; "
            "do not claim cleanup without source or measured evidence."
        )
    }


def test_failed_black_box_verdict_gets_a_neutral_oracle_fallback():
    from app.services.behavior_claim_validator import normalize_behavior_claim_verdicts

    request = {
        "claims": [
            {
                "claim_id": "ROW:black_box_cases.json:BB-12",
                "binding": "binding-bb-12",
                "type": "black_box_case_behavior",
                "statement": json.dumps(
                    {
                        "oracle_basis": "unrelated source test proves cleanup",
                        "expected_result": "all resources are cleaned",
                    }
                ),
            }
        ]
    }
    raw = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "ROW:black_box_cases.json:BB-12",
                    "binding": "binding-bb-12",
                    "status": "contradicts",
                    "reason": "the oracle is unrelated to cleanup",
                    "field_patch": {},
                }
            ]
        }
    )

    result = normalize_behavior_claim_verdicts(
        raw_output=raw,
        request=request,
        validator={"provider": "codex", "independent": True},
    )

    oracle = result["claims"][0]["field_patch"]["oracle_basis"]
    assert "运行前登记" in oracle
    assert "不预设" in oracle


def test_normalize_behavior_claim_verdicts_keeps_duplicate_ids_bound_to_each_request():
    from app.services.behavior_claim_validator import normalize_behavior_claim_verdicts

    request = _request()
    request["claims"] = [
        {
            "claim_id": "TC-04",
            "artifact": "sfmea.json",
            "row_id": "SFMEA-04",
            "binding": "sfmea-binding",
            "context_ids": ["CTX-001"],
        },
        {
            "claim_id": "TC-04",
            "artifact": "black_box_cases.json",
            "row_id": "BB-04",
            "binding": "blackbox-binding",
            "context_ids": ["CTX-001"],
        },
    ]
    raw = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "TC-04",
                    "binding": "sfmea-binding",
                    "status": "insufficient",
                    "reason": "SFMEA evidence is incomplete",
                },
                {
                    "claim_id": "TC-04",
                    "binding": "blackbox-binding",
                    "status": "supports",
                    "reason": "black-box behavior is supported",
                },
            ]
        }
    )

    result = normalize_behavior_claim_verdicts(
        raw_output=raw,
        request=request,
        validator={"provider": "codex", "independent": True},
    )

    assert [item["binding"] for item in result["claims"]] == [
        "sfmea-binding",
        "blackbox-binding",
    ]
    assert [item["status"] for item in result["claims"]] == [
        "insufficient",
        "supports",
    ]


def test_partition_behavior_claim_request_limits_claims_and_only_carries_used_contexts():
    from app.services.behavior_claim_validator import partition_behavior_claim_request

    request = _request()
    request["claims"] = [
        *request["claims"],
        {
            "claim_id": "C-003",
            "type": "source_behavior",
            "statement": "third claim",
            "binding": "binding-3",
            "context_ids": ["CTX-002"],
        },
    ]
    request["contexts"].append(
        {
            "context_id": "CTX-002",
            "path": "lib/iscsi/conn.c",
            "content": "return 0;",
        }
    )

    batches = partition_behavior_claim_request(request, batch_size=2)

    assert [[claim["claim_id"] for claim in batch["claims"]] for batch in batches] == [
        ["ROW:sfmea.json:SFMEA-001", "ROW:black_box_cases.json:TC-001"],
        ["C-003"],
    ]
    assert [item["context_id"] for item in batches[0]["contexts"]] == ["CTX-001"]
    assert [item["context_id"] for item in batches[1]["contexts"]] == ["CTX-002"]


def test_old_behavior_validation_schema_is_not_reused_without_field_patches():
    from app.services.behavior_claim_validator import _reusable_bound_verdicts

    reusable = _reusable_bound_verdicts(
        existing={
            "schema_version": 1,
            "status": "completed",
            "validator": {
                "provider": "codex",
                "runtime_id": "default-codex",
                "model": "gpt-5.5",
                "reasoning_effort": "medium",
                "independent": True,
            },
            "claims": [
                {
                    "claim_id": "ROW:sfmea.json:SFMEA-001",
                    "binding": "binding-1",
                    "status": "contradicts",
                    "reason": "old natural-language-only verdict",
                }
            ],
        },
        validator={
            "provider": "codex",
            "runtime_id": "default-codex",
            "model": "gpt-5.5",
            "reasoning_effort": "medium",
            "independent": True,
        },
    )

    assert reusable == {}


def test_reset_behavior_claim_audit_diagnostics_removes_stale_active_batches(tmp_path):
    from app.services.behavior_claim_validator import (
        reset_behavior_claim_audit_diagnostics,
    )

    diagnostics = tmp_path / "behavior_claim_audit"
    (diagnostics / "batch_01").mkdir(parents=True)
    (diagnostics / "batch_01" / "raw_output.txt").write_text("old", encoding="utf-8")
    (diagnostics / "raw_output.txt").write_text("old aggregate", encoding="utf-8")
    (diagnostics / "keep.txt").write_text("operator note", encoding="utf-8")

    reset_behavior_claim_audit_diagnostics(diagnostics)

    assert not (diagnostics / "batch_01").exists()
    assert not (diagnostics / "raw_output.txt").exists()
    assert (diagnostics / "keep.txt").read_text(encoding="utf-8") == "operator note"


@pytest.mark.asyncio
async def test_materialize_behavior_claim_validation_uses_independent_runtime_and_cache(
    tmp_path, monkeypatch
):
    from app.services.behavior_claim_validator import materialize_behavior_claim_validation
    from app.config import settings

    monkeypatch.setattr(settings, "behavior_claim_audit_enabled", True)

    calls = []
    progress = []

    async def streamer(**kwargs):
        calls.append(kwargs)
        runtime_dir = Path(
            kwargs["runtime"]["env"]["CODETALK_AGENT_ARTIFACT_DIR"]
        )
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "transient-runtime-state.bin").write_bytes(b"runtime")
        yield AGENT_FINAL_ANSWER_PREFIX + json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "ROW:sfmea.json:SFMEA-001",
                        "status": "supports",
                        "reason": "guard returns before dereference",
                    },
                    {
                        "claim_id": "ROW:black_box_cases.json:TC-001",
                        "status": "contradicts",
                        "reason": "the expected status conflicts with source",
                    },
                ]
            }
        )

    runtime = {
        "id": "default-codex",
        "name": "Codex",
        "provider": "codex",
        "command": "codex",
        "args": [],
        "resume_args": [],
        "env": {},
        "prompt_transport": "codex_exec_json",
        "enabled": True,
    }
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    result = await materialize_behavior_claim_validation(
        artifact_dir=artifact_dir,
        repo_path=tmp_path,
        generator_identity="deepseek-reasoner",
        request=_request(),
        runtime_loader=lambda runtime_id: runtime,
        streamer=streamer,
        on_progress=progress.append,
    )

    assert result["validator"]["independent"] is True
    assert [item["status"] for item in result["claims"]] == [
        "supports",
        "contradicts",
    ]
    assert calls[0]["cwd"] == str(tmp_path)
    assert "质量分数" not in calls[0]["prompt"]
    assert (artifact_dir / "behavior_claim_validation.json").is_file()
    assert not list((artifact_dir / "behavior_claim_audit").glob("batch_*/runtime"))
    assert list((artifact_dir / "behavior_claim_audit").glob("batch_*/raw_output.txt"))
    assert progress[0] == {
        "kind": "stage_provider_started",
        "stage_id": "behavior_claim_validation",
        "status": "running",
        "claim_count": 2,
        "model": "gpt-5.5",
        "user_message": "正在使用独立审计器核验 2 条源码事实",
    }
    assert progress[-1]["kind"] == "stage_completed"
    assert progress[-1]["stage_id"] == "behavior_claim_validation"
    assert progress[-1]["status"] == "completed"

    async def should_not_run(**kwargs):
        raise AssertionError("cache should avoid a second agent invocation")
        yield ""

    reused = await materialize_behavior_claim_validation(
        artifact_dir=artifact_dir,
        repo_path=tmp_path,
        generator_identity="deepseek-reasoner",
        request=_request(),
        runtime_loader=lambda runtime_id: runtime,
        streamer=should_not_run,
    )
    assert reused["reused"] is True


@pytest.mark.asyncio
async def test_materialize_behavior_claim_validation_reuses_unchanged_bound_verdicts(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.behavior_claim_validator import materialize_behavior_claim_validation

    monkeypatch.setattr(settings, "behavior_claim_audit_enabled", True)
    monkeypatch.setattr(settings, "behavior_claim_audit_batch_size", 8)
    runtime = {
        "id": "default-codex",
        "provider": "codex",
        "command": "codex",
        "args": [],
        "resume_args": [],
        "env": {},
        "enabled": True,
    }
    artifact_dir = tmp_path / "artifacts"
    first_calls = []

    async def first_streamer(**kwargs):
        first_calls.append(kwargs)
        batch = json.loads(kwargs["prompt"].split("VALIDATION_REQUEST:\n", 1)[1])
        yield AGENT_FINAL_ANSWER_PREFIX + json.dumps(
            {
                "claims": [
                    {
                        "claim_id": claim["claim_id"],
                        "binding": claim["binding"],
                        "status": "supports",
                        "reason": f"verified {claim['binding']}",
                    }
                    for claim in batch["claims"]
                ]
            }
        )

    await materialize_behavior_claim_validation(
        artifact_dir=artifact_dir,
        repo_path=tmp_path,
        generator_identity="deepseek-reasoner",
        request=_request(),
        runtime_loader=lambda runtime_id: runtime,
        streamer=first_streamer,
    )
    assert len(first_calls) == 1

    changed_request = _request()
    changed_request["request_sha256"] = "request-456"
    changed_request["claims"][1] = {
        **changed_request["claims"][1],
        "statement": "changed expected behavior",
        "binding": "binding-2-changed",
    }
    second_calls = []

    async def second_streamer(**kwargs):
        second_calls.append(kwargs)
        batch = json.loads(kwargs["prompt"].split("VALIDATION_REQUEST:\n", 1)[1])
        assert [claim["binding"] for claim in batch["claims"]] == [
            "binding-2-changed"
        ]
        yield AGENT_FINAL_ANSWER_PREFIX + json.dumps(
            {
                "claims": [
                    {
                        "claim_id": batch["claims"][0]["claim_id"],
                        "binding": "binding-2-changed",
                        "status": "contradicts",
                        "reason": "changed claim conflicts with source",
                    }
                ]
            }
        )

    result = await materialize_behavior_claim_validation(
        artifact_dir=artifact_dir,
        repo_path=tmp_path,
        generator_identity="deepseek-reasoner",
        request=changed_request,
        runtime_loader=lambda runtime_id: runtime,
        streamer=second_streamer,
    )

    assert len(second_calls) == 1
    assert result["request_sha256"] == "request-456"
    assert result["reused_claim_count"] == 1
    assert result["validated_claim_count"] == 1
    assert [(claim["binding"], claim["status"]) for claim in result["claims"]] == [
        ("binding-1", "supports"),
        ("binding-2-changed", "contradicts"),
    ]


@pytest.mark.asyncio
async def test_materialize_behavior_claim_validation_batches_without_losing_verdicts(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.behavior_claim_validator import materialize_behavior_claim_validation

    monkeypatch.setattr(settings, "behavior_claim_audit_enabled", True)
    monkeypatch.setattr(settings, "behavior_claim_audit_batch_size", 1)
    monkeypatch.setattr(settings, "behavior_claim_audit_concurrency", 2)
    request = _request()
    calls = []

    async def streamer(**kwargs):
        calls.append(kwargs)
        batch = json.loads(kwargs["prompt"].split("VALIDATION_REQUEST:\n", 1)[1])
        yield AGENT_FINAL_ANSWER_PREFIX + json.dumps(
            {
                "claims": [
                    {
                        "claim_id": claim["claim_id"],
                        "status": "supports",
                        "reason": "batch verified",
                    }
                    for claim in batch["claims"]
                ]
            }
        )

    runtime = {
        "id": "default-codex",
        "provider": "codex",
        "command": "codex",
        "args": [],
        "resume_args": [],
        "env": {},
        "enabled": True,
    }
    result = await materialize_behavior_claim_validation(
        artifact_dir=tmp_path / "artifacts",
        repo_path=tmp_path,
        generator_identity="deepseek-reasoner",
        request=request,
        runtime_loader=lambda runtime_id: runtime,
        streamer=streamer,
    )

    assert len(calls) == 2
    assert [item["claim_id"] for item in result["claims"]] == [
        "ROW:sfmea.json:SFMEA-001",
        "ROW:black_box_cases.json:TC-001",
    ]
    assert all(item["status"] == "supports" for item in result["claims"])


@pytest.mark.asyncio
async def test_behavior_claim_validation_splits_a_truncated_multi_claim_batch(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.behavior_claim_validator import materialize_behavior_claim_validation

    monkeypatch.setattr(settings, "behavior_claim_audit_enabled", True)
    monkeypatch.setattr(settings, "behavior_claim_audit_batch_size", 2)
    monkeypatch.setattr(settings, "behavior_claim_audit_concurrency", 2)
    calls: list[int] = []

    async def streamer(**kwargs):
        batch = json.loads(kwargs["prompt"].split("VALIDATION_REQUEST:\n", 1)[1])
        calls.append(len(batch["claims"]))
        if len(batch["claims"]) > 1:
            # Mirrors a provider response cut midway through an otherwise
            # valid object. The retry must retain this raw diagnostic and
            # re-validate only its two atomic claims.
            yield AGENT_FINAL_ANSWER_PREFIX + '{"claims":[{"claim_id":"partial"'
            return
        claim = batch["claims"][0]
        yield AGENT_FINAL_ANSWER_PREFIX + json.dumps(
            {"claims": [{
                "claim_id": claim["claim_id"],
                "binding": claim["binding"],
                "status": "supports",
                "reason": "single claim verified",
            }]}
        )

    runtime = {"id": "audit", "provider": "claude", "command": "claude", "env": {}}
    result = await materialize_behavior_claim_validation(
        artifact_dir=tmp_path / "artifacts",
        repo_path=tmp_path,
        generator_identity="agent-runtime:generator",
        request=_request(),
        runtime_loader=lambda _runtime_id: runtime,
        streamer=streamer,
    )

    assert calls == [2, 1, 1]
    assert [item["status"] for item in result["claims"]] == ["supports", "supports"]
    assert (tmp_path / "artifacts" / "behavior_claim_audit" / "batch_01" / "raw_output.txt").is_file()
    assert (tmp_path / "artifacts" / "behavior_claim_audit" / "batch_1-a" / "raw_output.txt").is_file()


@pytest.mark.asyncio
async def test_materialize_behavior_claim_validation_keeps_duplicate_ids_across_batches(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.behavior_claim_validator import materialize_behavior_claim_validation

    monkeypatch.setattr(settings, "behavior_claim_audit_enabled", True)
    monkeypatch.setattr(settings, "behavior_claim_audit_batch_size", 1)
    monkeypatch.setattr(settings, "behavior_claim_audit_concurrency", 2)
    request = _request()
    request["claims"] = [
        {
            "claim_id": "TC-04",
            "artifact": "sfmea.json",
            "row_id": "SFMEA-04",
            "binding": "sfmea-binding",
            "context_ids": ["CTX-001"],
        },
        {
            "claim_id": "TC-04",
            "artifact": "black_box_cases.json",
            "row_id": "BB-04",
            "binding": "blackbox-binding",
            "context_ids": ["CTX-001"],
        },
    ]

    async def streamer(**kwargs):
        batch = json.loads(kwargs["prompt"].split("VALIDATION_REQUEST:\n", 1)[1])
        claim = batch["claims"][0]
        yield AGENT_FINAL_ANSWER_PREFIX + json.dumps(
            {
                "claims": [
                    {
                        "claim_id": claim["claim_id"],
                        "binding": claim["binding"],
                        "status": (
                            "supports"
                            if claim["binding"] == "blackbox-binding"
                            else "insufficient"
                        ),
                        "reason": claim["binding"],
                    }
                ]
            }
        )

    runtime = {
        "id": "default-codex",
        "provider": "codex",
        "command": "codex",
        "args": [],
        "resume_args": [],
        "env": {},
        "enabled": True,
    }
    result = await materialize_behavior_claim_validation(
        artifact_dir=tmp_path / "artifacts",
        repo_path=tmp_path,
        generator_identity="deepseek-reasoner",
        request=request,
        runtime_loader=lambda runtime_id: runtime,
        streamer=streamer,
    )

    assert [(item["binding"], item["status"]) for item in result["claims"]] == [
        ("sfmea-binding", "insufficient"),
        ("blackbox-binding", "supports"),
    ]
