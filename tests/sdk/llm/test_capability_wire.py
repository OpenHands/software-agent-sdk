"""Exercise the real installed provider adapters against a local HTTP endpoint."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from openhands.sdk import LLM
from openhands.sdk.llm import ImageContent, Message, MessageToolCall, TextContent


IMAGE = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAA"
    "C0lEQVR42mP8/x8AAusB9Wl6XioAAAAASUVORK5CYII="
)


@pytest.fixture
def endpoint():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            response = {
                "id": "wire-test",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
            if self.path.endswith("/messages"):
                response = {
                    "id": "wire-test",
                    "type": "message",
                    "role": "assistant",
                    "model": body["model"],
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            payload = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def messages(role):
    content = [TextContent(text="Read this image."), ImageContent(image_urls=[IMAGE])]
    result = [
        Message(role="system", content=[TextContent(text="You are a test assistant.")]),
        Message(role="user", content=[TextContent(text="Inspect the screenshot.")]),
    ]
    if role == "user":
        result[-1] = Message(role="user", content=content)
    else:
        result += [
            Message(
                role="assistant",
                tool_calls=[
                    MessageToolCall(
                        id="call_image",
                        name="screenshot",
                        arguments="{}",
                        origin="completion",
                    )
                ],
            ),
            Message(
                role="tool",
                content=content,
                tool_call_id="call_image",
                name="screenshot",
            ),
        ]
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("role", ["user", "tool"])
@pytest.mark.parametrize(
    "model",
    [
        "openai/deepseek-v4.1-flash",
        "litellm_proxy/deepseek-v4.1-flash",
        "openai/minimax-m3",
        "openai/qwen3.6-plus",
        "openai/kimi-k3",
    ],
)
async def test_image_reaches_http_boundary(endpoint, use_async, role, model):
    base, requests = endpoint
    llm = LLM(
        model=model,
        base_url=base,
        api_key="local-test",
        num_retries=0,
        reasoning_effort=None,
        capability_overrides={"supports_vision": True},
    )
    if use_async:
        await llm.acompletion(messages(role))
    else:
        llm.completion(messages(role))
    assert len(requests) == 1
    parts = requests[0]["messages"][-1]["content"]
    assert isinstance(parts, list)
    assert any(
        p.get("type") == "image_url" and p["image_url"]["url"] == IMAGE for p in parts
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("role", ["user", "tool"])
async def test_native_deepseek_adapter_must_deliver_or_reject_images(
    endpoint, use_async, role
):
    base, requests = endpoint
    llm = LLM(
        model="deepseek/deepseek-v4.1-flash",
        base_url=base,
        api_key="local-test",
        num_retries=0,
        reasoning_effort=None,
    )
    try:
        if use_async:
            await llm.acompletion(messages(role))
        else:
            llm.completion(messages(role))
    except Exception as error:
        assert "discarded image content" in str(error)
        assert not requests
    else:
        assert len(requests) == 1
        assert any(
            p.get("type") == "image_url" for p in requests[0]["messages"][-1]["content"]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("use_async", [False, True])
async def test_canonical_identity_does_not_rewrite_transport(endpoint, use_async):
    base, requests = endpoint
    llm = LLM(
        model="openai/custom-deployment",
        model_canonical_name="deepseek/deepseek-v4.1-flash",
        base_url=base,
        api_key="local-test",
        num_retries=0,
        reasoning_effort=None,
    )
    if use_async:
        await llm.acompletion(messages("user"))
    else:
        llm.completion(messages("user"))
    assert requests[0]["model"] == "custom-deployment"
    assert any(
        p.get("type") == "image_url" for p in requests[0]["messages"][-1]["content"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("api_mode", ["chat", "responses"])
async def test_disabled_vision_rejects_request_but_not_token_count(
    endpoint, use_async, api_mode
):
    base, requests = endpoint
    llm = LLM(
        model="openai/gpt-4o",
        base_url=base,
        api_key="local-test",
        num_retries=0,
        disable_vision=True,
        api_mode=api_mode,
    )
    inputs = messages("user")
    assert llm.get_token_count(inputs) > 0
    with pytest.raises(Exception, match="Image-bearing request cannot be sent"):
        if use_async:
            await llm.agenerate(inputs)
        else:
            llm.generate(inputs)
    assert not requests


@pytest.mark.asyncio
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "model", ["anthropic/claude-opus-5", "anthropic/claude-sonnet-5"]
)
async def test_anthropic_default_output_budget_is_bounded(endpoint, use_async, model):
    base, requests = endpoint
    llm = LLM(
        model=model,
        base_url=base,
        api_key="local-test",
        num_retries=0,
        reasoning_effort=None,
    )
    inputs = messages("user")[:1] + [
        Message(role="user", content=[TextContent(text="Say ok.")])
    ]
    if use_async:
        await llm.acompletion(inputs)
    else:
        llm.completion(inputs)
    assert len(requests) == 1
    assert 0 < requests[0]["max_tokens"] <= 16384
