"""Exercise real SDK/LiteLLM HTTP and SSE parsing without provider credentials.

Run from the repository root: uv run python .pr/local_transport_smoke.py
"""

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from openhands.sdk.llm import LLM, Message, TextContent


TEXT = "Hello local stream"
ITEM = {
    "id": "msg_local",
    "type": "message",
    "role": "assistant",
    "status": "completed",
    "content": [{"type": "output_text", "text": TEXT, "annotations": []}],
}
RESPONSE = {
    "id": "resp_local",
    "object": "response",
    "created_at": 1,
    "model": "gpt-4o",
    "status": "completed",
    "output": [ITEM],
    "parallel_tool_calls": False,
    "tool_choice": "auto",
    "tools": [],
    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        streaming = request.get("stream", False)
        self.send_response(200)
        self.send_header(
            "Content-Type", "text/event-stream" if streaming else "application/json"
        )
        self.end_headers()
        if self.path == "/v1/responses":
            if not streaming:
                self.wfile.write(json.dumps(RESPONSE).encode())
                return
            events = [
                {"type": "response.created", "response": RESPONSE},
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_local",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": TEXT,
                },
                {"type": "response.output_item.done", "output_index": 0, "item": ITEM},
                {
                    "type": "response.completed",
                    "response": {**RESPONSE, "output": []},
                },
            ]
            for sequence_number, event in enumerate(events):
                event["sequence_number"] = sequence_number
                payload = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                self.wfile.write(payload.encode())
        elif self.path == "/v1/chat/completions" and streaming:
            for delta, finish in [({"content": TEXT}, None), ({}, "stop")]:
                chunk = {
                    "id": "chat_local",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "gpt-4o",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            raise AssertionError(f"Unexpected request path: {self.path}")


async def run_calls(base_url):
    messages = [Message(role="user", content=[TextContent(text="Say hello")])]
    for api, streaming in [("responses", False), ("responses", True), ("chat", True)]:
        for asynchronous in [False, True]:
            llm = LLM(
                model="openai/gpt-4o",
                api_key="local-test-only",
                base_url=base_url,
                num_retries=0,
                timeout=10,
            )
            chunks = []
            kwargs = {
                "stream": streaming,
                "on_token": chunks.append if streaming else None,
            }
            if api == "responses":
                result = (
                    await llm.aresponses(messages, **kwargs)
                    if asynchronous
                    else llm.responses(messages, **kwargs)
                )
            else:
                result = (
                    await llm.acompletion(messages, **kwargs)
                    if asynchronous
                    else llm.completion(messages, **kwargs)
                )
            assert result.message.content == [TextContent(text=TEXT)]
            if streaming:
                text = "".join(chunk.choices[0].delta.content or "" for chunk in chunks)
                assert text == TEXT
                if api == "responses":
                    assert {chunk.id for chunk in chunks} == {"msg_local"}
            print(f"PASS: {api}, async={asynchronous}, streaming={streaming}")


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        asyncio.run(run_calls(f"http://127.0.0.1:{server.server_port}/v1"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


if __name__ == "__main__":
    main()
