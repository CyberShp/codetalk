from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.services.quality_evaluation_contract import (
    BenchmarkIdentity,
    EvaluationScope,
    RepairSummary,
)
from tests.test_quality_evaluator import _snapshot


class _Store:
    def __init__(self, root: Path, *, status: str = "completed"):
        self.root = root
        self.status = status

    def load(self, task_run_id: str):
        if task_run_id == "missing":
            raise KeyError(task_run_id)
        return SimpleNamespace(
            task_run_id=task_run_id,
            artifact_dir=str(self.root / task_run_id),
            status=self.status,
        )


@pytest.fixture
async def quality_client(tmp_path):
    from app.api import quality_evaluations as api_module
    from app.services.quality_evaluator import build_quality_report

    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    report = build_quality_report(
        scope=EvaluationScope.INDEPENDENT_BENCHMARK,
        run_ref="run-1",
        benchmark_identity=BenchmarkIdentity(
            case_id="hidden-case", source_revision="d" * 40, truth_package_version="1"
        ),
        first_pass=_snapshot(),
        final_after_auto_repair=_snapshot(),
        repair_summary=RepairSummary(
            attempt_count=0, elapsed_seconds=0, terminal_block_reason=None
        ),
    )
    (run_dir / "quality_evaluation_report.json").write_text(
        report.model_dump_json(by_alias=True), encoding="utf-8"
    )

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(api_module.router)
    app.dependency_overrides[api_module.get_task_run_store] = lambda: _Store(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, app, api_module


async def test_api_projects_axes_without_hidden_truth_answers(quality_client) -> None:
    client, _, _ = quality_client
    response = await client.get("/api/workbench/task-runs/run-1/quality-evaluation")
    assert response.status_code == 200
    payload = response.json()
    encoded = json.dumps(payload)
    assert payload["scope"] == "independent_benchmark"
    assert payload["final_after_auto_repair"]["accuracy"]["status"] == "pass"
    assert "gold_claims" not in encoded
    assert "coverage_universe" not in encoded
    assert "critical_chains" not in encoded
    assert "miss_ids" not in encoded
    assert "evidence_refs" not in encoded


async def test_api_returns_404_409_and_422_for_absent_incomplete_and_scope_mismatch(quality_client, tmp_path) -> None:
    client, app, api_module = quality_client
    assert (await client.get("/api/workbench/task-runs/missing/quality-evaluation")).status_code == 404

    (tmp_path / "running").mkdir()
    app.dependency_overrides[api_module.get_task_run_store] = lambda: _Store(tmp_path, status="running")
    assert (await client.get("/api/workbench/task-runs/running/quality-evaluation")).status_code == 409

    app.dependency_overrides[api_module.get_task_run_store] = lambda: _Store(tmp_path)
    mismatch = await client.get(
        "/api/workbench/task-runs/run-1/quality-evaluation?scope=operational"
    )
    assert mismatch.status_code == 422


def test_api_critical_miss_projection_keeps_only_public_alias_and_layer() -> None:
    from app.api.quality_evaluations import _public_report

    projected = _public_report(
        {
            "critical_misses": [{
                "item_id": "hidden-gold-or-chain-id",
                "reason": "hidden semantic answer",
                "validation_layer": "L2",
                "evidence_refs": ["source://secret.c#L10-L20"],
            }],
        }
    )

    miss = projected["critical_misses"][0]
    assert miss["item_id"].startswith("public-quality-")
    assert miss["reason"] == "critical obligation is not satisfied"
    assert miss["validation_layer"] == "L2"
    encoded = json.dumps(projected)
    assert "hidden" not in encoded
    assert "secret.c" not in encoded


def test_api_critical_miss_projection_adds_axis_specific_public_next_action() -> None:
    from app.api.quality_evaluations import _public_report

    projected = _public_report(
        {
            "final_after_auto_repair": {
                "accuracy": {
                    "critical_misses": [{
                        "item_id": "claim:generated-claim-42",
                        "reason": "critical_unsupported_claim",
                        "validation_layer": "L2",
                        "evidence_refs": ["truth://private-package"],
                    }],
                },
                "breadth": {
                    "critical_misses": [{
                        "item_id": "hidden-breadth-id",
                        "reason": "critical protocol obligation was not realized",
                        "validation_layer": "L1",
                    }],
                },
                "depth": {
                    "critical_misses": [{
                        "item_id": "chain:hidden-chain/node:hidden-node",
                        "reason": "required causal node remains open",
                        "validation_layer": "L3",
                    }],
                },
            },
        }
    )

    axes = projected["final_after_auto_repair"]
    accuracy = axes["accuracy"]["critical_misses"][0]
    assert accuracy["item_id"].startswith("public-accuracy-")
    assert accuracy["public_label"] == "生成事实 generated-claim-42"
    assert accuracy["reason"] == "公开证据未能闭合该事实陈述"
    assert accuracy["recommended_action"] == (
        "核对公开源码证据与事实陈述，并修正不一致内容"
    )
    assert accuracy["validation_layer"] == "L2"
    assert axes["breadth"]["critical_misses"][0]["public_label"].startswith(
        "协议覆盖项 REF-"
    )
    assert axes["breadth"]["critical_misses"][0]["recommended_action"] == (
        "补充该关键场景及其对应测试证据"
    )
    assert axes["depth"]["critical_misses"][0]["public_label"].startswith(
        "因果链节点 REF-"
    )
    assert axes["depth"]["critical_misses"][0]["recommended_action"] == (
        "补充入口、状态转换、错误传播和验证结果的闭环证据"
    )
    encoded = json.dumps(projected, ensure_ascii=False)
    assert "hidden-breadth-id" not in encoded
    assert "hidden-chain" not in encoded
    assert "hidden-node" not in encoded
    assert "truth://" not in encoded


def test_api_public_aliases_are_stable_and_distinct_without_revealing_gold_ids() -> None:
    from app.api.quality_evaluations import _public_report

    payload = {
        "run_ref": "run-public-aliases",
        "final_after_auto_repair": {
            "accuracy": {
                "critical_misses": [
                    {
                        "item_id": "gold:hidden-gold-one",
                        "reason": "critical_gold_omitted",
                        "validation_layer": "L2",
                    },
                    {
                        "item_id": "gold:hidden-gold-two",
                        "reason": "critical_gold_omitted",
                        "validation_layer": "L2",
                    },
                ]
            }
        },
    }

    first = _public_report(payload)
    second = _public_report(payload)
    misses = first["final_after_auto_repair"]["accuracy"]["critical_misses"]

    assert first == second
    assert misses[0]["item_id"] != misses[1]["item_id"]
    assert misses[0]["public_label"] != misses[1]["public_label"]
    assert all(item["public_label"].startswith("遗漏的关键基准事实 REF-") for item in misses)
    encoded = json.dumps(first, ensure_ascii=False)
    assert "hidden-gold" not in encoded
