"""Factory for HTTP clients used by local or deployment-approved tool services.

Local services (GitNexus, Joern, CodeCompass) normally run on localhost.
Deployments may instead put one behind an approved internal endpoint.  Both
forms must pass the same runtime egress admission check before an HTTP client
is created; ``trust_env=False`` prevents a local request from being silently
rerouted through an inherited proxy.

Usage:
    async with local_http_client(settings.gitnexus_base_url, timeout=30) as client:
        resp = await client.get("/api/repos")
"""

import httpx

from app.services.runtime_environment import require_runtime_url


def local_http_client(
    base_url: str,
    timeout: float = 30.0,
    connect_timeout: float = 5.0,
) -> httpx.AsyncClient:
    """Return an AsyncClient for a deployment-approved tool service.

    The admission check is deliberately here rather than at individual call
    sites.  A custom GitNexus/CGC URL otherwise becomes an unaudited egress
    path before the first request is issued.
    """
    require_runtime_url(base_url)
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout, connect=connect_timeout),
        trust_env=False,
    )
