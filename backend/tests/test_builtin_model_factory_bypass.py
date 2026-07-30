from __future__ import annotations

import types
from pathlib import Path


def _request(run_id: str = "factory-run"):
    from app.services.harness_facade import HarnessRunRequest

    return HarnessRunRequest(
        provider="builtin-llm",
        command=[],
        cwd="/repo",
        workflow_snapshot={"compiled_contract_version": 3},
        task_bundle={"required_artifacts": []},
        prompt_transport="builtin_llm",
        requires_network=False,
        run_id=run_id,
    )


def test_llm_factory_uses_lifecycle_safe_builtin_adapter(tmp_path):
    from app.llm.factory import create_builtin_model_adapter

    adapter = create_builtin_model_adapter(
        tmp_path,
        execute_callable=lambda **_kwargs: {"status": "completed"},
    )

    assert type(adapter).__module__ == "app.services.provider_adapters.safe_builtin_model"
    session = adapter.prepare(_request())
    staging = Path(session.artifact_dir)
    assert staging.is_dir()
    assert staging.parent.name == ".builtin-model-staging"
    assert len(staging.name) == 12

    adapter.finalize_artifacts(session)
    assert not staging.exists()


def test_llm_factory_installs_short_runner_writer_for_builtin_closure(
    tmp_path,
    monkeypatch,
):
    import app.services.workbench_workflow_runner as runner
    from app.llm.factory import create_builtin_model_adapter
    from app.services.provider_adapters.safe_builtin_model import (
        _short_atomic_write_json,
    )

    original_writer = runner._write_json
    monkeypatch.setattr(runner, "_write_json", original_writer)

    def closure_template(**_kwargs):
        return _write_json  # noqa: F821 - resolved from runner module globals

    runner_closure = types.FunctionType(
        closure_template.__code__,
        runner.__dict__,
        name="execute_builtin_model",
    )

    adapter = create_builtin_model_adapter(
        tmp_path,
        execute_callable=runner_closure,
    )

    assert runner._write_json is _short_atomic_write_json
    session = adapter.prepare(_request("factory-runner"))
    assert len(Path(session.artifact_dir).name) == 12
    adapter.finalize_artifacts(session)
