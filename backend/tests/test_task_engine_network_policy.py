from unittest.mock import patch

import pytest

from app.adapters.base import ToolCapability, UnifiedResult
from app.services.task_engine import _build_summary


@pytest.mark.asyncio
async def test_legacy_task_summary_rejects_unapproved_model_endpoint_before_http(monkeypatch):
    """Legacy task summaries must not bypass the V3 model egress policy."""
    monkeypatch.setattr("app.services.network_policy.settings.intranet_network_mode", True)
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_hosts", [])
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_cidrs", [])
    result = UnifiedResult(
        tool_name="local-tool",
        capability=ToolCapability.DOCUMENTATION,
        data={"documentation": "local evidence"},
    )

    with patch("app.services.task_engine.httpx.AsyncClient") as client:
        summary = await _build_summary(
            [result],
            {
                "llm_base_url": "https://api.openai.com/v1",
                "llm_api_key": "redacted",
                "model": "test-model",
            },
        )

    assert summary == "local-tool: generated documentation (14 chars, 0 diagrams). Preview: local evidence"
    client.assert_not_called()
