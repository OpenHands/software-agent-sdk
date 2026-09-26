import json
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMRateLimitError


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
@pytest.mark.parametrize("budget_denied", [False, True], ids=["transient", "budget"])
async def test_http_rate_limit_retry_and_budget_recovery(
    provider, api_mode, use_async, budget_denied
):
    base_url, replies, requests = provider
    replies.append(
        (
            429,
            {
                "error": {
                    "type": "budget_exceeded" if budget_denied else "rate_limit_error",
                    "message": "Budget has been exceeded! Team=t"
                    if budget_denied
                    else "Too many requests",
                    "code": "429",
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
    messages = [Message(role="user", content=[TextContent(text="continue")])]

    async def call():
        return await llm.agenerate(messages) if use_async else llm.generate(messages)

    if budget_denied:
        with pytest.raises(LLMRateLimitError, match="Budget has been exceeded"):
            await call()
        assert len(requests) == 1

    response = await call()
    assert response.message.content == [TextContent(text="resumed")]
    assert len(requests) == 2
