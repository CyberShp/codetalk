"""Factory for HTTP clients used by local tool services."""

import httpx


def local_http_client(
    base_url: str,
    timeout: float = 30.0,
    connect_timeout: float = 5.0,
) -> httpx.AsyncClient:
    """Return an AsyncClient for the configured tool service."""
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout, connect=connect_timeout),
        trust_env=False,
    )
