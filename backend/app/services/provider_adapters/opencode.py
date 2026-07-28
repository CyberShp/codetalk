"""OpenCode CLI provider adapter."""

from app.services.provider_adapters.cli_base import CliProviderAdapter
from app.services.provider_adapters.contracts import ProviderCapabilities


class OpenCodeAdapter(CliProviderAdapter):
    provider = "opencode"
    default_command = "opencode"
    prompt_transport = "opencode_run_arg"
    output_mode = "auto"
    provider_capabilities = ProviderCapabilities(
        streaming=True,
        tool_call=True,
        session_resume=True,
        structured_output=False,
        mcp=True,
        skills=False,
        cancellation=True,
    )
