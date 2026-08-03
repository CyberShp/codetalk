from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


def _judgment(*, candidate: str = "The reset request joins the pending queue."):
    from app.services.quality_benchmark_semantic_judge import SemanticJudgment

    return SemanticJudgment(
        judgment_id="accuracy-001",
        axis="accuracy",
        candidate_statement=candidate,
        oracle_statement=(
            "A busy concurrent reset appends the request to pending_resets."
        ),
        evidence_refs=("source://storage.c#reset-busy:L2-L3",),
    )


def test_behavior_claim_batch_judge_reuses_bound_l2_validator_and_records_identity(
    tmp_path: Path,
) -> None:
    from app.services.quality_benchmark_semantic_judge import (
        BehaviorClaimBatchSemanticJudge,
    )

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text(
        "if (busy) {\n  pending_resets.append(req);\n}\n", encoding="utf-8"
    )
    seen: dict[str, object] = {}

    async def fake_materializer(**kwargs):
        seen.update(kwargs)
        request = kwargs["request"]
        assert request["kind"] == "behavior_claim_validation_request"
        assert request["claims"][0]["type"] == "source_behavior"
        assert "pending queue" in request["claims"][0]["statement"]
        assert "pending_resets" in request["claims"][0]["statement"]
        assert request["contexts"][0]["path"] == "storage.c"
        assert "pending_resets.append" in request["contexts"][0]["content"]
        return {
            "kind": "behavior_claim_validation",
            "schema_version": 2,
            "status": "completed",
            "request_sha256": request["request_sha256"],
            "validator": {
                "provider": "fixture-provider",
                "runtime_id": "fixture-runtime",
                "model": "judge-model-v2",
                "reasoning_effort": "high",
                "independent": True,
            },
            "response_models": ["judge-model-v2"],
            "claims": [
                {
                    "claim_id": request["claims"][0]["claim_id"],
                    "binding": request["claims"][0]["binding"],
                    "status": "supports",
                    "reason": "The cited branch appends the busy request.",
                }
            ],
        }

    result = BehaviorClaimBatchSemanticJudge(
        materializer=fake_materializer
    ).judge(
        judgments=(_judgment(),),
        source_dir=source,
        generator_model="generator-model-v1",
        deadline_monotonic=time.monotonic() + 10,
        snapshot_label="first_pass",
    )

    assert result.verdicts == {"accuracy-001": "supports"}
    assert result.limitations == ()
    assert seen["repo_path"] == source.resolve()
    assert seen["generator_identity"] == "agent-runtime:codex:generator-model-v1"
    assert 0 < float(seen["timeout_seconds"]) <= 10
    assert result.metadata["snapshot"] == "first_pass"
    assert result.metadata["judge_version"] == "quality-semantic-judge-v1"
    assert result.metadata["judge"]["model"] == "judge-model-v2"
    assert len(result.metadata["request_sha256"]) == 64
    assert len(result.metadata["result_sha256"]) == 64
    assert "pending_resets" not in json.dumps(result.metadata)


def test_behavior_claim_batch_judge_fails_closed_for_same_model(tmp_path: Path) -> None:
    from app.services.quality_benchmark_semantic_judge import (
        BehaviorClaimBatchSemanticJudge,
    )

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("busy\nappend\nreturn\n", encoding="utf-8")

    async def same_model_materializer(**kwargs):
        request = kwargs["request"]
        return {
            "status": "completed",
            "request_sha256": request["request_sha256"],
            "validator": {
                "provider": "codex",
                "runtime_id": "audit",
                "model": "gpt-5.6-sol",
                "independent": True,
            },
            "response_models": ["gpt-5.6-sol"],
            "claims": [
                {
                    "claim_id": request["claims"][0]["claim_id"],
                    "binding": request["claims"][0]["binding"],
                    "status": "supports",
                    "reason": "claimed support",
                }
            ],
        }

    result = BehaviorClaimBatchSemanticJudge(
        materializer=same_model_materializer
    ).judge(
        judgments=(_judgment(),),
        source_dir=source,
        generator_model="gpt-5.6-sol",
        deadline_monotonic=time.monotonic() + 10,
        snapshot_label="final_after_auto_repair",
    )

    assert result.verdicts == {"accuracy-001": "insufficient"}
    assert result.metadata["status"] == "non_independent"
    assert result.limitations == ("SEMANTIC_JUDGE_NOT_INDEPENDENT",)


def test_behavior_claim_batch_judge_timeout_is_explicit_and_cannot_pass(
    tmp_path: Path,
) -> None:
    from app.services.quality_benchmark_semantic_judge import (
        BehaviorClaimBatchSemanticJudge,
    )

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("busy\nappend\nreturn\n", encoding="utf-8")
    called = False

    async def must_not_run(**_kwargs):
        nonlocal called
        called = True
        await asyncio.sleep(0)
        return {}

    result = BehaviorClaimBatchSemanticJudge(materializer=must_not_run).judge(
        judgments=(_judgment(),),
        source_dir=source,
        generator_model="generator-model",
        deadline_monotonic=time.monotonic() - 0.01,
        snapshot_label="first_pass",
    )

    assert called is False
    assert result.verdicts == {"accuracy-001": "insufficient"}
    assert result.metadata["status"] == "timed_out"
    assert result.limitations == ("SEMANTIC_JUDGE_DEADLINE_EXCEEDED",)


def test_behavior_claim_batch_judge_unavailable_result_is_not_a_support(
    tmp_path: Path,
) -> None:
    from app.services.quality_benchmark_semantic_judge import (
        BehaviorClaimBatchSemanticJudge,
    )

    source = tmp_path / "source"
    source.mkdir()
    (source / "storage.c").write_text("busy\nappend\nreturn\n", encoding="utf-8")

    async def unavailable_materializer(**kwargs):
        return {
            "status": "unavailable",
            "request_sha256": kwargs["request"]["request_sha256"],
            "validator": {
                "provider": "",
                "runtime_id": "",
                "model": "",
                "independent": False,
            },
            "claims": [],
        }

    result = BehaviorClaimBatchSemanticJudge(
        materializer=unavailable_materializer
    ).judge(
        judgments=(_judgment(),),
        source_dir=source,
        generator_model="generator-model",
        deadline_monotonic=time.monotonic() + 10,
        snapshot_label="first_pass",
    )

    assert result.verdicts == {"accuracy-001": "insufficient"}
    assert result.metadata["status"] == "unavailable"
    assert result.limitations == ("SEMANTIC_JUDGE_UNAVAILABLE",)


def test_default_materializer_uses_explicit_codex_harness_identity_without_ui_db(
    tmp_path: Path,
) -> None:
    from app.services.quality_benchmark_semantic_judge import (
        CodexHarnessSemanticMaterializer,
    )

    source = tmp_path / "source"
    source.mkdir()
    artifact_roots: list[Path] = []
    prepared = []
    sandbox_calls = []

    class FakeFacade:
        def __init__(self, artifact_dir):
            self.artifact_dir = Path(artifact_dir)
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_roots.append(self.artifact_dir)

        def prepare(self, request):
            prepared.append(request)
            return SimpleNamespace(run_id="judge-run")

        def execute(self, _session, **_kwargs):
            request = prepared[-1].task_bundle["validation_request"]
            (self.artifact_dir / "semantic_verdicts.json").write_text(
                json.dumps(
                    {
                        "claims": [
                            {
                                "claim_id": request["claims"][0]["claim_id"],
                                "binding": request["claims"][0]["binding"],
                                "status": "supports",
                                "reason": "The exact source context supports it.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (self.artifact_dir / "sandbox_policy.json").write_text(
                '{"status":"active","engine":"fixture"}', encoding="utf-8"
            )
            return SimpleNamespace(
                status="completed",
                timed_out=False,
                exit_code=0,
                session_id="judge-run",
                provider_diagnostics={},
            )

    @contextmanager
    def fake_sandbox(**kwargs):
        sandbox_calls.append(kwargs)
        yield

    materializer = CodexHarnessSemanticMaterializer(
        facade_factory=FakeFacade,
        codex_resolver=lambda: "/opt/codex/bin/codex",
        cli_version_loader=lambda _command: "codex-cli 0.145.0",
        sandbox_factory=fake_sandbox,
        approved_network_targets=("localhost:9443",),
    )
    request = {
        "kind": "behavior_claim_validation_request",
        "schema_version": 2,
        "claims": [
            {
                "claim_id": "accuracy-001",
                "binding": "accuracy-001",
                "type": "source_behavior",
                "statement": "candidate and oracle",
                "context_ids": [],
                "evidence_bindings": [],
            }
        ],
        "contexts": [],
        "candidate_count": 1,
        "requested_count": 1,
        "truncated": False,
        "request_sha256": "a" * 64,
    }

    result = asyncio.run(
        materializer(
            request=request,
            repo_path=source,
            generator_identity="agent-runtime:codex:gpt-5.6-sol",
            timeout_seconds=10,
            judge_model="gpt-5.5",
            mode="deep",
            deadline_monotonic=time.monotonic() + 10,
        )
    )

    assert result["status"] == "completed"
    assert result["validator"] == {
        "provider": "openai",
        "runtime_id": "quality-benchmark-codex-cli",
        "model": "gpt-5.5",
        "reasoning_effort": "high",
        "independent": True,
        "cli_version": "codex-cli 0.145.0",
    }
    assert prepared[0].provider == "codex"
    assert prepared[0].cwd == str(source.resolve())
    assert prepared[0].prompt_transport == "codex_exec_json"
    assert prepared[0].command[:3] == [
        "/opt/codex/bin/codex",
        "-m",
        "gpt-5.5",
    ]
    assert "--ephemeral" in prepared[0].command
    assert "--ignore-rules" in prepared[0].command
    assert "--skip-git-repo-check" in prepared[0].command
    assert prepared[0].task_bundle["required_artifacts"] == [
        "semantic_verdicts.json"
    ]
    assert sandbox_calls[0]["model"] == "gpt-5.5"
    assert sandbox_calls[0]["source_dir"] == source.resolve()
    assert not artifact_roots[0].exists()
