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
