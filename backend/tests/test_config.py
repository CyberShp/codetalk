from app.config import Settings


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


def test_staged_quality_repair_defaults_to_three_bounded_attempts(monkeypatch):
    monkeypatch.delenv("STAGED_QUALITY_REPAIR_MAX_ATTEMPTS", raising=False)

    assert Settings(_env_file=None).staged_quality_repair_max_attempts == 3


def test_behavior_claim_audit_defaults_to_bounded_parallel_medium_reasoning(monkeypatch):
    monkeypatch.delenv("BEHAVIOR_CLAIM_AUDIT_REASONING_EFFORT", raising=False)

    audit = Settings(_env_file=None)

    assert audit.behavior_claim_audit_reasoning_effort == "medium"
    assert audit.behavior_claim_audit_batch_size == 16
    assert audit.behavior_claim_audit_concurrency == 4
    assert audit.behavior_claim_audit_timeout_seconds == 360
