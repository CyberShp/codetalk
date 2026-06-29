import httpx
import pytest

from app.llm.openai_compat import OpenAICompatClient

pytestmark = pytest.mark.asyncio


async def test_complete_falls_back_to_reasoning_content_when_content_is_empty():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "length",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "SPDK NVMe-oF connect 会先建立控制器连接，然后协商队列并提交 IO。",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 5,
                    "total_tokens": 8,
                },
            },
        )

    client = OpenAICompatClient("https://example.test", "test-key", "deepseek-v4-pro")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        response = await client.complete([{"role": "user", "content": "hello"}], max_tokens=32)
    finally:
        await client.close()

    assert "NVMe-oF connect" in response.content
    assert response.truncated is True
    assert response.usage["total_tokens"] == 8
