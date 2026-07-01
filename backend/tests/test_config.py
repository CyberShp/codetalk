from app.config import Settings


def test_default_cors_origins_exclude_retired_frontend_ports():
    origins = Settings().cors_origins_list

    assert "http://localhost:3003" in origins
    assert "http://127.0.0.1:3003" in origins
    assert "http://localhost:3205" not in origins
    assert "http://127.0.0.1:3205" not in origins
