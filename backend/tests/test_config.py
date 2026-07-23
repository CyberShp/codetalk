import os
import tempfile

from app.config import Settings, configure_runtime_temp_environment


def test_default_cors_origins_exclude_retired_frontend_ports():
    origins = Settings().cors_origins_list

    assert "http://localhost:3003" in origins
    assert "http://127.0.0.1:3003" in origins
    assert "http://localhost:3205" not in origins
    assert "http://127.0.0.1:3205" not in origins


def test_workbench_v2_is_enabled_by_default_and_can_roll_back(monkeypatch):
    monkeypatch.delenv("WORKBENCH_V2_ENABLED", raising=False)
    assert Settings(_env_file=None).workbench_v2_enabled is True

    monkeypatch.setenv("WORKBENCH_V2_ENABLED", "false")
    assert Settings(_env_file=None).workbench_v2_enabled is False


def test_staged_quality_repair_defaults_to_two_bounded_attempts(monkeypatch):
    monkeypatch.delenv("STAGED_QUALITY_REPAIR_MAX_ATTEMPTS", raising=False)

    configured = Settings(_env_file=None)

    assert configured.staged_quality_repair_max_attempts == 3
    assert configured.staged_workflow_timeout_seconds == 1180
    assert configured.staged_quality_repair_min_remaining_seconds == 120
    assert configured.staged_workflow_shutdown_grace_seconds == 2.0


def test_regular_stage_quality_repair_uses_primary_model_by_default(monkeypatch):
    monkeypatch.delenv("REGULAR_STAGE_QUALITY_REPAIR_USE_PRIMARY_MODEL", raising=False)

    assert Settings(_env_file=None).regular_stage_quality_repair_use_primary_model is True


def test_behavior_claim_audit_defaults_to_bounded_parallel_medium_reasoning(monkeypatch):
    monkeypatch.delenv("BEHAVIOR_CLAIM_AUDIT_REASONING_EFFORT", raising=False)

    audit = Settings(_env_file=None)

    assert audit.behavior_claim_audit_reasoning_effort == "medium"
    assert audit.behavior_claim_audit_batch_size == 8
    assert audit.behavior_claim_audit_concurrency == 4
    assert audit.behavior_claim_audit_timeout_seconds == 360
    assert audit.behavior_claim_audit_heartbeat_seconds == 10.0


def test_source_analysis_cache_schema_tracks_relevance_v6(monkeypatch):
    monkeypatch.delenv("SOURCE_ANALYSIS_SCHEMA_VERSION", raising=False)

    assert Settings(_env_file=None).source_analysis_schema_version == "source-evidence-pack-v6"


def test_runtime_temp_directory_defaults_to_data_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("CODETALK_TEMP_DIR", raising=False)
    configured = Settings(_env_file=None, data_dir=str(tmp_path / "runtime-data"))

    assert configured.runtime_temp_path == (tmp_path / "runtime-data" / "tmp").resolve()


def test_runtime_temp_directory_accepts_codetalk_override(monkeypatch, tmp_path):
    temp_root = tmp_path / "media-temp"
    monkeypatch.setenv("CODETALK_TEMP_DIR", str(temp_root))

    configured = Settings(_env_file=None, data_dir=str(tmp_path / "runtime-data"))

    assert configured.runtime_temp_path == temp_root.resolve()


def test_configure_runtime_temp_environment_updates_python_and_child_env(monkeypatch, tmp_path):
    original_tempdir = tempfile.tempdir
    original_env = {
        key: os.environ.get(key)
        for key in ("CODETALK_TEMP_DIR", "TEMP", "TMP", "TMPDIR")
    }
    configured = Settings(
        _env_file=None,
        data_dir=str(tmp_path / "runtime-data"),
        CODETALK_TEMP_DIR=str(tmp_path / "media-temp"),
    )
    try:
        resolved = configure_runtime_temp_environment(configured)

        assert tempfile.gettempdir() == str(resolved)
        assert resolved.is_dir()
        for key in ("CODETALK_TEMP_DIR", "TEMP", "TMP", "TMPDIR"):
            assert os.environ[key] == str(resolved)
    finally:
        tempfile.tempdir = original_tempdir
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
