import json
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM, FallbackStrategy, Message, TextContent
from openhands.sdk.llm.exceptions import LLMRateLimitError, is_quota_exhaustion_error
from openhands.sdk.llm.llm_profile_store import LLMProfileStore


@pytest.fixture
def provider():
    replies = deque()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            status, body = replies.popleft()
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", replies, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
@pytest.mark.parametrize("api_mode", ["chat", "responses"])
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "status,budget_denied",
    [
        (code, False)
        for code in (408, 409, 429, 502, 520, 522, 524, 599, 402, 403, 405, 406, 410)
    ]
    + [(429, True)],
)
async def test_http_rate_limit_retry_and_budget_recovery(
    provider, api_mode, use_async, status, budget_denied
):
    base_url, replies, requests = provider
    replies.append(
        (
            status,
            {
                "error": {
                    "type": "budget_exceeded" if budget_denied else "provider_error",
                    "message": "Budget has been exceeded! Team=t"
                    if budget_denied
                    else "Provider request failed",
                    "code": str(status),
                }
            },
        )
    )
    if api_mode == "chat":
        success = {
            "id": "chat-1",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "resumed"},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    else:
        success = {
            "id": "resp-1",
            "object": "response",
            "created_at": 1,
            "model": "gpt-4o",
            "status": "completed",
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "output": [
                {
                    "type": "message",
                    "id": "msg-1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "resumed", "annotations": []}
                    ],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
    replies.append((200, success))
    llm = LLM(
        model="openai/gpt-4o",
        base_url=base_url,
        api_key=SecretStr("test-key"),
        usage_id="budget-http",
        api_mode=api_mode,
        num_retries=3,
        retry_min_wait=0,
        retry_max_wait=0,
        retry_multiplier=0,
    )
    messages = [
        Message(
            role="system", content=[TextContent(text="You are a helpful assistant.")]
        ),
        Message(role="user", content=[TextContent(text="continue")]),
    ]

    async def call():
        return await llm.agenerate(messages) if use_async else llm.generate(messages)

    if budget_denied:
        with pytest.raises(LLMRateLimitError, match="Budget has been exceeded"):
            await call()
        assert len(requests) == 1

    if status in (402, 403, 405, 406, 410):
        with pytest.raises(Exception, match="Provider request failed"):
            await call()
        assert len(requests) == 1
        return

    response = await call()
    assert response.message.content == [TextContent(text="resumed")]
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("api_mode", ["chat", "responses"])
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "code_field,error_code,exhausted",
    [
        ("code", "insufficient_quota", True),
        ("type", "insufficient_quota", True),
        ("type", "usage_limit_reached", True),
        ("type", "budget_exceeded", True),
        ("code", "rate_limit_exceeded", False),
    ],
)
async def test_http_structured_quota_skips_retries(
    provider, api_mode, use_async, code_field, error_code, exhausted
):
    base_url, replies, requests = provider
    error = {
        "message": "You exceeded your current quota, please check your plan.",
        "type": "provider_error",
        "code": "429",
    }
    error[code_field] = error_code
    replies.extend([(429, {"error": error})] * 3)
    llm = LLM(
        model="openai/gpt-4o",
        base_url=base_url,
        api_key=SecretStr("test-key"),
        api_mode=api_mode,
        num_retries=3,
        retry_min_wait=0,
        retry_max_wait=0,
        retry_multiplier=0,
    )
    messages = [
        Message(role="system", content=[TextContent(text="Be concise.")]),
        Message(role="user", content=[TextContent(text="ping")]),
    ]

    with pytest.raises(LLMRateLimitError) as caught:
        if use_async:
            await llm.agenerate(messages)
        else:
            llm.generate(messages)

    assert len(requests) == (1 if exhausted else llm.num_retries)
    # Profile pre-flight classifies the SDK exception after transport mapping.
    assert is_quota_exhaustion_error(caught.value) is exhausted


@pytest.mark.parametrize(
    "error_code,fallback_rate_limited",
    [
        ("insufficient_quota", False),
        ("insufficient_quota", True),
        ("budget_exceeded", False),
    ],
)
def test_http_structured_quota_fallback(
    provider, tmp_path, monkeypatch, error_code, fallback_rate_limited
):
    base_url, replies, requests = provider
    # Profiles omit API keys without a cipher; the fallback resolves this fake key.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    replies.append(
        (429, {"error": {"message": "Allowance exhausted", "type": error_code}})
    )
    if fallback_rate_limited:
        replies.append(
            (429, {"error": {"message": "Try again", "type": "rate_limit_exceeded"}})
        )
    replies.extend(
        [
            (
                200,
                {
                    "id": "chat-fallback",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "fallback"},
                        }
                    ],
                },
            ),
        ]
    )
    LLMProfileStore(tmp_path).save(
        "backup",
        LLM(
            model="openai/gpt-4o-mini",
            api_key=SecretStr("test-key"),
            base_url=base_url,
            num_retries=3,
            retry_min_wait=0,
            retry_max_wait=0,
            retry_multiplier=0,
        ),
    )
    llm = LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("test-key"),
        base_url=base_url,
        num_retries=3,
        retry_min_wait=0,
        retry_max_wait=0,
        retry_multiplier=0,
        fallback_strategy=FallbackStrategy(
            fallback_llms=["backup"], profile_store_dir=tmp_path
        ),
    )
    messages = [
        Message(role="system", content=[TextContent(text="Be concise.")]),
        Message(role="user", content=[TextContent(text="ping")]),
    ]
    if error_code == "budget_exceeded":
        with pytest.raises(LLMRateLimitError):
            llm.generate(messages)
        assert [request["model"] for request in requests] == ["gpt-4o"]
    else:
        assert llm.generate(messages).message.content == [TextContent(text="fallback")]
        assert [request["model"] for request in requests] == ["gpt-4o"] + [
            "gpt-4o-mini"
        ] * (2 if fallback_rate_limited else 1)
