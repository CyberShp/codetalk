from app.llm.factory import _resolve_proxy


def test_direct_mode_uses_only_general_settings_pem():
    assert _resolve_proxy({
        "proxy_mode": "none",
        "proxy_url": "http://ignored.example:8080",
        "ssl_cert_path": "C:/certs/model.pem",
    }) == (None, "C:/certs/model.pem", True)


def test_direct_mode_without_pem_uses_default_ca_store():
    assert _resolve_proxy({
        "proxy_mode": "none",
        "proxy_url": "",
        "ssl_cert_path": "",
    }) == (None, None, True)


def test_custom_proxy_and_pem_come_from_general_settings():
    assert _resolve_proxy({
        "proxy_mode": "custom",
        "proxy_url": "http://127.0.0.1:7890",
        "ssl_cert_path": "/certs/provider.pem",
    }) == ("http://127.0.0.1:7890", "/certs/provider.pem", False)
