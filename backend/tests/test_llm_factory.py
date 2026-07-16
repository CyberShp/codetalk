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
