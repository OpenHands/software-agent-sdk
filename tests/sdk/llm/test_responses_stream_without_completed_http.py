"""A Responses stream that ends without response.completed is a no-response.

These tests drive the real LiteLLM streaming path against a local HTTP server,
since LiteLLM decides how such a stream fails (#6657).
"""

import asyncio
import json
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMNoResponseError


MESSAGES = [Message(role="user", content=[TextContent(text="hi")])]


def _events(text: str, *, completed: bool) -> list[dict[str, Any]]:
    item = {
        "type": "message",
        "id": "msg_1",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    final = {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "gpt-4o-mini",
        "output": [item] if text else [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
    }
    events: list[dict[str, Any]] = [
        {
            "type": "response.created",
            "response": {**final, "status": "in_progress", "output": []},
        }
    ]
    if text:
        events += [
            {"type": "response.output_item.added", "output_index": 0, "item": item},
            {
                "type": "response.output_text.delta",
                "item_id": "msg_1",
                "output_index": 0,
                "content_index": 0,
                "delta": text,
            },
            {"type": "response.output_item.done", "output_index": 0, "item": item},
        ]
    if completed:
        events.append({"type": "response.completed", "response": final})
    return events


@pytest.fixture
def provider():
    replies: deque[list[dict[str, Any]]] = deque()
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            events = replies.popleft()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for event in events:
                data = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                self.wfile.write(data.encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True

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


def _llm(base_url: str, num_retries: int) -> LLM:
    return LLM(
        model="openai/gpt-4o-mini",
        base_url=base_url,
        api_key=SecretStr("test-key"),
        api_mode="responses",
        stream=True,
        temperature=0.0,
        num_retries=num_retries,
        retry_min_wait=0,
        retry_max_wait=0,
        usage_id="test",
    )


async def _noop(_chunk: object) -> None:
    pass


@pytest.mark.parametrize("text", ["lost", ""], ids=["after-output", "no-output"])
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
def test_retried_as_no_response_with_temperature_bump(provider, text, use_async):
    base_url, replies, requests = provider
    replies.extend([_events(text, completed=False), _events("hi", completed=True)])
    llm = _llm(base_url, num_retries=2)

    if use_async:
        response = asyncio.run(llm.aresponses(MESSAGES, on_token=_noop))
    else:
        response = llm.responses(MESSAGES, on_token=lambda _chunk: None)

    assert response.message.content[0].text == "hi"  # type: ignore[union-attr]
    assert [r.get("temperature") for r in requests] == [0.0, 1.0]


@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
def test_exhausted_retries_raise_no_response(provider, use_async):
    base_url, replies, requests = provider
    replies.extend([_events("lost", completed=False)] * 2)
    llm = _llm(base_url, num_retries=2)

    with pytest.raises(LLMNoResponseError):
        if use_async:
            asyncio.run(llm.aresponses(MESSAGES, on_token=_noop))
        else:
            llm.responses(MESSAGES, on_token=lambda _chunk: None)
    assert len(requests) == 2
