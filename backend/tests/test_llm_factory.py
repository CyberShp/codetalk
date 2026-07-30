import pytest

from app.llm.factory import (
    _automatic_source_analysis_model,
    create_source_analysis_llm_client,
)


def test_factory_builds_builtin_adapter_with_injected_dependencies(tmp_path):
    from app.llm.factory import create_builtin_model_adapter
    from app.services.provider_adapters.builtin_model import BuiltinModelAdapter

    async def client_factory():
        return object()

    def execute_callable(**_kwargs):
        return {"status": "completed"}

    adapter = create_builtin_model_adapter(
        tmp_path,
        client_factory=client_factory,
        execute_callable=execute_callable,
    )

    assert isinstance(adapter, BuiltinModelAdapter)
    assert adapter.client_factory is client_factory
    assert adapter.execute_callable is execute_callable


def test_auto_source_analysis_routes_official_deepseek_reasoner_to_chat():
    assert _automatic_source_analysis_model(
        api_type="openai_compat",
        base_url="https://api.deepseek.com",
        model="deepseek-reasoner",
    ) == "deepseek-v4-flash"


def test_auto_source_analysis_routes_official_deepseek_v4_pro_to_flash():
    assert _automatic_source_analysis_model(
        api_type="openai_compat",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
    ) == "deepseek-v4-flash"


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
async def test_llm_factory_allows_configured_model_without_deployment_approval(tmp_path, monkeypatch):
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
    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet")
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_hosts", [])
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_cidrs", [])

    client = await create_llm_client("public")
    try:
        assert client._enforce_network_policy is True
        assert client._configured_model_endpoint is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_llm_factory_allows_an_explicitly_approved_model_endpoint(tmp_path, monkeypatch):
    import aiosqlite

    from app.llm.factory import create_llm_client

    db_path = tmp_path / "codetalk.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE llm_configs (
                id TEXT PRIMARY KEY, api_type TEXT NOT NULL, base_url TEXT NOT NULL,
                api_key TEXT NOT NULL, model TEXT NOT NULL, config_json TEXT
            )
            """
        )
        await db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        await db.execute(
            "INSERT INTO llm_configs VALUES (?, ?, ?, ?, ?, ?)",
            ("approved", "openai_compat", "https://api.deepseek.com", "secret", "deepseek-chat", None),
        )
        await db.commit()

    monkeypatch.setattr("app.llm.factory.settings.sqlite_db", db_path)
    monkeypatch.setattr("app.services.network_policy.settings.network_policy_v2_enabled", True)
    monkeypatch.setattr("app.services.network_policy.settings.network_mode", "intranet")
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_hosts", ["api.deepseek.com"])
    monkeypatch.setattr("app.services.network_policy.settings.intranet_allowed_cidrs", [])
    monkeypatch.setattr(
        "app.services.network_policy.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("203.0.113.18", 443))],
    )

    client = await create_llm_client("approved")
    try:
        assert client._enforce_network_policy is True
        assert client._configured_model_endpoint is True
    finally:
        await client.close()


def test_factory_uses_only_deployment_proxy_and_ca_not_user_general_settings(monkeypatch):
    from app.llm.factory import _resolve_proxy

    monkeypatch.setattr("app.llm.factory.settings.network_policy_v2_enabled", True)
    monkeypatch.setattr("app.llm.factory.settings.network_mode", "intranet")
    monkeypatch.setattr(
        "app.llm.factory.settings.approved_proxy_url",
        "https://deploy-user:deploy-secret@gateway.corp.example:8443",
    )
    monkeypatch.setattr("app.llm.factory.settings.approved_proxy_config_id", "proxy-prod-1")
    monkeypatch.setattr("app.llm.factory.settings.approved_ca_bundle_path", "/etc/codetalk/corp-ca.pem")

    proxy_url, ssl_cert_path, force_direct = _resolve_proxy(
        {
            "proxy_mode": "custom",
            "proxy_url": "https://user:db-secret@untrusted.example:8080",
            "ssl_cert_path": "/tmp/user-ca.pem",
        }
    )

    assert proxy_url == "https://deploy-user:deploy-secret@gateway.corp.example:8443"
    assert ssl_cert_path == "/etc/codetalk/corp-ca.pem"
    assert force_direct is False


def test_factory_model_request_url_matches_openai_client_when_base_already_has_v1():
    from app.llm.factory import _model_request_url

    assert _model_request_url(
        "openai_compat",
        "https://api.deepseek.com/v1/",
    ) == "https://api.deepseek.com/v1/chat/completions"


@pytest.mark.asyncio
async def test_factory_probe_and_run_share_the_same_runtime_model_request_url(tmp_path, monkeypatch):
    import aiosqlite
    import httpx

    from app.llm.factory import create_llm_client

    db_path = tmp_path / "codetalk.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE llm_configs (
                id TEXT PRIMARY KEY, api_type TEXT NOT NULL, base_url TEXT NOT NULL,
                api_key TEXT NOT NULL, model TEXT NOT NULL, config_json TEXT
            )
            """
        )
        await db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        await db.execute(
            "INSERT INTO llm_configs VALUES (?, ?, ?, ?, ?, ?)",
            ("approved", "openai_compat", "https://models.corp.example", "secret", "model", None),
        )
        await db.commit()

    expected_url = "https://models.corp.example/v1/chat/completions"
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr("app.llm.factory.settings.sqlite_db", db_path)
    monkeypatch.setattr(
        "app.llm.factory.require_runtime_model_request_url",
        lambda url: calls.append(("create", url)),
    )
    monkeypatch.setattr(
        "app.llm.openai_compat.require_runtime_model_request_url",
        lambda url: calls.append(("run", url)),
    )

    client = await create_llm_client("approved")
    await client._client.aclose()
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await client.health_check()
        with pytest.raises(RuntimeError, match="模型服务拒绝访问") as exc_info:
            await client.complete_once([{"role": "user", "content": "hello"}])
    finally:
        await client.close()

    assert calls == [
        ("create", expected_url),
        ("run", expected_url),
        ("run", expected_url),
    ]
    assert "http_403" in str(exc_info.value)
