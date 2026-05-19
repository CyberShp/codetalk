"""Factory for httpx.AsyncClient configured for localhost-bound tool services.

Local services (GitNexus, DeepWiki, Joern, CodeCompass) all run on
localhost.  They must not be routed through system proxies — trust_env=False
is enforced here and cannot be overridden by callers.

Usage:
    async with local_http_client(settings.gitnexus_base_url, timeout=30) as client:
        resp = await client.get("/api/repos")
"""

import httpx


def local_http_client(
    base_url: str,
    timeout: float = 30.0,
    connect_timeout: float = 5.0,
) -> httpx.AsyncClient:
    """Return an AsyncClient for a localhost tool service.

    trust_env=False is always set — local services must not go through a proxy.
    """
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout, connect=connect_timeout),
        trust_env=False,
    )
