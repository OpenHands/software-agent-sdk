"""Check SDK -> LiteLLM -> Anthropic HTTP payloads using loopback only.

Run after `make build`: .venv/bin/python .pr/repro_prompt_cache.py
The companion repro_chat_content module clears credentials and blocks all
non-loopback connections before importing the SDK.
"""

import json
from http.server import ThreadingHTTPServer
from threading import Thread
from typing import Any

from repro_chat_content import (
    LLM,
    Message,
    MessageToolCall,
    StrictChatServer,
    TextContent,
)


received: list[dict[str, Any]] = []


class AnthropicServer(StrictChatServer):
    def do_POST(self) -> None:
        assert self.path == "/v1/messages", self.path
        received.append(
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        )
        payload = {
            "id": "msg_local_cache",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "local-ok"}],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    failures = 0
    with ThreadingHTTPServer(("127.0.0.1", 0), AnthropicServer) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            llm = LLM(
                model="anthropic/claude-sonnet-4-20250514",
                api_key="local-test-only-not-a-real-key",
                base_url=f"http://127.0.0.1:{server.server_port}",
                caching_prompt=True,
                num_retries=0,
                timeout=5,
                max_output_tokens=16,
                log_completions=False,
            )
            for role, text in [
                ("tool", "Tool response"),
                ("tool", None),
                ("user", "Question"),
            ]:
                for blank in ["", " \t\n"]:
                    content = [] if text is None else [TextContent(text=text)]
                    content.append(TextContent(text=blank))
                    if role == "tool":
                        messages = [
                            Message(
                                role="user", content=[TextContent(text="Run the tool")]
                            ),
                            Message(
                                role="assistant",
                                content=[],
                                tool_calls=[
                                    MessageToolCall(
                                        id="call_cache",
                                        name="test_tool",
                                        arguments="{}",
                                        origin="completion",
                                    )
                                ],
                            ),
                            Message(
                                role="tool",
                                content=content,
                                tool_call_id="call_cache",
                                name="test_tool",
                            ),
                        ]
                    else:
                        messages = [Message(role="user", content=content)]
                    stored = [message.model_dump_json() for message in messages]
                    before = len(received)
                    response = llm.completion(messages)
                    assert len(received) == before + 1
                    assert isinstance(response.message.content[0], TextContent)
                    assert response.message.content[0].text == "local-ok"
                    assert [m.model_dump_json() for m in messages] == stored
                    block = received[-1]["messages"][-1]["content"][-1]
                    cached = block.get("cache_control") == {"type": "ephemeral"}
                    failures += not cached
                    print(
                        f"{'PASS' if cached else 'FAIL'} {role}, text={text!r}, "
                        f"blank={blank!r}: HTTP 200; "
                        f"cache_control={block.get('cache_control')!r}"
                    )
        finally:
            server.shutdown()
            worker.join(timeout=5)
    print(f"{6 - failures}/6 cache markers retained; loopback HTTP only")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
