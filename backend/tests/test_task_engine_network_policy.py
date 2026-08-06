from unittest.mock import ANY, patch

import pytest

from app.adapters.base import ToolCapability, UnifiedResult
from app.services.task_engine import _build_summary


@pytest.mark.asyncio
async def test_legacy_task_summary_uses_configured_model_endpoint_without_proxy_env(monkeypatch):
    """Legacy task summaries pass through configured model endpoints without env proxy leakage."""
    monkeypatch.setattr("app.services.network_policy.settings.intranet_network_mode", True)
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_hosts", [])
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_cidrs", [])
    result = UnifiedResult(
        tool_name="local-tool",
        capability=ToolCapability.DOCUMENTATION,
        data={"documentation": "local evidence"},
    )

    class FailingAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            raise ConnectionError("blocked by test double")

    with patch("app.services.task_engine.httpx.AsyncClient", side_effect=FailingAsyncClient) as client:
        summary = await _build_summary(
            [result],
            {
                "llm_base_url": "https://api.openai.com/v1",
                "llm_api_key": "redacted",
                "model": "test-model",
            },
        )

    assert summary == "local-tool: generated documentation (14 chars, 0 diagrams). Preview: local evidence"
    client.assert_called_once_with(
        base_url="https://api.openai.com/v1",
        timeout=ANY,
        trust_env=False,
    )
