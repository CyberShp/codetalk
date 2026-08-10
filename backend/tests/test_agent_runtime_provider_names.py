from app.services.agent_runtimes import AgentRuntimeStore


def test_agent_runtime_accepts_arbitrary_provider_name(tmp_path):
    store = AgentRuntimeStore(tmp_path / "agent-runtimes.db")

    payload = store._normalize_payload(
        {
            "name": "Internal Company Agent",
            "provider": "internal-company-agent",
            "command": "python",
        },
        partial=False,
    )

    assert payload["provider"] == "internal-company-agent"
    assert payload["prompt_transport"] == "stdin"
