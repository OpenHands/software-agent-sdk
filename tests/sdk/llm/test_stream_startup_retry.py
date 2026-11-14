import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from lmnr.opentelemetry_lib.opentelemetry.instrumentation.litellm import (
    LitellmInstrumentor,
)
from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
)


@asynccontextmanager
async def _provider_server(
    statuses: list[int], error_code: str = "provider_error"
) -> AsyncIterator[tuple[str, list[int]]]:
    attempts: list[int] = []

    async def completion(request: web.Request) -> web.StreamResponse:
        payload = await request.json()
        assert payload["stream"] is True
        assert payload["messages"][0]["role"] == "system"
        status = statuses[min(len(attempts), len(statuses) - 1)]
        attempts.append(status)
        if status != 200:
            return web.json_response(
                {"error": {"message": "Provider error", "code": error_code}},
                status=status,
            )
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for delta, finish_reason in [({"content": "Recovered"}, None), ({}, "stop")]:
            chunk = {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": finish_reason}
                ],
            }
            await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    app = web.Application()
    app.router.add_post("/chat/completions", completion)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}", attempts
    finally:
        await runner.cleanup()


@pytest.fixture
def instrument_litellm():
    instrumentor = LitellmInstrumentor()
    instrumentor.instrument()
    try:
        yield
    finally:
        instrumentor.uninstrument()


def _llm(base_url: str) -> LLM:
    return LLM(
        model="openai/test-model",
        api_key=SecretStr("test-key"),
        base_url=base_url,
        usage_id="stream-startup-test",
        stream=True,
        num_retries=2,
        retry_min_wait=0,
        retry_max_wait=0,
        retry_multiplier=0,
    )


def _messages() -> list[Message]:
    return [
        Message(role="system", content=[TextContent(text="Answer briefly.")]),
        Message(role="user", content=[TextContent(text="Hello")]),
    ]


@pytest.mark.usefixtures("instrument_litellm")
@pytest.mark.asyncio
@pytest.mark.parametrize("statuses", [[200], [503, 200], [502, 200], [429, 200]])
async def test_stream_startup_retry_with_instrumentation_enabled(statuses):
    tokens = []
    async with _provider_server(statuses) as (base_url, attempts):
        result = await _llm(base_url).acompletion(
            messages=_messages(), on_token=tokens.append
        )
    assert result.message.content == [TextContent(text="Recovered")]
    assert (
        "".join(
            choice.delta.content or "" for token in tokens for choice in token.choices
        )
        == "Recovered"
    )
    assert attempts == statuses


@pytest.mark.usefixtures("instrument_litellm")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,error_code,error_type,expected_attempts",
    [
        (503, "provider_error", LLMServiceUnavailableError, 2),
        (400, "invalid_request", LLMBadRequestError, 1),
        (401, "invalid_api_key", LLMAuthenticationError, 1),
        (429, "insufficient_quota", LLMRateLimitError, 1),
    ],
)
async def test_stream_startup_retry_bounds(
    status, error_code, error_type, expected_attempts
):
    tokens = []
    async with _provider_server([status], error_code) as (base_url, attempts):
        with pytest.raises(error_type):
            await _llm(base_url).acompletion(
                messages=_messages(), on_token=tokens.append
            )
    assert attempts == [status] * expected_attempts
    assert tokens == []


@pytest.mark.usefixtures("instrument_litellm")
@pytest.mark.asyncio
async def test_stream_callback_error_is_not_retried():
    def on_token(_):
        raise RuntimeError("Callback failed")

    async with _provider_server([200]) as (base_url, attempts):
        with pytest.raises(RuntimeError, match="Callback failed"):
            await _llm(base_url).acompletion(messages=_messages(), on_token=on_token)
    assert attempts == [200]
