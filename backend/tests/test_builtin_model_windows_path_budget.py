from __future__ import annotations

import types
from pathlib import Path


def _request():
    from app.services.harness_facade import HarnessRunRequest

    return HarnessRunRequest(
        provider="builtin-llm",
        command=[],
        cwd="/repo",
        workflow_snapshot={"compiled_contract_version": 3},
        task_bundle={"required_artifacts": []},
        prompt_transport="builtin_llm",
        requires_network=False,
        run_id="windows-path-run",
    )


def test_short_atomic_json_writer_does_not_repeat_long_target_name(tmp_path, monkeypatch):
    from app.services.provider_adapters import safe_builtin_model

    captured: dict[str, object] = {}
    original = safe_builtin_model.tempfile.NamedTemporaryFile

    def recording_named_temporary_file(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(
        safe_builtin_model.tempfile,
        "NamedTemporaryFile",
        recording_named_temporary_file,
    )
    target = (
        tmp_path
        / ("很长的中文工作流目录" * 3)
        / "builtin_llm_execution_input.json"
    )

    safe_builtin_model._short_atomic_write_json(target, {"ok": True})

    assert target.read_text(encoding="utf-8").strip().startswith("{")
    assert captured["prefix"] == ".ct-"
    assert captured["suffix"] == ".tmp"
    assert Path(str(captured["dir"])) == target.parent
    assert target.name not in str(captured["prefix"])


def test_builtin_prepare_uses_short_staging_epoch(tmp_path):
    from app.services.provider_adapters.safe_builtin_model import BuiltinModelAdapter

    adapter = BuiltinModelAdapter(
        tmp_path,
        execute_callable=lambda **_kwargs: {"status": "completed"},
    )
    session = adapter.prepare(_request())
    staging = Path(session.artifact_dir)

    assert staging.is_dir()
    assert staging.parent.name == ".builtin-model-staging"
    assert len(staging.name) == 12

    adapter.finalize_artifacts(session)


def test_registry_builtin_installs_short_writer_for_runner_closure(tmp_path, monkeypatch):
    import app.services.workbench_workflow_runner as runner
    from app.services.provider_adapters.registry import create_provider_adapter
    from app.services.provider_adapters.safe_builtin_model import _short_atomic_write_json

    original_writer = runner._write_json
    monkeypatch.setattr(runner, "_write_json", original_writer)

    def closure_template(**_kwargs):
        return _write_json  # noqa: F821 - resolved from runner globals below

    runner_closure = types.FunctionType(
        closure_template.__code__,
        runner.__dict__,
        name="execute_builtin_model",
    )

    adapter = create_provider_adapter(
        provider="builtin-llm",
        prompt_transport="builtin_llm",
        artifact_dir=tmp_path,
        builtin_execute_callable=runner_closure,
    )

    assert adapter is not None
    assert runner._write_json is _short_atomic_write_json
