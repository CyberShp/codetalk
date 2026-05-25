"""Unit tests for app/adapters/__init__.py registry functions and base class."""

import pytest

from app.adapters import (
    create_adapter,
    get_adapter,
    get_all_adapters,
)
from app.adapters.base import AnalysisRequest, BaseToolAdapter
from app.utils.local_client import local_http_client


class TestAdapterRegistry:
    def test_get_all_adapters_returns_registered(self):
        """Line 36: get_all_adapters returns list of all registered singletons."""
        adapters = get_all_adapters()
        assert isinstance(adapters, list)
        names = {a.name() for a in adapters}
        assert "gitnexus" in names
        assert "deepwiki" in names

    def test_get_adapter_unknown_raises_key_error(self):
        """Lines 23-25: get_adapter raises KeyError for unknown name."""
        with pytest.raises(KeyError, match="Unknown tool adapter"):
            get_adapter("nonexistent-adapter-xyz")

    def test_create_adapter_unknown_raises_key_error(self):
        """Lines 30-32: create_adapter raises KeyError for unknown name."""
        with pytest.raises(KeyError, match="Unknown tool adapter"):
            create_adapter("nonexistent-adapter-xyz")

    def test_create_adapter_known_returns_instance(self):
        """Line 32: create_adapter returns a fresh instance from the factory."""
        adapter = create_adapter("deepwiki")
        assert adapter is not None
        assert adapter.name() == "deepwiki"


class TestBaseAdapterCleanup:
    async def test_cleanup_is_no_op(self):
        """base.py line 81: default cleanup() is a no-op (does not raise).
        DeepwikiAdapter does not override cleanup(), so this hits the base class pass."""
        adapter = get_adapter("deepwiki")
        req = AnalysisRequest(repo_local_path="/tmp/repo")
        await adapter.cleanup(req)


class TestLocalHttpClient:
    def test_returns_async_client(self):
        """local_client.py line 24: local_http_client returns an httpx.AsyncClient."""
        import httpx

        client = local_http_client("http://localhost:7100")
        assert isinstance(client, httpx.AsyncClient)
        assert not client.is_closed
