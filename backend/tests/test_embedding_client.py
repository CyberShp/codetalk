"""Contract tests for EmbeddingClient (Layer 1 — HTTP adapter, no DB)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.llm.embedding_client import EmbeddingClient, _BATCH_SIZE


def _make_embedding_response(texts: list[str], dim: int = 3) -> dict:
    """Build a fake /v1/embeddings response with deterministic vectors."""
    return {
        "data": [
            {"index": i, "embedding": [float(i + 1)] * dim}
            for i in range(len(texts))
        ],
        "model": "test-model",
        "usage": {"prompt_tokens": 10, "total_tokens": 10},
    }


def _mock_response(json_data: dict, status: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "http://test/v1/embeddings")
    return httpx.Response(status_code=status, json=json_data, request=request)


# ---------------------------------------------------------------------------
# Client construction branches
# ---------------------------------------------------------------------------


class TestClientConstruction:
    def test_default_client(self):
        client = EmbeddingClient(
            base_url="http://localhost:8080",
            api_key="test-key",
            model="test-model",
        )
        assert client._base_url == "http://localhost:8080"
        assert client._model == "test-model"

    def test_trailing_slash_stripped(self):
        client = EmbeddingClient(
            base_url="http://localhost:8080/",
            api_key="key",
            model="m",
        )
        assert client._base_url == "http://localhost:8080"

    def test_force_direct_uses_transport(self):
        client = EmbeddingClient(
            base_url="http://localhost:8080",
            api_key="key",
            model="m",
            force_direct=True,
        )
        assert client._client is not None

    def test_proxy_param(self):
        client = EmbeddingClient(
            base_url="http://localhost:8080",
            api_key="key",
            model="m",
            proxy_url="http://proxy:3128",
        )
        assert client._client is not None

    def test_ssl_cert_path(self):
        with patch("app.llm.embedding_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = AsyncMock()
            client = EmbeddingClient(
                base_url="http://localhost:8080",
                api_key="key",
                model="m",
                ssl_cert_path="/path/to/cert.pem",
            )
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["verify"] == "/path/to/cert.pem"


# ---------------------------------------------------------------------------
# embed_batch
# ---------------------------------------------------------------------------


class TestEmbedBatch:
    @pytest.mark.asyncio
    async def test_empty_input(self):
        client = EmbeddingClient(
            base_url="http://test", api_key="k", model="m"
        )
        result = await client.embed_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_single_text(self):
        client = EmbeddingClient(
            base_url="http://test", api_key="k", model="m"
        )
        resp_data = _make_embedding_response(["hello"])
        mock_resp = _mock_response(resp_data)

        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=mock_resp)

        result = await client.embed_batch(["hello"])
        assert len(result) == 1
        assert result[0] == [1.0, 1.0, 1.0]

    @pytest.mark.asyncio
    async def test_batch_within_limit(self):
        texts = [f"text_{i}" for i in range(10)]
        client = EmbeddingClient(
            base_url="http://test", api_key="k", model="m"
        )
        resp_data = _make_embedding_response(texts)
        mock_resp = _mock_response(resp_data)

        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=mock_resp)

        result = await client.embed_batch(texts)
        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_auto_batch_splitting(self):
        n = _BATCH_SIZE + 10
        texts = [f"text_{i}" for i in range(n)]
        client = EmbeddingClient(
            base_url="http://test", api_key="k", model="m"
        )

        call_count = 0

        async def _fake_do_embed(batch):
            nonlocal call_count
            call_count += 1
            return [[float(call_count)] * 3] * len(batch)

        client._do_embed = _fake_do_embed

        result = await client.embed_batch(texts)
        assert len(result) == n
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_response_sorted_by_index(self):
        client = EmbeddingClient(
            base_url="http://test", api_key="k", model="m"
        )
        reversed_data = {
            "data": [
                {"index": 2, "embedding": [3.0]},
                {"index": 0, "embedding": [1.0]},
                {"index": 1, "embedding": [2.0]},
            ],
            "model": "m",
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        }
        mock_resp = _mock_response(reversed_data)
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=mock_resp)

        result = await client.embed_batch(["a", "b", "c"])
        assert result == [[1.0], [2.0], [3.0]]


# ---------------------------------------------------------------------------
# Authorization header
# ---------------------------------------------------------------------------


class TestAuthHeader:
    @pytest.mark.asyncio
    async def test_api_key_sent_as_bearer(self):
        client = EmbeddingClient(
            base_url="http://test", api_key="sk-test123", model="m"
        )
        mock_resp = _mock_response(_make_embedding_response(["x"]))
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=mock_resp)

        await client.embed_batch(["x"])
        call_args = client._client.post.call_args
        headers = call_args.kwargs.get("headers") or call_args[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer sk-test123"

    @pytest.mark.asyncio
    async def test_empty_api_key_omits_auth(self):
        client = EmbeddingClient(
            base_url="http://test", api_key="", model="m"
        )
        mock_resp = _mock_response(_make_embedding_response(["x"]))
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=mock_resp)

        await client.embed_batch(["x"])
        call_args = client._client.post.call_args
        headers = call_args.kwargs.get("headers") or call_args[1].get("headers", {})
        assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_http_4xx_raises(self):
        client = EmbeddingClient(
            base_url="http://test", api_key="k", model="m"
        )
        err_resp = httpx.Response(
            status_code=401,
            json={"error": "Unauthorized"},
            request=httpx.Request("POST", "http://test/v1/embeddings"),
        )
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=err_resp)

        with pytest.raises(httpx.HTTPStatusError):
            await client.embed_batch(["test"])

    @pytest.mark.asyncio
    async def test_http_5xx_retried_then_succeeds(self):
        client = EmbeddingClient(
            base_url="http://test", api_key="k", model="m"
        )
        err_resp = httpx.Response(
            status_code=500,
            json={"error": "Internal"},
            request=httpx.Request("POST", "http://test/v1/embeddings"),
        )

        call_count = 0

        async def _fake_do_embed(texts):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise httpx.HTTPStatusError(
                    "500",
                    request=httpx.Request("POST", "http://test"),
                    response=err_resp,
                )
            return [[1.0]] * len(texts)

        client._do_embed = _fake_do_embed

        result = await client.embed_batch(["test"])
        assert len(result) == 1
        assert call_count == 3
