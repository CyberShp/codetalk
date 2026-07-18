import json
from pathlib import Path

import pytest

from app.services.agent_cli_bridge import AGENT_FINAL_ANSWER_PREFIX


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
    assert "field_patch" in prompt


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
