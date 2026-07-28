"""Codex CLI provider adapter."""

from app.services.provider_adapters.cli_base import CliProviderAdapter
from app.services.provider_adapters.contracts import ProviderCapabilities


class CodexCliAdapter(CliProviderAdapter):
    provider = "codex"
    default_command = "codex"
    prompt_transport = "codex_exec_json"
    output_mode = "stream_json"
    provider_capabilities = ProviderCapabilities(
        streaming=True,
        tool_call=True,
        session_resume=True,
        structured_output=True,
        mcp=True,
        skills=True,
        cancellation=True,
    )
