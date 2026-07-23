"""Endpoint normalization shared by OpenAI-compatible adapters."""

from __future__ import annotations


def normalize_openai_compat_base_url(base_url: str) -> str:
    """Accept both provider-root and conventional ``.../v1`` configuration values.

    CodeTalk owns the versioned endpoint suffix. Keeping this normalization at the
    adapter boundary prevents the settings placeholder (which shows ``.../v1``)
    from producing ``/v1/v1/...`` requests.
    """

    normalized = str(base_url or "").strip().rstrip("/")
    if normalized.lower().endswith("/v1"):
        return normalized[:-3]
    return normalized
