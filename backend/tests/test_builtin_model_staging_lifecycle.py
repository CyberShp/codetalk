from __future__ import annotations

import threading
import time
from pathlib import Path


def _request(*, run_id: str = "same-run"):
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


def _adapter(tmp_path, execute_callable):
    from app.services.provider_adapters.registry import create_provider_adapter

    adapter = create_provider_adapter(
        provider="builtin-llm",
        prompt_transport="builtin_llm",
        artifact_dir=tmp_path,
        builtin_execute_callable=execute_callable,
    )
    assert adapter is not None
    return adapter


def test_registry_builtin_prepare_creates_unique_existing_staging_epochs(tmp_path):
    adapter = _adapter(tmp_path, lambda **_kwargs: {"status": "completed"})

    first = adapter.prepare(_request(run_id="retry-run"))
    second = adapter.prepare(_request(run_id="retry-run"))

    first_dir = Path(first.artifact_dir)
    second_dir = Path(second.artifact_dir)
    assert first.session_id != second.session_id
    assert first_dir != second_dir
    assert first_dir.is_dir()
    assert second_dir.is_dir()
    first_dir.relative_to(tmp_path / ".builtin-model-staging")
    second_dir.relative_to(tmp_path / ".builtin-model-staging")
    assert len(first_dir.name) == 12
    assert len(second_dir.name) == 12

    adapter.finalize_artifacts(first)
    adapter.finalize_artifacts(second)
    assert not first_dir.exists()
    assert not second_dir.exists()


def test_builtin_timeout_keeps_staging_until_worker_finishes_writing(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    write_errors: list[BaseException] = []

    def slow_builtin(*, session, **_kwargs):
        entered.set()
        release.wait(timeout=1)
        try:
            # Match the production failure: the runner writes an atomic temporary
            # input file without recreating the provider epoch around every write.
            temporary = Path(session.artifact_dir) / (
                ".builtin_llm_execution_input.json.test.tmp"
            )
            temporary.write_text("{}", encoding="utf-8")
        except BaseException as exc:
            write_errors.append(exc)
            raise
        finally:
            finished.set()
        return {"status": "completed", "exit_code": 0, "artifacts": []}

    adapter = _adapter(tmp_path, slow_builtin)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    session = facade.prepare(_request(run_id="timeout-run"))
    staging_dir = Path(session.artifact_dir)

    result = facade.execute(session, timeout_sec=0.03)

    assert entered.is_set()
    assert result.status == "error"
    assert result.timed_out is True
    # The public execute call has returned, but the synchronous model worker still
    # owns the directory and must be able to finish diagnostic writes safely.
    assert staging_dir.is_dir()

    release.set()
    assert finished.wait(timeout=0.5)
    deadline = time.monotonic() + 0.5
    while staging_dir.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert write_errors == []
    assert not staging_dir.exists()
    assert list(tmp_path.glob(".builtin_llm_execution_input.json*")) == []


def test_builtin_finalize_is_idempotent_after_completed_worker(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade

    def complete(*, session, **_kwargs):
        path = Path(session.artifact_dir) / "diagnostic.json"
        path.write_text("{}", encoding="utf-8")
        return {"status": "completed", "exit_code": 0, "artifacts": []}

    adapter = _adapter(tmp_path, complete)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    session = facade.prepare(_request(run_id="complete-run"))

    result = facade.execute(session)

    assert result.status == "completed"
    assert not Path(session.artifact_dir).exists()
    adapter.finalize_artifacts(session)
    adapter.finalize_artifacts(session)
    assert not Path(session.artifact_dir).exists()
