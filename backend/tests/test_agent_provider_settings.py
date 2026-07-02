import aiosqlite
import pytest

from app.config import settings
from app.services.agent_provider_settings import apply_persisted_agent_provider_settings


pytestmark = pytest.mark.asyncio


async def _write_settings_db(db_path, rows):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        await db.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            rows,
        )
        await db.commit()


async def test_default_sqlite_path_follows_patched_data_dir(tmp_path, monkeypatch):
    old_root = tmp_path / "old-root"
    new_root = tmp_path / "new-root"
    old_db = old_root / "data" / "codetalk.db"
    new_db = new_root / "data" / "codetalk.db"
    await _write_settings_db(old_db, [("external_agent_custom_providers", "[]")])
    await _write_settings_db(
        new_db,
        [
            (
                "external_agent_custom_providers",
                '[{"id":"local-agent","command":"python agent.py","prompt_transport":"stdin"}]',
            )
        ],
    )
    monkeypatch.chdir(old_root)
    monkeypatch.setattr(settings, "data_dir", str(new_root / "data"))
    monkeypatch.setattr(settings, "sqlite_db", "data/codetalk.db")
    monkeypatch.setattr(settings, "external_agent_custom_providers", [])

    payload = await apply_persisted_agent_provider_settings()

    assert payload["external_agent_custom_providers"][0]["id"] == "local-agent"
    assert settings.external_agent_custom_providers[0]["command"] == "python agent.py"
