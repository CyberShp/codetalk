import httpx
import pytest

from app.llm.anthropic import AnthropicClient


pytestmark = pytest.mark.asyncio


async def test_stream_complete_translates_403_without_exposing_endpoint_details():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request, text="gateway-secret")

    client = AnthropicClient("https://secret-gateway.example", "test-key", "claude-test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        with pytest.raises(RuntimeError, match="模型服务拒绝访问") as exc_info:
            async for _ in client.stream_complete([{"role": "user", "content": "hello"}]):
                pass
    finally:
        await client.close()

    assert "secret-gateway.example" not in str(exc_info.value)
    assert "gateway-secret" not in str(exc_info.value)
