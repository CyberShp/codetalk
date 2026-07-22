import pytest

from app.llm.factory import (
    _automatic_source_analysis_model,
    create_source_analysis_llm_client,
)


def test_auto_source_analysis_routes_official_deepseek_reasoner_to_chat():
    assert _automatic_source_analysis_model(
        api_type="openai_compat",
        base_url="https://api.deepseek.com",
        model="deepseek-reasoner",
    ) == "deepseek-chat"


def test_auto_source_analysis_does_not_guess_for_other_providers():
    assert _automatic_source_analysis_model(
        api_type="openai_compat",
        base_url="https://internal.example/v1",
        model="deepseek-reasoner",
    ) is None


@pytest.mark.asyncio
async def test_optional_source_analysis_route_falls_back_when_settings_table_is_unavailable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.llm.factory.settings.sqlite_db", tmp_path / "empty.db")
    monkeypatch.setattr("app.llm.factory.settings.source_analysis_model", "auto")

    assert await create_source_analysis_llm_client() is None
    assert _automatic_source_analysis_model(
        api_type="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-opus",
    ) is None


@pytest.mark.asyncio
async def test_llm_factory_rejects_public_endpoint_in_intranet_mode(tmp_path, monkeypatch):
    import aiosqlite

    from app.llm.factory import create_llm_client

    db_path = tmp_path / "codetalk.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE llm_configs (
                id TEXT PRIMARY KEY,
                api_type TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT NOT NULL,
                model TEXT NOT NULL,
                config_json TEXT
            )
            """
        )
        await db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        await db.execute(
            "INSERT INTO llm_configs VALUES (?, ?, ?, ?, ?, ?)",
            ("public", "openai_compat", "https://api.openai.com", "secret", "gpt", None),
        )
        await db.commit()

    monkeypatch.setattr("app.llm.factory.settings.sqlite_db", db_path)
    monkeypatch.setattr("app.services.network_policy.settings.intranet_network_mode", True)
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_hosts", [])
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_cidrs", [])

    with pytest.raises(Exception, match="公网出口已被内网策略拒绝"):
        await create_llm_client("public")
