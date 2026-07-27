import httpx
import pytest

from app.llm.openai_compat import OpenAICompatClient

pytestmark = pytest.mark.asyncio


async def test_complete_does_not_promote_reasoning_content_to_user_answer():
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
        with pytest.raises(ValueError, match="empty or too-short response"):
            await client.complete([{"role": "user", "content": "hello"}], max_tokens=32)
    finally:
        await client.close()


async def test_stream_complete_never_mixes_reasoning_content_into_user_answer():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=(
                'data: {"choices":[{"delta":{"reasoning_content":"我们被要求先分析任务。"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"# iSCSI Login 报告"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"\\n正文。"},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    client = OpenAICompatClient("https://example.test", "test-key", "deepseek-reasoner")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        chunks = [
            chunk
            async for chunk in client.stream_complete(
                [{"role": "user", "content": "analyze"}],
                max_tokens=32,
            )
        ]
    finally:
        await client.close()

    assert "".join(chunks) == "# iSCSI Login 报告\n正文。"


async def test_stream_complete_translates_403_without_exposing_endpoint_details():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request, text="gateway-secret")

    client = OpenAICompatClient("https://secret-gateway.example", "test-key", "deepseek-chat")
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


async def test_health_check_uses_only_the_configured_inference_route():
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        assert request.url.path == "/v1/chat/completions"
        payload = __import__("json").loads(request.content)
        assert payload["model"] == "deepseek-chat"
        assert payload["max_tokens"] <= 8
        return httpx.Response(
            200,
            json={
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "模型连接已经验证成功。",
                        },
                    }
                ],
            },
        )

    client = OpenAICompatClient(
        "https://api.deepseek.com", "test-key", "deepseek-chat"
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        success, message = await client.health_check()
    finally:
        await client.close()

    assert success is True
    assert message == "连接成功（已验证实际推理接口）"
    assert requests == [
        ("POST", "/v1/chat/completions"),
    ]


async def test_complete_accepts_a_base_url_that_already_includes_v1():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "模型连接已经验证成功。",
                        },
                    }
                ],
            },
        )

    client = OpenAICompatClient(
        "https://api.deepseek.com/v1/", "test-key", "deepseek-chat"
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        response = await client.complete(
            [{"role": "user", "content": "hello"}], max_tokens=8
        )
    finally:
        await client.close()

    assert response.content == "模型连接已经验证成功。"


@pytest.mark.parametrize("stream", [False, True])
async def test_deepseek_v4_flash_disables_thinking_for_bounded_output(stream: bool):
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        assert payload["thinking"] == {"type": "disabled"}
        if stream:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=(
                    'data: {"choices":[{"delta":{"content":"稳定且完整的结构化输出内容"},"finish_reason":"stop"}]}\n\n'
                    "data: [DONE]\n\n"
                ),
            )
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                    "choices": [{"finish_reason": "stop", "message": {"content": "稳定且完整的结构化输出内容"}}],
            },
        )

    client = OpenAICompatClient("https://api.deepseek.com", "test-key", "deepseek-v4-flash")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        if stream:
            result = "".join([item async for item in client.stream_complete([{"role": "user", "content": "hello"}])])
        else:
            result = (await client.complete([{"role": "user", "content": "hello"}])).content
    finally:
        await client.close()

    assert result == "稳定且完整的结构化输出内容"


async def test_deepseek_v4_pro_disables_thinking_for_bounded_output():
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        assert payload["thinking"] == {"type": "disabled"}
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-pro",
                "choices": [{"finish_reason": "stop", "message": {"content": "深度输出保持思考模式"}}],
            },
        )

    client = OpenAICompatClient("https://api.deepseek.com", "test-key", "deepseek-v4-pro")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.complete([{"role": "user", "content": "hello"}])
    finally:
        await client.close()

    assert result.content == "深度输出保持思考模式"


async def test_factory_managed_client_checks_each_model_request_before_transport(monkeypatch):
    transport_called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal transport_called
        transport_called = True
        return httpx.Response(200, json={})

    client = OpenAICompatClient(
        "https://api.deepseek.com",
        "test-key",
        "deepseek-chat",
        enforce_network_policy=True,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        "app.llm.openai_compat.require_runtime_model_request_url",
        lambda _url: (_ for _ in ()).throw(ValueError("model_endpoint_path_forbidden")),
    )

    try:
        with pytest.raises(ValueError, match="model_endpoint_path_forbidden"):
            await client.complete([{"role": "user", "content": "hello"}], max_tokens=32)
    finally:
        await client.close()

    assert transport_called is False
